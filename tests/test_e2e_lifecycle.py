"""End-to-End lifecycle and integration tests for Procedural Keystroke Synth.

Validates:
- End-to-end pipeline: Key Press -> Telemetry -> Harmony Rules -> DSP Audio Block;
- Mute toggle behavior: zero audio output while stream keeps running;
- Master volume adjustment via tray / orchestrator;
- Clean cascade shutdown without hanging threads or deadlocks;
- Procedural tray icon states (Active vs Muted).
"""

import math
import time
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
from pynput import keyboard

from src.main import SynthOrchestrator
from src.lifecycle.tray import TrayManager, create_tray_icon


class TestEndToEndPipeline:
    """Verifies end-to-end event propagation through all subagents."""

    def test_key_press_to_audio_pipeline(self):
        orchestrator = SynthOrchestrator(enable_tray=False)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # 1. Simulate key presses in Telemetry hook
        key_a = keyboard.KeyCode.from_char('a')
        key_enter = keyboard.Key.enter
        orchestrator.telemetry._on_press(key_a)
        orchestrator.telemetry._on_press(key_enter)

        # Verify items reached input queue
        assert orchestrator.input_queue.qsize() == 2

        # 2. Process events through Harmony Engine
        harmony_events = orchestrator.harmony.process_pending_events()
        assert len(harmony_events) == 2
        assert orchestrator.audio_queue.qsize() == 2

        # 3. Render block in DSP Engine
        orchestrator.audio.render_block(outdata)

        # Audio buffer should contain non-zero synth soundwaves
        max_amplitude = np.max(np.abs(outdata))
        assert max_amplitude > 0.05
        assert max_amplitude <= 1.0
        assert not np.isnan(outdata).any()
        assert not np.isinf(outdata).any()

    def test_mute_toggle_silences_audio_output(self):
        orchestrator = SynthOrchestrator(enable_tray=False)
        outdata = np.zeros((512, 2), dtype=np.float32)

        # Send regular keystroke
        orchestrator.telemetry._on_press(keyboard.KeyCode.from_char('k'))
        orchestrator.harmony.process_pending_events()

        # Render unmuted
        orchestrator.audio.render_block(outdata)
        assert np.max(np.abs(outdata)) > 0.0

        # Enable Mute
        orchestrator.audio.toggle_mute()
        assert orchestrator.audio.is_muted is True

        # Send another keystroke
        orchestrator.telemetry._on_press(keyboard.KeyCode.from_char('z'))
        orchestrator.harmony.process_pending_events()

        # Render muted: must be strictly silent (all zeros)
        orchestrator.audio.render_block(outdata)
        assert np.max(np.abs(outdata)) == 0.0

        # Disable Mute (Unmute)
        orchestrator.audio.toggle_mute()
        assert orchestrator.audio.is_muted is False

        # Send another keystroke
        orchestrator.telemetry._on_press(keyboard.KeyCode.from_char('c'))
        orchestrator.harmony.process_pending_events()

        # Render unmuted: sound should be restored
        orchestrator.audio.render_block(outdata)
        assert np.max(np.abs(outdata)) > 0.0

    def test_master_volume_scaling(self):
        from src.harmony.engine import HarmonyEvent

        orchestrator = SynthOrchestrator(enable_tray=False, master_volume=1.0)
        outdata_100 = np.zeros((512, 2), dtype=np.float32)
        outdata_25 = np.zeros((512, 2), dtype=np.float32)

        # Trigger identical note at 100% volume
        ev1 = HarmonyEvent(
            event_type="note",
            frequency=440.0,
            velocity=0.8,
            lpf_cutoff=1.0,
            duration=1.0,
            timestamp=100.0
        )
        orchestrator.audio_queue.put(ev1)
        orchestrator.audio.render_block(outdata_100)
        amp_100 = np.max(np.abs(outdata_100))

        # Reset voices, reset filter states and set volume to 25%
        orchestrator.audio.set_master_volume(0.25)
        for v in orchestrator.audio.voices:
            v.is_active = False
            v.phase1 = 0.0
            v.phase2 = 0.0
        orchestrator.audio._filter_state_l = 0.0
        orchestrator.audio._filter_state_r = 0.0
        orchestrator.audio._delay_buf.fill(0.0)

        ev2 = HarmonyEvent(
            event_type="note",
            frequency=440.0,
            velocity=0.8,
            lpf_cutoff=1.0,
            duration=1.0,
            timestamp=100.0
        )
        orchestrator.audio_queue.put(ev2)
        orchestrator.audio.render_block(outdata_25)
        amp_25 = np.max(np.abs(outdata_25))

        # 25% volume output must scale proportionately (approx 0.25x)
        assert pytest.approx(amp_25 / amp_100, rel=0.1) == 0.25


