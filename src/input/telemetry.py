"""Agent-Input: High-performance, non-blocking OS keystroke telemetry.

Captures global input events via pynput, computes real-time rolling WPM,
and dispatches strongly typed KeyEvent dataclasses into a thread-safe queue.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
import queue
import threading
import time
from typing import Optional, Set

from pynput import keyboard


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """Telemetry event generated on OS key press."""
    timestamp: float                # Monotonic timestamp (time.monotonic())
    key_type: str                   # "regular", "modulation", "backspace", "enter"
    raw_char: Optional[str]         # String representation of character or key
    wpm: float                      # Current rolling WPM


# Pre-allocated set of modulation characters for O(1) membership check
MODULATION_CHARS: Set[str] = {
    '(', ')',
    '[', ']',
    '{', '}',
    "'", '"',
    '\t'
}


def clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamps a floating point value to the interval [min_val, max_val]."""
    if val < min_val:
        return min_val
    if val > max_val:
        return max_val
    return val


class InputTelemetry:
    """Non-blocking global keystroke telemetry engine."""

    def __init__(
        self,
        event_queue: Optional[queue.Queue[KeyEvent]] = None,
        max_queue_size: int = 256,
        window_size: int = 30,
        idle_reset_seconds: float = 3.0
    ) -> None:
        """
        Initialize telemetry collector.

        :param event_queue: Thread-safe queue for downstream consumers.
        :param max_queue_size: Capacity of event queue before dropping oldest.
        :param window_size: Size of sliding window deque for WPM calculation.
        :param idle_reset_seconds: Threshold to reset sliding window if user goes idle.
        """
        self.event_queue: queue.Queue[KeyEvent] = (
            event_queue if event_queue is not None else queue.Queue(maxsize=max_queue_size)
        )
        self._max_queue_size = max_queue_size
        self._timestamps: collections.deque[float] = collections.deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._idle_reset_seconds = idle_reset_seconds

        self._listener: Optional[keyboard.Listener] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return True if background listener thread is active."""
        return self._is_running

    def calculate_wpm(self, current_time: Optional[float] = None) -> float:
        """
        Calculate rolling WPM using the sliding window deque.
        Formula: (keystrokes / 5) / (dt_seconds / 60)
        Protected against division by zero and clamped to [0.0, 200.0].
        """
        now = time.monotonic() if current_time is None else current_time

        with self._lock:
            n_events = len(self._timestamps)
            if n_events < 2:
                return 0.0

            # Reset window if typing paused longer than idle threshold
            time_since_last = now - self._timestamps[-1]
            if time_since_last > self._idle_reset_seconds:
                self._timestamps.clear()
                return 0.0

            dt = self._timestamps[-1] - self._timestamps[0]
            if dt <= 1e-4:
                return 0.0

            # 1 standard word = 5 keystrokes
            words = (n_events - 1) / 5.0
            minutes = dt / 60.0
            raw_wpm = words / minutes

            return clamp(raw_wpm, 0.0, 200.0)

    def classify_key(self, key: keyboard.Key | keyboard.KeyCode) -> tuple[Optional[str], Optional[str]]:
        """
        Classifies key into (key_type, raw_char).
        Returns (None, None) if key is an unmapped modifier/control key.
        """
        # 1. Enter
        if key == keyboard.Key.enter:
            return "enter", "\n"

        # 2. Backspace
        if key == keyboard.Key.backspace:
            return "backspace", "\b"

        # 3. Tab (Modulation)
        if key == keyboard.Key.tab:
            return "modulation", "\t"

        # 4. Space (Regular)
        if key == keyboard.Key.space:
            return "regular", " "

        # 5. KeyCode with character
        if isinstance(key, keyboard.KeyCode):
            char = key.char
            if char is not None:
                if char in MODULATION_CHARS:
                    return "modulation", char
                # Printable regular keys
                if char.isprintable():
                    return "regular", char

        # 6. Unhandled special keys (Shift, Ctrl, Alt, CapsLock, Fn, etc.)
        return None, None

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        """Low-latency key press handler executed in pynput daemon thread."""
        now = time.monotonic()
        key_type, raw_char = self.classify_key(key)

        if key_type is None:
            return

        with self._lock:
            self._timestamps.append(now)
            # Calculate instantaneous rolling WPM
            n_events = len(self._timestamps)
            if n_events >= 2:
                dt = self._timestamps[-1] - self._timestamps[0]
                if dt > 1e-4:
                    words = (n_events - 1) / 5.0
                    minutes = dt / 60.0
                    current_wpm = clamp(words / minutes, 0.0, 200.0)
                else:
                    current_wpm = 0.0
            else:
                current_wpm = 0.0

        event = KeyEvent(
            timestamp=now,
            key_type=key_type,
            raw_char=raw_char,
            wpm=current_wpm
        )

        self._enqueue_event(event)

    def _enqueue_event(self, event: KeyEvent) -> None:
        """Enqueues event without blocking. Drops oldest event if queue is full."""
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            try:
                # Evict oldest event to preserve low latency
                self.event_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                pass

    def start(self) -> None:
        """Starts the background pynput keyboard listener daemon thread."""
        if self._is_running:
            return

        self._is_running = True
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            daemon=True
        )
        self._listener.start()

    def stop(self) -> None:
        """Stops the keyboard listener and releases resources."""
        self._is_running = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

        with self._lock:
            self._timestamps.clear()
