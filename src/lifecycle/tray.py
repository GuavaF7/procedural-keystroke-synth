"""Agent-Lifecycle: System tray management, global OS hotkeys, and config persistence.

Features:
- Global Hotkeys:
  * Ctrl + Shift + M: Instant Mute / Unmute;
  * Ctrl + Alt + Up: Fine Volume Increase (+5%);
  * Ctrl + Alt + Down: Fine Volume Decrease (-5%);
- Dynamic Tray Menu with status, 7-step volume grid [10%, 25%, 40%, 55%, 70%, 85%, 100%];
- Persistent JSON configuration (config.json) preserving volume, mute, and scale across restarts.
"""

from __future__ import annotations

import json
import math
import os
from typing import Callable, Optional
from PIL import Image, ImageDraw
import pystray
from pynput import keyboard

from src.input.telemetry import InputTelemetry
from src.harmony.engine import HarmonyEngine
from src.dsp.engine import AudioEngine
from src.config.settings import SettingsManager, DEFAULT_CONFIG_PATH

CONFIG_PATH = DEFAULT_CONFIG_PATH

DEFAULT_CONFIG = {
    "master_volume": 0.70,
    "scale_mode": "dorian",
    "is_muted": False
}


def load_config(path: str = CONFIG_PATH) -> dict:
    """Loads configuration from JSON file or returns defaults."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config_data: dict, path: str = CONFIG_PATH) -> None:
    """Persists configuration to JSON file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception:
        pass


def create_tray_icon(is_muted: bool = False) -> Image.Image:
    """
    Generates a procedural 64x64 RGBA synth icon in memory.
    Active: Vibrant ambient teal (#00E5A3) with sound pulses.
    Muted: Deep crimson (#FF3B30) with muted diagonal slash.
    """
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if not is_muted:
        draw.ellipse((4, 4, 60, 60), fill=(10, 30, 30, 220), outline=(0, 229, 163, 255), width=3)
        points = [(14, 32), (20, 20), (28, 44), (36, 20), (44, 44), (50, 32)]
        draw.line(points, fill=(0, 229, 163, 255), width=3, joint="curve")
        draw.ellipse((30, 28, 34, 32), fill=(255, 255, 255, 255))
    else:
        draw.ellipse((4, 4, 60, 60), fill=(40, 15, 15, 220), outline=(255, 59, 48, 255), width=3)
        draw.line([(16, 16), (48, 48)], fill=(255, 59, 48, 255), width=4)
        draw.polygon([(20, 26), (26, 26), (36, 18), (36, 46), (26, 38), (20, 38)], fill=(255, 100, 100, 200))

    return img


class TrayManager:
    """Manages Windows system tray icon, global hotkeys, and persistent settings."""

    def __init__(
        self,
        telemetry: InputTelemetry,
        harmony: HarmonyEngine,
        audio: AudioEngine,
        on_exit: Optional[Callable[[], None]] = None,
        on_open_gui: Optional[Callable[[], None]] = None,
        config_path: str = CONFIG_PATH,
        settings_manager: Optional[SettingsManager] = None
    ) -> None:
        self.telemetry = telemetry
        self.harmony = harmony
        self.audio = audio
        self.on_exit = on_exit
        self.on_open_gui = on_open_gui
        self.config_path = config_path
        self.settings_manager = (
            settings_manager if settings_manager is not None else SettingsManager(config_path=config_path)
        )

        self._icon: Optional[pystray.Icon] = None
        self._hotkey_listener: Optional[keyboard.GlobalHotKeys] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _persist(self) -> None:
        """Saves current state to config file via SettingsManager."""
        if self.settings_manager is not None:
            self.settings_manager.config.master_volume = round(self.audio.master_volume, 2)
            self.settings_manager.config.current_scale = self.harmony.scale_mode.capitalize()
            self.settings_manager.config.is_muted = self.audio.is_muted
            self.settings_manager.save_debounced()
        else:
            data = {
                "master_volume": round(self.audio.master_volume, 2),
                "scale_mode": self.harmony.scale_mode,
                "is_muted": self.audio.is_muted
            }
            save_config(data, self.config_path)

    def toggle_mute(self) -> bool:
        """Toggles audio mute state, refreshes tray icon, and persists setting."""
        new_state = self.audio.toggle_mute()
        if self._icon is not None:
            try:
                self._icon.icon = create_tray_icon(new_state)
                if hasattr(self._icon, "update_menu"):
                    self._icon.update_menu()
            except Exception:
                pass
        self._persist()
        return new_state

    def set_volume(self, volume: float) -> None:
        """Sets master volume factor [0.0 .. 1.0] and persists setting."""
        self.audio.set_master_volume(volume)
        self._persist()

    def volume_up(self, step: float = 0.05) -> float:
        """Increases volume by step (default 5%) and persists."""
        new_vol = min(1.0, self.audio.master_volume + step)
        self.set_volume(new_vol)
        return new_vol

    def volume_down(self, step: float = 0.05) -> float:
        """Decreases volume by step (default 5%) and persists."""
        new_vol = max(0.0, self.audio.master_volume - step)
        self.set_volume(new_vol)
        return new_vol

    def _get_mute_text(self, item: pystray.MenuItem) -> str:
        """Dynamic label reflecting current audio mute state."""
        return "Включить звук" if self.audio.is_muted else "Без звука (Mute)"

    def _on_open_gui_requested(self) -> None:
        """Restores main window from tray."""
        if self.on_open_gui is not None:
            try:
                self.on_open_gui()
            except Exception:
                pass

    def _build_menu(self) -> pystray.Menu:
        """
        Builds streamlined context menu:
        1. 'Открыть окно' (Default Action: double-click / single-click);
        2. 'Mute' / 'Включить звук' (Dynamic toggle with checkbox);
        3. Separator;
        4. 'Выход' (Graceful full shutdown).
        """
        return pystray.Menu(
            pystray.MenuItem(
                "Открыть окно",
                action=lambda icon, item: self._on_open_gui_requested(),
                default=True
            ),
            pystray.MenuItem(
                self._get_mute_text,
                action=lambda icon, item: self.toggle_mute(),
                checked=lambda item: self.audio.is_muted
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Выход",
                action=lambda icon, item: self._on_exit_requested()
            )
        )

    def _on_exit_requested(self) -> None:
        self.stop()
        if self.on_exit is not None:
            self.on_exit()

    def start(self, run_detached: bool = True) -> None:
        if self._is_running:
            return
        self._is_running = True

        # Global Hotkeys: Mute + Volume Up/Down
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({
                '<ctrl>+<shift>+m': self.toggle_mute,
                '<ctrl>+<alt>+<up>': lambda: self.volume_up(0.05),
                '<ctrl>+<alt>+<down>': lambda: self.volume_down(0.05)
            })
            self._hotkey_listener.start()
        except Exception:
            self._hotkey_listener = None

        # System Tray Icon
        try:
            self._icon = pystray.Icon(
                name="ProceduralKeystrokeSynth",
                icon=create_tray_icon(self.audio.is_muted),
                title="Procedural Keystroke Synth",
                menu=self._build_menu()
            )
            if run_detached:
                self._icon.run_detached()
            else:
                self._icon.run()
        except Exception:
            self._icon = None

    def stop(self) -> None:
        self._is_running = False
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