class TestLifecycleTeardown:
    """Verifies graceful cascade termination without hangs or resource leaks."""

    def test_clean_orchestrator_start_stop(self):
        with patch("sounddevice.OutputStream") as mock_stream_cls, \
             patch("pynput.keyboard.Listener") as mock_listener_cls:

            orchestrator = SynthOrchestrator(enable_tray=False)
            orchestrator.start()
            assert orchestrator.is_running is True

            # Stop orchestrator
            orchestrator.stop()
            assert orchestrator.is_running is False

            # Verify downstream subagents were stopped
            assert orchestrator.telemetry.is_running is False
            assert orchestrator.harmony._is_running is False
            assert orchestrator.audio.is_running is False


class TestTrayIntegration:
    """Verifies tray icon creation and menu actions."""

    def test_procedural_tray_icon_generation(self):
        active_icon = create_tray_icon(is_muted=False)
        assert active_icon.size == (64, 64)
        assert active_icon.mode == "RGBA"

        muted_icon = create_tray_icon(is_muted=True)
        assert muted_icon.size == (64, 64)
        assert muted_icon.mode == "RGBA"

    def test_tray_manager_mute_toggle_and_volume(self):
        orchestrator = SynthOrchestrator(enable_tray=False)
        tray = TrayManager(
            telemetry=orchestrator.telemetry,
            harmony=orchestrator.harmony,
            audio=orchestrator.audio,
            on_exit=orchestrator.stop
        )

        # Initial mute state
        assert orchestrator.audio.is_muted is False

        # Toggle mute through tray
        new_state = tray.toggle_mute()
        assert new_state is True
        assert orchestrator.audio.is_muted is True

        # Toggle back
        new_state = tray.toggle_mute()
        assert new_state is False
        assert orchestrator.audio.is_muted is False

        # Set volume
        tray.set_volume(0.5)
        assert math.isclose(orchestrator.audio.master_volume, 0.5)

    def test_tray_menu_streamlined_structure(self):
        orchestrator = SynthOrchestrator(enable_tray=False)
        mock_open_gui = MagicMock()
        mock_exit = MagicMock()

        tray = TrayManager(
            telemetry=orchestrator.telemetry,
            harmony=orchestrator.harmony,
            audio=orchestrator.audio,
            on_exit=mock_exit,
            on_open_gui=mock_open_gui
        )
        menu = tray._build_menu()
        items = list(menu.items)

        # Must have exactly 4 items (Open, Mute, Separator, Exit)
        assert len(items) == 4

        # Item 0: Open Window with default=True (double click / single click)
        open_item = items[0]
        assert "Открыть" in open_item.text
        assert open_item.default is True
        open_item(MagicMock())
        mock_open_gui.assert_called_once()

        # Item 1: Dynamic Mute Toggle with checkbox
        mute_item = items[1]
        assert mute_item.checked is False
        assert "Без звука" in mute_item.text

        # Toggle mute
        orchestrator.audio.is_muted = True
        assert mute_item.checked is True
        assert "Включить звук" in mute_item.text

        # Item 2: Separator
        separator_item = items[2]
        import pystray
        assert separator_item == pystray.Menu.SEPARATOR or not separator_item.text or separator_item.text.startswith("-")

        # Item 3: Exit
        exit_item = items[3]
        assert "Выход" in exit_item.text
        exit_item(MagicMock())
        mock_exit.assert_called_once()

    def test_tray_lifecycle_with_mocks(self):
        orchestrator = SynthOrchestrator(enable_tray=False)
        tray = TrayManager(
            telemetry=orchestrator.telemetry,
            harmony=orchestrator.harmony,
            audio=orchestrator.audio,
            on_exit=orchestrator.stop
        )

        with patch("pystray.Icon") as mock_icon_cls, \
             patch("pynput.keyboard.GlobalHotKeys") as mock_hotkeys_cls:
            tray.start(run_detached=True)
            assert tray.is_running is True

            tray.stop()
            assert tray.is_running is False


class TestMainCli:
    """Verifies argument parsing and top-level runner."""

    def test_parse_arguments_default(self):
        from src.main import parse_arguments
        with patch("sys.argv", ["main.py"]):
            args = parse_arguments()
            assert args.scale == "dorian"
            assert args.volume == 0.85
            assert args.buffer_size == 512
            assert args.no_tray is False

    def test_parse_arguments_custom(self):
        from src.main import parse_arguments
        with patch("sys.argv", ["main.py", "--scale", "pentatonic", "--volume", "0.5", "--buffer-size", "256", "--no-tray"]):
            args = parse_arguments()
            assert args.scale == "pentatonic"
            assert args.volume == 0.5
            assert args.buffer_size == 256
            assert args.no_tray is True

    def test_run_forever_immediate_shutdown(self):
        with patch("sounddevice.OutputStream"), \
             patch("pynput.keyboard.Listener"):
            orchestrator = SynthOrchestrator(enable_tray=False)
            orchestrator._shutdown_event.set()
            orchestrator.run_forever()
            assert orchestrator.is_running is True
            orchestrator.stop()
            assert orchestrator.is_running is False

