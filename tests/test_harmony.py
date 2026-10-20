"""Unit and integration tests for Agent-Harmony rules engine.
Tests scale quantization, circle of fifths modulation, LPF envelope recovery,
and pipeline integration with Agent-Input telemetry events.
"""

import queue
import time
from unittest.mock import patch

import pytest
from pynput import keyboard

from src.input.telemetry import InputTelemetry, KeyEvent
from src.harmony.engine import (
    HarmonyEngine,
    HarmonyEvent,
    PRECOMPUTED_DORIAN_TABLES,
    PRECOMPUTED_PENTATONIC_TABLES,
    PRECOMPUTED_SUB_TABLES,
    CIRCLE_ROOT_NAMES,
)


class TestHarmonyQuantization:
    """Validate that every produced frequency is strictly in scale."""

    def test_regular_notes_strict_quantization_dorian(self):
        engine = HarmonyEngine(scale_mode="dorian")
        allowed_freqs = set(PRECOMPUTED_DORIAN_TABLES[0])  # D Dorian

        # Test typing across alphanumeric keys with timestamp progression > cooldown
        for i, char in enumerate("abcdefghijklmnopqrstuvwxyz1234567890"):
            t = 100.0 + i * 0.1
            ev = KeyEvent(timestamp=t, key_type="regular", raw_char=char, wpm=60.0)
            harm_ev = engine.process_key_event(ev, current_time=t)
            assert harm_ev is not None
            assert harm_ev.event_type == "note"
            assert harm_ev.frequency in allowed_freqs, f"Freq {harm_ev.frequency} not in active scale"
            assert 0.0 <= harm_ev.velocity <= 1.0

    def test_space_bar_percussion_event(self):
        engine = HarmonyEngine()
        ev = KeyEvent(timestamp=100.0, key_type="regular", raw_char=' ', wpm=60.0)
        harm_ev = engine.process_key_event(ev, current_time=100.0)
        assert harm_ev is not None
        assert harm_ev.event_type == "percussion"

    def test_rate_limiting_cooldown_anti_clutter(self):
        engine = HarmonyEngine(cooldown_seconds=0.080)
        t0 = 100.0

        # First keystroke -> note
        ev1 = KeyEvent(timestamp=t0, key_type="regular", raw_char='a', wpm=120.0)
        harm1 = engine.process_key_event(ev1, current_time=t0)
        assert harm1.event_type == "note"

        # Rapid second keystroke within 20 ms (< 80 ms cooldown) -> lpf_update instead of note
        t1 = t0 + 0.020
        ev2 = KeyEvent(timestamp=t1, key_type="regular", raw_char='b', wpm=120.0)
        harm2 = engine.process_key_event(ev2, current_time=t1)
        assert harm2.event_type == "lpf_update"

    def test_regular_notes_strict_quantization_pentatonic(self):
        engine = HarmonyEngine(scale_mode="pentatonic")
        allowed_freqs = set(PRECOMPUTED_PENTATONIC_TABLES[0])  # D Minor Pentatonic

        for i, char in enumerate("python_code_test"):
            t = 100.0 + i * 0.1
            ev = KeyEvent(timestamp=t, key_type="regular", raw_char=char, wpm=80.0)
            harm_ev = engine.process_key_event(ev, current_time=t)
            assert harm_ev is not None
            assert harm_ev.frequency in allowed_freqs


class TestModulationCircleOfFifths:
    """Validate circle of fifths rotation and transposed frequency sets."""

    def test_modulation_clockwise_and_counter_clockwise(self):
        engine = HarmonyEngine()
        assert engine.current_root_name == "D"
        assert engine.current_circle_index == 0

        # Opening bracket: +1 step clockwise (D -> A)
        ev_open = KeyEvent(timestamp=100.0, key_type="modulation", raw_char="(", wpm=50.0)
        harm_open = engine.process_key_event(ev_open, current_time=100.0)
        assert harm_open is not None
        assert engine.current_root_name == "A"
        assert engine.current_circle_index == 1
        assert harm_open.frequency in PRECOMPUTED_DORIAN_TABLES[1]

        # Further modulation: [ -> +1 step (A -> E)
        ev_bracket = KeyEvent(timestamp=100.1, key_type="modulation", raw_char="[", wpm=50.0)
        engine.process_key_event(ev_bracket, current_time=100.1)
        assert engine.current_root_name == "E"
        assert engine.current_circle_index == 2

        # Closing bracket: -1 step counter-clockwise (E -> A)
        ev_close = KeyEvent(timestamp=100.2, key_type="modulation", raw_char="]", wpm=50.0)
        engine.process_key_event(ev_close, current_time=100.2)
        assert engine.current_root_name == "A"
        assert engine.current_circle_index == 1

        # Tab: -1 step counter-clockwise (A -> D)
        ev_tab = KeyEvent(timestamp=100.3, key_type="modulation", raw_char="\t", wpm=50.0)
        engine.process_key_event(ev_tab, current_time=100.3)
        assert engine.current_root_name == "D"
        assert engine.current_circle_index == 0

    def test_full_circle_wrap_around(self):
        engine = HarmonyEngine()
        # 12 dominant steps should return to D
        for _ in range(12):
            engine.modulate_circle_of_fifths(+1)
        assert engine.current_circle_index == 0
        assert engine.current_root_name == "D"


