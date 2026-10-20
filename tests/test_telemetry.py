"""Comprehensive unit tests for Agent-Input telemetry module.
Covers WPM sliding window calculations, key classifications, queue overflow and concurrency.
"""

import queue
import threading
import time
from unittest.mock import patch

import pytest
from pynput import keyboard

from src.input.telemetry import InputTelemetry, KeyEvent, clamp, MODULATION_CHARS


class TestTelemetryUnit:
    """Unit tests for InputTelemetry logic without physical OS hooks."""

    def test_clamp_function(self):
        assert clamp(50.0, 0.0, 100.0) == 50.0
        assert clamp(-10.0, 0.0, 100.0) == 0.0
        assert clamp(150.0, 0.0, 100.0) == 100.0

    def test_key_classification_enter(self):
        telemetry = InputTelemetry()
        k_type, char = telemetry.classify_key(keyboard.Key.enter)
        assert k_type == "enter"
        assert char == "\n"

    def test_key_classification_backspace(self):
        telemetry = InputTelemetry()
        k_type, char = telemetry.classify_key(keyboard.Key.backspace)
        assert k_type == "backspace"
        assert char == "\b"

    def test_key_classification_tab(self):
        telemetry = InputTelemetry()
        k_type, char = telemetry.classify_key(keyboard.Key.tab)
        assert k_type == "modulation"
        assert char == "\t"

    def test_key_classification_space(self):
        telemetry = InputTelemetry()
        k_type, char = telemetry.classify_key(keyboard.Key.space)
        assert k_type == "regular"
        assert char == " "

    @pytest.mark.parametrize("mod_char", ['(', ')', '[', ']', '{', '}', "'", '"'])
    def test_key_classification_modulation_chars(self, mod_char):
        telemetry = InputTelemetry()
        key = keyboard.KeyCode.from_char(mod_char)
        k_type, char = telemetry.classify_key(key)
        assert k_type == "modulation"
        assert char == mod_char

    @pytest.mark.parametrize("reg_char", ['a', 'Z', '0', '9', ';', ':', '.', '/', '!', '?'])
    def test_key_classification_regular_chars(self, reg_char):
        telemetry = InputTelemetry()
        key = keyboard.KeyCode.from_char(reg_char)
        k_type, char = telemetry.classify_key(key)
        assert k_type == "regular"
        assert char == reg_char

    def test_key_classification_unhandled_modifiers(self):
        telemetry = InputTelemetry()
        # Shift, Ctrl, Alt should return (None, None) and not trigger telemetry
        k_type, char = telemetry.classify_key(keyboard.Key.shift)
        assert k_type is None
        assert char is None

        k_type, char = telemetry.classify_key(keyboard.Key.ctrl_l)
        assert k_type is None
        assert char is None

        k_type, char = telemetry.classify_key(keyboard.Key.alt_l)
        assert k_type is None
        assert char is None


class TestWpmCalculation:
    """Test sliding window calculation with deterministic mock timestamps."""

    def test_wpm_zero_on_insufficient_samples(self):
        telemetry = InputTelemetry()
        assert telemetry.calculate_wpm() == 0.0

        telemetry._timestamps.append(100.0)
        assert telemetry.calculate_wpm(current_time=100.1) == 0.0

    def test_wpm_division_by_zero_protection(self):
        telemetry = InputTelemetry()
        # Identical timestamps (dt == 0.0)
        telemetry._timestamps.append(100.0)
        telemetry._timestamps.append(100.0)
        assert telemetry.calculate_wpm(current_time=100.0) == 0.0

    def test_wpm_calculation_exact(self):
        telemetry = InputTelemetry(window_size=30)
        # 6 keystrokes => 1 word (5 key intervals) in 1.0 second
        # 1 word in 1 sec = 60 WPM
        start_t = 100.0
        for i in range(6):
            telemetry._timestamps.append(start_t + i * 0.2)

        # dt = 1.0 second, 5 intervals (6 keys), words = (6 - 1) / 5 = 1.0
        # minutes = 1.0 / 60 = 0.016667
        # WPM = 1.0 / (1.0 / 60) = 60.0
        wpm = telemetry.calculate_wpm(current_time=101.0)
        assert pytest.approx(wpm, rel=1e-3) == 60.0

    def test_wpm_clamping_max(self):
        telemetry = InputTelemetry(window_size=30)
        # Extreme speed: 10 keys in 0.05 seconds -> would be 2160 WPM -> clamped to 200.0
        start_t = 100.0
        for i in range(10):
            telemetry._timestamps.append(start_t + i * 0.005)

        wpm = telemetry.calculate_wpm(current_time=100.05)
        assert wpm == 200.0

    def test_wpm_idle_reset(self):
        telemetry = InputTelemetry(idle_reset_seconds=2.0)
        telemetry._timestamps.append(100.0)
        telemetry._timestamps.append(100.5)

        # 5 seconds later (> 2.0 idle threshold)
        wpm = telemetry.calculate_wpm(current_time=105.5)
        assert wpm == 0.0
        assert len(telemetry._timestamps) == 0


class TestQueueAndConcurrency:
    """Stress test for queue overflow and thread safety."""

    def test_queue_overflow_drops_oldest(self):
        q = queue.Queue(maxsize=3)
        telemetry = InputTelemetry(event_queue=q, max_queue_size=3)

        # Send 5 events through _enqueue_event
        for i in range(5):
            ev = KeyEvent(timestamp=float(i), key_type="regular", raw_char=str(i), wpm=10.0)
            telemetry._enqueue_event(ev)

        assert q.qsize() == 3
        # Should contain items 2, 3, 4 (items 0 and 1 dropped)
        item0 = q.get_nowait()
        item1 = q.get_nowait()
        item2 = q.get_nowait()
        assert item0.raw_char == "2"
        assert item1.raw_char == "3"
        assert item2.raw_char == "4"

    def test_concurrent_key_presses(self):
        telemetry = InputTelemetry(max_queue_size=1000)
        num_threads = 8
        keys_per_thread = 50

        def worker(thread_idx: int):
            for i in range(keys_per_thread):
                key = keyboard.KeyCode.from_char(chr(ord('a') + (i % 26)))
                telemetry._on_press(key)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All events should be enqueued safely
        assert telemetry.event_queue.qsize() == num_threads * keys_per_thread

    def test_start_and_stop_lifecycle(self):
        telemetry = InputTelemetry()
        with patch("pynput.keyboard.Listener") as mock_listener_cls:
            mock_instance = mock_listener_cls.return_value
            telemetry.start()
            assert telemetry.is_running is True
            mock_instance.start.assert_called_once()

            telemetry.stop()
            assert telemetry.is_running is False
            mock_instance.stop.assert_called_once()
