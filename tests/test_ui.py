"""Unit and integration tests for Cyber-Night GUI window (src/ui/window.py)."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from src.input.telemetry import InputTelemetry
from src.harmony.engine import HarmonyEngine
from src.dsp.engine import AudioEngine
from src.ui.window import SynthAppWindow


@pytest.fixture
def mock_engines():
    telemetry = MagicMock(spec=InputTelemetry)
    telemetry.calculate_wpm.return_value = 68.5

    harmony = MagicMock(spec=HarmonyEngine)
    harmony.current_root_name = "D"
    harmony.scale_mode = "dorian"
    harmony.last_note_name = "F4"
    harmony.cooldown_seconds = 0.080
    harmony._arpeggio_step = 5

    audio = MagicMock(spec=AudioEngine)
    audio.master_volume = 0.70
    audio.keys_volume = 0.85
    audio.sub_volume = 0.90
    audio.drone_volume = 0.0
    audio.enable_drone = False
    audio.delay_wet = 0.30
    audio.delay_feedback = 0.40
    audio.sound_preset = "Warm Rhodes"
    audio.is_muted = False

    return telemetry, harmony, audio


class TestUIWindow:
    def test_window_init_and_widgets(self, mock_engines):
        telemetry, harmony, audio = mock_engines

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            cfg_path = tf.name

        try:
            window = SynthAppWindow(
                telemetry=telemetry,
                harmony=harmony,
                audio=audio,
                config_path=cfg_path
            )

            assert window.title() == "Procedural Keystroke Synth — Cyber-Night Studio"
            assert window.wpm_label is not None
            assert window.mute_btn is not None
            assert window.preset_menu.get() == "Warm Rhodes"

            # Test sliders interaction
            window._on_master_slider(0.5)
            audio.set_master_volume.assert_called_with(0.5)

            window._on_keys_slider(0.6)
            audio.set_keys_volume.assert_called_with(0.6)

            window._on_sub_slider(0.7)
            audio.set_sub_volume.assert_called_with(0.7)

            window._on_delay_wet_slider(0.4)
            audio.set_delay_wet.assert_called_with(0.4)

            window._on_delay_fb_slider(0.5)
            audio.set_delay_feedback.assert_called_with(0.5)

            window._on_cooldown_slider(100.0)
            harmony.set_cooldown_ms.assert_called_with(100.0)

            window._on_drone_slider(0.2)
            audio.set_drone_volume.assert_called_with(0.2)

            # Test preset change
            window._on_preset_change("Crystal Bells")
            audio.set_preset.assert_called_with("Crystal Bells")

            # Test scale change
            window._on_scale_change("Lydian (Dreamy)")
            harmony.set_scale_mode.assert_called_with("lydian")

            # Test mute toggle
            window.toggle_mute()
            audio.toggle_mute.assert_called_once()

            # Test minimize and restore
            window.minimize_to_tray()
            assert window.state() == "withdrawn"

            # Execute thread-safe restore
            window.restore_from_tray()
            window.update()  # Process scheduled callbacks
            assert window.state() == "normal"

            # Test mute visual synchronization
            audio.is_muted = True
            window._update_mute_button_visuals()
            assert window.mute_btn.cget("text") == "MUTED"

            audio.is_muted = False
            window._update_mute_button_visuals()
            assert window.mute_btn.cget("text") == "SOUND ACTIVE"

            # Test background thread restore
            window.minimize_to_tray()
            assert window.state() == "withdrawn"

            import threading
            bg_thread = threading.Thread(target=window.restore_from_tray)
            bg_thread.start()
            bg_thread.join()
            window._drain_ui_queue()
            window.update()
            assert window.state() == "normal"

            # Clean up
            window.destroy()
        finally:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