class TestLpfFilterDynamics:
    """Validate LPF drop on backspace and exponential recovery."""

    def test_backspace_drops_lpf_and_smoothly_recovers(self):
        engine = HarmonyEngine(lpf_tau=1.0, lpf_min=0.15)
        t0 = 100.0
        assert engine.current_lpf_cutoff == 1.0

        # Press backspace
        ev_bs = KeyEvent(timestamp=t0, key_type="backspace", raw_char="\b", wpm=40.0)
        harm_bs = engine.process_key_event(ev_bs, current_time=t0)
        assert harm_bs is not None
        assert harm_bs.event_type == "lpf_update"
        assert harm_bs.lpf_cutoff == 0.15
        assert engine.current_lpf_cutoff == 0.15

        # 0.5 sec later -> partial recovery: 0.15 + (1.0 - 0.15) * (1 - e^(-0.5)) ≈ 0.15 + 0.85 * 0.393 = 0.484
        engine.update_lpf(current_time=t0 + 0.5)
        cutoff_half_sec = engine.current_lpf_cutoff
        assert 0.40 < cutoff_half_sec < 0.55

        # 3.0 sec later -> almost fully recovered to 1.0
        engine.update_lpf(current_time=t0 + 3.0)
        assert engine.current_lpf_cutoff > 0.90


class TestEnterSubDrop:
    """Validate deep sub-bass drop on Enter key."""

    def test_enter_sub_drop_frequency(self):
        engine = HarmonyEngine()
        t0 = 200.0
        ev_enter = KeyEvent(timestamp=t0, key_type="enter", raw_char="\n", wpm=60.0)
        harm_ev = engine.process_key_event(ev_enter, current_time=t0)

        assert harm_ev is not None
        assert harm_ev.event_type == "sub_drop"
        # Root D sub frequency is approx 36.71 Hz
        assert pytest.approx(harm_ev.frequency, rel=1e-2) == 36.71
        assert harm_ev.velocity >= 0.85


class TestPipelineIntegration:
    """End-to-end integration test: InputTelemetry -> HarmonyEngine."""

    def test_telemetry_to_harmony_pipeline(self):
        shared_input_queue: queue.Queue[KeyEvent] = queue.Queue(maxsize=100)
        shared_audio_queue: queue.Queue[HarmonyEvent] = queue.Queue(maxsize=100)

        telemetry = InputTelemetry(event_queue=shared_input_queue)
        harmony = HarmonyEngine(
            input_queue=shared_input_queue,
            audio_queue=shared_audio_queue,
            scale_mode="dorian"
        )

        # Simulate user typing: "def main():\n"
        input_keys = [
            keyboard.KeyCode.from_char('d'),
            keyboard.KeyCode.from_char('e'),
            keyboard.KeyCode.from_char('f'),
            keyboard.Key.space,
            keyboard.KeyCode.from_char('('),
            keyboard.KeyCode.from_char(')'),
            keyboard.Key.enter
        ]

        for k in input_keys:
            telemetry._on_press(k)

        # Input queue must contain 7 events
        assert shared_input_queue.qsize() == 7

        # Drain and process through HarmonyEngine
        processed_events = harmony.process_pending_events(max_events=10)
        assert len(processed_events) == 7
        assert shared_audio_queue.qsize() == 7

        # Last event must be sub_drop from Enter
        last_ev = processed_events[-1]
        assert last_ev.event_type == "sub_drop"
        assert pytest.approx(last_ev.frequency, rel=1e-2) == 36.71

        # Check that opening parenthesis modulated the key
        open_bracket_event = processed_events[4]
        assert open_bracket_event.event_type == "note"
        # Active root became D again ('(' shifted to A (+1), ')' shifted back to D (-1))
        assert harmony.current_root_name == "D"

    def test_harmony_thread_lifecycle_and_background_worker(self):
        input_q: queue.Queue[KeyEvent] = queue.Queue()
        audio_q: queue.Queue[HarmonyEvent] = queue.Queue()
        harmony = HarmonyEngine(input_queue=input_q, audio_queue=audio_q)

        harmony.start()
        assert harmony._is_running is True

        # Calling start again should be a no-op
        harmony.start()

        # Send an event through input queue
        ev = KeyEvent(timestamp=time.monotonic(), key_type="regular", raw_char="x", wpm=50.0)
        input_q.put(ev)

        # Wait for worker thread to process
        time.sleep(0.1)
        assert audio_q.qsize() >= 1

        harmony.stop()
        assert harmony._is_running is False

    def test_audio_queue_overflow_drops_oldest(self):
        audio_q: queue.Queue[HarmonyEvent] = queue.Queue(maxsize=2)
        harmony = HarmonyEngine(audio_queue=audio_q)

        for i in range(4):
            ev = HarmonyEvent(
                event_type="note",
                frequency=440.0 + i * 10,
                velocity=0.5,
                lpf_cutoff=1.0,
                duration=0.5,
                timestamp=time.monotonic()
            )
            harmony._enqueue_audio(ev)

        assert audio_q.qsize() == 2
        ev1 = audio_q.get_nowait()
        ev2 = audio_q.get_nowait()
        assert pytest.approx(ev1.frequency) == 460.0
        assert pytest.approx(ev2.frequency) == 470.0
