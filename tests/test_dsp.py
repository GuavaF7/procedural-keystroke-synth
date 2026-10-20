"""Real-time performance benchmarks and QA tests for Agent-DSP engine.
Tests callback execution time limit (< 2.5 ms), voice stealing under overload (200 notes/sec),
signal integrity (NaN/Inf/Clipping checks), and LPF cutoff smoothing continuity.
"""

import queue
import time
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from src.harmony.engine import HarmonyEvent
from src.dsp.engine import AudioEngine, DSPVoice


class TestDSPCallbackBenchmark:
    """Benchmark tests ensuring strict compliance with real-time audio budget (< 2.5 ms)."""

    def test_render_block_execution_time(self):
        engine = AudioEngine(max_voices=32)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Pre-fill all 32 voices to measure worst-case load
        for i in range(32):
            ev = HarmonyEvent(
                event_type="note",
                frequency=220.0 + i * 25.0,
                velocity=0.8,
                lpf_cutoff=1.0,
                duration=1.5,
                timestamp=time.monotonic()
            )
            engine.audio_queue.put_nowait(ev)

        # Warm up
        engine.render_block(outdata)

        # Measure 100 consecutive blocks
        n_blocks = 100
        t0 = time.perf_counter()
        for _ in range(n_blocks):
            engine.render_block(outdata)
        t1 = time.perf_counter()

        avg_ms_per_block = ((t1 - t0) / n_blocks) * 1000.0
        print(f"\n[BENCHMARK] Average render_block time with 32 voices: {avg_ms_per_block:.3f} ms")

        # Must execute strictly under 2.5 ms SLA (buffer window is ~11.6 ms)
        assert avg_ms_per_block < 2.5, f"Callback too slow: {avg_ms_per_block:.3f} ms exceeds 2.5 ms limit"


class TestDSPVoiceStealingAndOverload:
    """Stress test with high-velocity input (200 notes/sec)."""

    def test_overload_200_notes_voice_stealing(self):
        engine = AudioEngine(max_voices=32)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Enqueue 200 notes rapidly (equivalent to extreme chord/arpeggio burst)
        for i in range(200):
            ev = HarmonyEvent(
                event_type="note" if i % 10 != 0 else "sub_drop",
                frequency=100.0 + (i % 50) * 15.0,
                velocity=0.9,
                lpf_cutoff=0.8,
                duration=1.2,
                timestamp=time.monotonic()
            )
            engine.audio_queue.put(ev)

        # Render 20 audio blocks to consume all events
        for _ in range(20):
            engine.render_block(outdata)
            # Signal must stay valid throughout
            assert not np.isnan(outdata).any()
            assert not np.isinf(outdata).any()
            assert np.all(outdata >= -1.0) and np.all(outdata <= 1.0)

        # Maximum active voices should never exceed 32 (bounded static pool)
        active_count = sum(1 for v in engine.voices if v.is_active)
        assert active_count <= 32


class TestDSPSignalIntegrity:
    """Verify numerical boundaries and soft saturation."""

    def test_soft_limiter_never_clips_over_unity(self):
        engine = AudioEngine(max_voices=32)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Trigger maximum velocity sub and high amplitude chords simultaneously
        for i in range(32):
            ev = HarmonyEvent(
                event_type="sub_drop" if i == 0 else "note",
                frequency=55.0 if i == 0 else 440.0,
                velocity=1.0,
                lpf_cutoff=1.0,
                duration=2.0,
                timestamp=time.monotonic()
            )
            engine.audio_queue.put(ev)

        engine.render_block(outdata)

        # All values must be strictly in [-1.0, 1.0] due to np.tanh
        max_abs = np.max(np.abs(outdata))
        assert max_abs <= 1.0
        # No NaNs or Infs
        assert not np.isnan(outdata).any()
        assert not np.isinf(outdata).any()

    def test_silence_produces_zero_audio_when_drone_disabled(self):
        engine = AudioEngine(enable_drone=False, enable_delay=False)
        outdata = np.zeros((512, 2), dtype=np.float32)

        engine.render_block(outdata)
        assert np.max(np.abs(outdata)) == 0.0

    def test_ambient_drone_sub_pad_present(self):
        engine = AudioEngine(enable_drone=True, drone_volume=0.8)
        engine._last_activity_time = time.monotonic()
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Render blocks to let gain ramp up while active
        for _ in range(5):
            engine._last_activity_time = time.monotonic()
            engine.render_block(outdata)

        max_amp = np.max(np.abs(outdata))
        # Active drone generates subtle bed of sound (~0.01 .. 0.10)
        assert 0.005 < max_amp < 0.15

    def test_ping_pong_delay_spreads_echoes(self):
        engine = AudioEngine(enable_drone=False, enable_delay=True)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Trigger single percussion click
        ev = HarmonyEvent(
            event_type="percussion",
            frequency=160.0,
            velocity=0.8,
            lpf_cutoff=1.0,
            duration=0.05,
            timestamp=time.monotonic()
        )
        engine.audio_queue.put(ev)
        engine.render_block(outdata)

        # Check that delay buffer now holds feedback energy
        assert np.max(np.abs(engine._delay_buf)) > 0.0


class TestDSPLpfSmoothingContinuity:
    """Verify that abrupt cutoff drops (Backspace) produce continuous audio without clicks."""

    def test_filter_cutoff_smooth_transition_no_discontinuities(self):
        engine = AudioEngine()
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Start with full cutoff
        ev_note = HarmonyEvent(
            event_type="note",
            frequency=600.0,
            velocity=0.8,
            lpf_cutoff=1.0,
            duration=2.0,
            timestamp=time.monotonic()
        )
        engine.audio_queue.put(ev_note)
        engine.render_block(outdata)

        # Record signal before abrupt drop
        prev_tail = outdata[-1].copy()

        # Send series of 5 rapid Backspaces (abrupt drop to 0.15)
        for _ in range(5):
            ev_bs = HarmonyEvent(
                event_type="lpf_update",
                frequency=0.0,
                velocity=0.5,
                lpf_cutoff=0.15,
                duration=0.5,
                timestamp=time.monotonic()
            )
            engine.audio_queue.put(ev_bs)

        # Render next block
        engine.render_block(outdata)
        first_sample = outdata[0].copy()

        # Delta between end of previous block and start of new block must be smooth (no click / pop)
        delta_l = abs(first_sample[0] - prev_tail[0])
        delta_r = abs(first_sample[1] - prev_tail[1])

        # A sudden DC jump / click would be > 0.5; smooth continuity is small
        assert delta_l < 0.35, f"Left channel discontinuity detected: {delta_l:.4f}"
        assert delta_r < 0.35, f"Right channel discontinuity detected: {delta_r:.4f}"


class TestDSPStreamLifecycle:
    """Test start and stop lifecycle and mock stream interaction."""

    def test_stream_lifecycle(self):
        engine = AudioEngine()
        with patch("sounddevice.OutputStream") as mock_stream_cls:
            mock_inst = mock_stream_cls.return_value
            engine.start()
            assert engine.is_running is True
            mock_inst.start.assert_called_once()

            # Second start is no-op
            engine.start()

            engine.stop()
            assert engine.is_running is False
            mock_inst.stop.assert_called_once()
            mock_inst.close.assert_called_once()
