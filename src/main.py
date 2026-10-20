"""Procedural Keystroke Synth: Main Orchestrator and Application Entry Point.

Coordinates:
- Agent-Input (OS keystroke capture & telemetry)
- Agent-Harmony (Music theory & modal quantization)
- Agent-DSP (Low-latency real-time synthesis)
- Agent-Lifecycle (Tray icon, persistent config & global hotkeys)
- Agent-UI (Modern Cyber-Night CustomTkinter GUI)
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time
from typing import Optional

from src.input.telemetry import InputTelemetry
from src.harmony.engine import HarmonyEngine
from src.dsp.engine import AudioEngine
from src.lifecycle.tray import TrayManager
from src.config.settings import SettingsManager, AppConfig
from src.ui.window import SynthAppWindow


class SynthOrchestrator:
    """Master coordinator linking all subagents via thread-safe IPC queues."""

    def __init__(
        self,
        scale_mode: Optional[str] = None,
        master_volume: Optional[float] = None,
        blocksize: int = 512,
        enable_tray: bool = True,
        settings_manager: Optional[SettingsManager] = None
    ) -> None:
        # Load persistent configuration
        self.settings_manager = (
            settings_manager if settings_manager is not None else SettingsManager()
        )
        cfg = self.settings_manager.config

        self.scale_mode = scale_mode if scale_mode is not None else cfg.current_scale.lower()
        self.master_volume = master_volume if master_volume is not None else cfg.master_volume
        self.blocksize = blocksize
        self.enable_tray = enable_tray

        # Thread-safe bounded IPC queues
        self.input_queue = queue.Queue(maxsize=256)
        self.audio_queue = queue.Queue(maxsize=256)

        # Initialize Subagents
        self.telemetry = InputTelemetry(event_queue=self.input_queue)
        self.harmony = HarmonyEngine(
            input_queue=self.input_queue,
            audio_queue=self.audio_queue,
            scale_mode=self.scale_mode,
            cooldown_seconds=cfg.cooldown_ms / 1000.0
        )
        self.audio = AudioEngine(
            audio_queue=self.audio_queue,
            blocksize=blocksize,
            master_volume=self.master_volume,
            keys_volume=cfg.keys_volume,
            sub_volume=cfg.sub_volume,
            drone_volume=cfg.drone_volume,
            enable_delay=True,
            delay_wet=cfg.delay_mix,
            delay_feedback=cfg.delay_feedback,
            enable_drone=cfg.drone_enabled,
            sound_preset=cfg.current_preset
        )
        if cfg.is_muted:
            self.audio.is_muted = True

        self.tray: Optional[TrayManager] = None
        if enable_tray:
            self.tray = TrayManager(
                telemetry=self.telemetry,
                harmony=self.harmony,
                audio=self.audio,
                on_exit=self.stop,
                settings_manager=self.settings_manager
            )

        self._shutdown_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return

            print("[*] Starting Procedural Keystroke Synth...")
            self.audio.start()
            self.harmony.start()
            self.telemetry.start()
            if self.tray is not None:
                self.tray.start(run_detached=True)

            self._is_running = True
            print("[+] Synth is running! Type anywhere to generate ambient music.")
            print("[!] Hotkeys: [Ctrl + Shift + M] Mute | [Ctrl + Alt + Up/Down] Volume +/- 5%")

    def stop(self) -> None:
        with self._lock:
            if not self._is_running:
                return

            print("\n[-] Initiating graceful shutdown...")
            self._is_running = False

            if self.settings_manager is not None:
                self.settings_manager.flush()

            if self.telemetry is not None:
                self.telemetry.stop()
            if self.harmony is not None:
                self.harmony.stop()
            if self.audio is not None:
                self.audio.stop()
            if self.tray is not None:
                self.tray.stop()

            self._shutdown_event.set()
            print("[+] All audio devices, hooks and worker threads cleanly terminated.")

    def run_forever(self) -> None:
        def handle_signal(sig, frame):
            print(f"\nReceived termination signal ({sig}).")
            self.stop()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        self.start()

        while not self._shutdown_event.is_set():
            try:
                time.sleep(0.2)
            except KeyboardInterrupt:
                self.stop()
                break


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procedural Keystroke Synth — Real-time typing-to-ambient synthesizer."
    )
    parser.add_argument(
        "--scale",
        choices=["dorian", "pentatonic", "lydian", "aeolian", "lofi"],
        default="dorian",
        help="Musical scale mode (default: dorian)"
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=0.85,
        help="Master volume [0.0 .. 1.0] (default: 0.85)"
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=512,
        help="Audio buffer frame size (default: 512 for ~11.6 ms latency)"
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable system tray icon and run exclusively in terminal"
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Disable graphical user interface and run in terminal background mode"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    orchestrator = SynthOrchestrator(
        scale_mode=args.scale,
        master_volume=args.volume,
        blocksize=args.buffer_size,
        enable_tray=not args.no_tray
    )

    if args.no_gui:
        # Headless console / background mode
        orchestrator.run_forever()
    else:
        # GUI mode on Main Thread
        window = SynthAppWindow(
            telemetry=orchestrator.telemetry,
            harmony=orchestrator.harmony,
            audio=orchestrator.audio,
            on_exit=orchestrator.stop,
            settings_manager=orchestrator.settings_manager
        )

        def handle_app_exit():
            def _close_gui():
                try:
                    window.destroy()
                except Exception:
                    pass
                orchestrator.stop()

            try:
                window.after(0, _close_gui)
            except Exception:
                orchestrator.stop()

        if orchestrator.tray is not None:
            orchestrator.tray.on_open_gui = window.restore_from_tray
            orchestrator.tray.on_exit = handle_app_exit

        orchestrator.start()

        try:
            window.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            orchestrator.stop()


if __name__ == "__main__":
    main()
