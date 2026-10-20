"""Agent-Harmony: Real-time procedural music theory and rules engine.

Features:
- Scales: Dorian (Lo-Fi), Minor Pentatonic (Space), Lydian (Dreamy), Aeolian (Melancholy);
- Rate Limiting (Anti-clutter cooldown: 40-200 ms, default 80 ms);
- Rapid keystrokes dynamically brighten the LPF filter instead of spawning sound clutter;
- Lush Lo-Fi Chord Clusters (m9 / m11 voicings across circle of fifths);
- Space bar produces tactile vinyl crackle / percussive muted click;
- Enter bar drops deep sub-bass and resolves arpeggiator to root;
- Precomputed frequency lookup tables for zero-allocation runtime performance.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import queue
import threading
import time
from typing import Optional, Sequence

from src.input.telemetry import KeyEvent, clamp


@dataclass(frozen=True, slots=True)
class HarmonyEvent:
    """Harmonic audio trigger dispatched to Agent-DSP."""
    event_type: str                 # "note", "sub_drop", "lpf_update", "percussion"
    frequency: float                # Note frequency in Hz
    velocity: float                 # Normalized velocity [0.0 .. 1.0]
    lpf_cutoff: float               # Filter cutoff factor [0.1 .. 1.0]
    duration: float                 # Recommended note decay/duration in seconds
    timestamp: float                # Monotonic timestamp


# Scale intervals (semitones from root)
DORIAN_INTERVALS: tuple[int, ...] = (0, 2, 3, 5, 7, 9, 10)
MINOR_PENTATONIC_INTERVALS: tuple[int, ...] = (0, 3, 5, 7, 10)
LYDIAN_INTERVALS: tuple[int, ...] = (0, 2, 4, 6, 7, 9, 11)
AEOLIAN_INTERVALS: tuple[int, ...] = (0, 2, 3, 5, 7, 8, 10)
LOFI_CHORD_INTERVALS: tuple[int, ...] = (0, 3, 7, 10, 14, 17, 21)

# Circle of fifths pitch classes starting from D (D=2)
CIRCLE_OF_FIFTHS_ROOTS: tuple[int, ...] = (
    2,   # D
    9,   # A
    4,   # E
    11,  # B
    6,   # F#
    1,   # C#
    8,   # G# / Ab
    3,   # D# / Eb
    10,  # A# / Bb
    5,   # F
    0,   # C
    7    # G
)

CIRCLE_ROOT_NAMES: tuple[str, ...] = (
    "D", "A", "E", "B", "F#", "C#", "Ab", "Eb", "Bb", "F", "C", "G"
)

CHORD_NAMES: tuple[str, ...] = (
    "Dm9", "Am9", "Em9", "Bm9", "F#m9", "C#m9", "Abm9", "Ebm9", "Bbm9", "Fm9", "Cm9", "Gm9"
)

NOTE_NAMES: tuple[str, ...] = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
)


def midi_to_freq(midi_note: int) -> float:
    """Converts MIDI note number to Hz: f = 440 * 2^((m - 69) / 12)."""
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def freq_to_note_name(freq: float) -> str:
    """Converts frequency in Hz to musical pitch name (e.g. 440.0 -> 'A4')."""
    if freq <= 15.0:
        return "Sub"
    midi_num = round(69.0 + 12.0 * math.log2(freq / 440.0))
    pitch_name = NOTE_NAMES[midi_num % 12]
    octave = (midi_num // 12) - 1
    return f"{pitch_name}{octave}"


def build_scale_lookup_table(
    intervals: Sequence[int] = DORIAN_INTERVALS,
    octaves: Sequence[int] = (3, 4, 5)
) -> tuple[tuple[float, ...], ...]:
    """Precomputes immutable frequency tables for all 12 circle-of-fifths keys."""
    tables: list[tuple[float, ...]] = []
    for root_pitch in CIRCLE_OF_FIFTHS_ROOTS:
        key_freqs: list[float] = []
        for oct_num in octaves:
            base_midi = (oct_num + 1) * 12 + root_pitch
            for interval in intervals:
                freq = midi_to_freq(base_midi + interval)
                key_freqs.append(round(freq, 3))
        tables.append(tuple(key_freqs))
    return tuple(tables)


def build_sub_lookup_table() -> tuple[float, ...]:
    """Precomputes sub-bass root frequencies (octave 1 ~35-70 Hz) for all 12 keys."""
    subs: list[float] = []
    for root_pitch in CIRCLE_OF_FIFTHS_ROOTS:
        sub_midi = 2 * 12 + root_pitch
        subs.append(round(midi_to_freq(sub_midi), 3))
    return tuple(subs)


# Static lookup tables precomputed once at module load
PRECOMPUTED_DORIAN_TABLES: tuple[tuple[float, ...], ...] = build_scale_lookup_table(
    DORIAN_INTERVALS, octaves=(3, 4, 5)
)
PRECOMPUTED_PENTATONIC_TABLES: tuple[tuple[float, ...], ...] = build_scale_lookup_table(
    MINOR_PENTATONIC_INTERVALS, octaves=(3, 4, 5)
)
PRECOMPUTED_LYDIAN_TABLES: tuple[tuple[float, ...], ...] = build_scale_lookup_table(
    LYDIAN_INTERVALS, octaves=(3, 4, 5)
)
PRECOMPUTED_AEOLIAN_TABLES: tuple[tuple[float, ...], ...] = build_scale_lookup_table(
    AEOLIAN_INTERVALS, octaves=(3, 4, 5)
)
PRECOMPUTED_LOFI_TABLES: tuple[tuple[float, ...], ...] = build_scale_lookup_table(
    LOFI_CHORD_INTERVALS, octaves=(3, 4, 5)
)
PRECOMPUTED_SUB_TABLES: tuple[float, ...] = build_sub_lookup_table()


class HarmonyEngine:
    """Procedural music theory engine mapping keystrokes to quantized modal audio events."""

    def __init__(
        self,
        input_queue: Optional[queue.Queue[KeyEvent]] = None,
        audio_queue: Optional[queue.Queue[HarmonyEvent]] = None,
        scale_mode: str = "dorian",
        lpf_tau: float = 0.8,
        lpf_min: float = 0.15,
        cooldown_seconds: float = 0.080
    ) -> None:
        self.input_queue: queue.Queue[KeyEvent] = (
            input_queue if input_queue is not None else queue.Queue(maxsize=256)
        )
        self.audio_queue: queue.Queue[HarmonyEvent] = (
            audio_queue if audio_queue is not None else queue.Queue(maxsize=256)
        )

        self.scale_mode = scale_mode
        self._scale_tables = self._resolve_scale_tables(scale_mode)

        self._circle_index: int = 0         # 0 = D root
        self._arpeggio_step: int = 0        # Stepper across scale degrees

        # LPF dynamics
        self._lpf_cutoff: float = 1.0       # Normalized cutoff [0.15 .. 1.0]
        self._lpf_tau: float = lpf_tau
        self._lpf_min: float = lpf_min
        self._last_lpf_update_time: float = time.monotonic()

        # Anti-Clutter Rate Limiting
        self.cooldown_seconds = cooldown_seconds
        self._last_note_time: float = 0.0
        self.last_note_name: str = "Ready"

        # Threading state
        self._is_running = False
        self._worker_thread: Optional[threading.Thread] = None

    def _resolve_scale_tables(self, mode: str) -> tuple[tuple[float, ...], ...]:
        normalized = mode.lower()
        if "pentatonic" in normalized:
            return PRECOMPUTED_PENTATONIC_TABLES
        elif "lydian" in normalized:
            return PRECOMPUTED_LYDIAN_TABLES
        elif "aeolian" in normalized or "minor" in normalized:
            return PRECOMPUTED_AEOLIAN_TABLES
        elif "lofi" in normalized:
            return PRECOMPUTED_LOFI_TABLES
        return PRECOMPUTED_DORIAN_TABLES

    def set_scale_mode(self, mode: str) -> None:
        """Dynamically switches active modal scale."""
        self.scale_mode = mode
        self._scale_tables = self._resolve_scale_tables(mode)

    def set_cooldown_ms(self, cooldown_ms: float) -> None:
        """Sets anti-clutter cooldown threshold in milliseconds."""
        self.cooldown_seconds = max(0.020, min(0.300, cooldown_ms / 1000.0))

    @property
    def current_root_name(self) -> str:
        return CIRCLE_ROOT_NAMES[self._circle_index]

    @property
    def current_chord_name(self) -> str:
        return CHORD_NAMES[self._circle_index]

    @property
    def current_lpf_cutoff(self) -> float:
        return self._lpf_cutoff

    @property
    def current_circle_index(self) -> int:
        return self._circle_index

    def update_lpf(self, current_time: Optional[float] = None) -> float:
        now = time.monotonic() if current_time is None else current_time
        dt = now - self._last_lpf_update_time
        if dt > 0.0:
            recovery = (1.0 - self._lpf_cutoff) * (1.0 - math.exp(-dt / self._lpf_tau))
            self._lpf_cutoff = clamp(self._lpf_cutoff + recovery, self._lpf_min, 1.0)
            self._last_lpf_update_time = now
        return self._lpf_cutoff

    def modulate_circle_of_fifths(self, step: int) -> int:
        self._circle_index = (self._circle_index + step) % 12
        return self._circle_index

    def process_key_event(
        self,
        event: KeyEvent,
        current_time: Optional[float] = None
    ) -> Optional[HarmonyEvent]:
        now = time.monotonic() if current_time is None else current_time
        self.update_lpf(now)

        active_scale = self._scale_tables[self._circle_index]
        scale_len = len(active_scale)

        # 1. Backspace: Drop filter cutoff down to muffled underwater effect
        if event.key_type == "backspace":
            self._lpf_cutoff = self._lpf_min
            self._last_lpf_update_time = now
            self.last_note_name = "Filter Drop"
            return HarmonyEvent(
                event_type="lpf_update",
                frequency=0.0,
                velocity=0.5,
                lpf_cutoff=self._lpf_cutoff,
                duration=0.6,
                timestamp=now
            )

        # 2. Enter: Deep sub-bass drop on active chord root
        if event.key_type == "enter":
            sub_freq = PRECOMPUTED_SUB_TABLES[self._circle_index]
            self._arpeggio_step = 0
            self._last_note_time = now
            self.last_note_name = f"{self.current_root_name}1 Sub"
            return HarmonyEvent(
                event_type="sub_drop",
                frequency=sub_freq,
                velocity=0.9,
                lpf_cutoff=self._lpf_cutoff,
                duration=2.2,
                timestamp=now
            )

        # 3. Space Bar: Tactile soft vinyl click / percussive crackle
        if event.key_type == "regular" and event.raw_char == ' ':
            self.last_note_name = "Vinyl Click"
            return HarmonyEvent(
                event_type="percussion",
                frequency=160.0,
                velocity=clamp(0.25 + 0.3 * (event.wpm / 120.0), 0.2, 0.5),
                lpf_cutoff=self._lpf_cutoff,
                duration=0.05,
                timestamp=now
            )

        # 4. Modulation: Shift scale along the circle of fifths
        if event.key_type == "modulation":
            if event.raw_char in {'(', '[', '{', "'"}:
                self.modulate_circle_of_fifths(+1)
            else:
                self.modulate_circle_of_fifths(-1)

            new_scale = self._scale_tables[self._circle_index]
            chime_idx = min(4, len(new_scale) - 1)
            self._last_note_time = now
            freq = new_scale[chime_idx]
            self.last_note_name = f"Mod -> {self.current_root_name} ({freq_to_note_name(freq)})"
            return HarmonyEvent(
                event_type="note",
                frequency=freq,
                velocity=clamp(0.4 + 0.3 * (event.wpm / 120.0), 0.3, 0.75),
                lpf_cutoff=self._lpf_cutoff,
                duration=1.8,
                timestamp=now
            )

        # 5. Regular typing: Check Rate-Limiting Cooldown
        if event.key_type == "regular":
            time_since_last_note = now - self._last_note_time
            if time_since_last_note < self.cooldown_seconds:
                # Anti-clutter: Do not spam new note; open filter cutoff slightly instead
                self._lpf_cutoff = clamp(self._lpf_cutoff + 0.035, self._lpf_min, 1.0)
                self._last_lpf_update_time = now
                return HarmonyEvent(
                    event_type="lpf_update",
                    frequency=0.0,
                    velocity=0.3,
                    lpf_cutoff=self._lpf_cutoff,
                    duration=0.4,
                    timestamp=now
                )

            # Trigger new quantized note from active scale
            self._last_note_time = now
            char_variance = (ord(event.raw_char) % 4) if event.raw_char else 0
            note_idx = (self._arpeggio_step + char_variance) % scale_len
            self._arpeggio_step = (self._arpeggio_step + 1) % scale_len

            freq = active_scale[note_idx]
            self.last_note_name = freq_to_note_name(freq)

            velocity = clamp(0.35 + 0.55 * (event.wpm / 120.0), 0.25, 0.95)
            duration = clamp(60.0 / max(event.wpm * 1.5, 40.0), 1.4, 2.4)

            return HarmonyEvent(
                event_type="note",
                frequency=freq,
                velocity=velocity,
                lpf_cutoff=self._lpf_cutoff,
                duration=duration,
                timestamp=now
            )

        return None

    def process_pending_events(self, max_events: int = 64) -> list[HarmonyEvent]:
        generated: list[HarmonyEvent] = []
        count = 0
        while count < max_events:
            try:
                event = self.input_queue.get_nowait()
            except queue.Empty:
                break

            harmony_ev = self.process_key_event(event)
            if harmony_ev is not None:
                generated.append(harmony_ev)
                self._enqueue_audio(harmony_ev)
            count += 1
        return generated

    def _enqueue_audio(self, event: HarmonyEvent) -> None:
        try:
            self.audio_queue.put_nowait(event)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.audio_queue.put_nowait(event)
            except queue.Full:
                pass

    def _worker_loop(self) -> None:
        while self._is_running:
            try:
                event = self.input_queue.get(timeout=0.05)
                harmony_ev = self.process_key_event(event)
                if harmony_ev is not None:
                    self._enqueue_audio(harmony_ev)
            except queue.Empty:
                self.update_lpf()
                continue
            except Exception:
                break

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._is_running = False
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=0.2)
            self._worker_thread = None
