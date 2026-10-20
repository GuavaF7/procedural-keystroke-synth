"""Agent-UI: Modern dark cyber-night CustomTkinter interface with Settings Persistence.

Features:
- Full data binding with SettingsManager (AppConfig);
- Real-time WPM speedometer and pulsing audio-reactive visualizer;
- Real-time status cards: Active scale, last played note, buffer latency;
- Master, Keys, Sub-Bass, and Drone Pad volume controls with debounced persistence;
- Delay Wet Mix and Feedback controls;
- Cooldown density threshold slider (40-200 ms);
- Sound preset picker (Warm Rhodes, Crystal Bells, Lo-Fi Felt Piano, 8-Bit Chiptune + Custom User Presets);
- Custom preset creation ("Save Custom Preset...") and factory reset ("Reset to Defaults");
- Musical scale picker (Dorian, Minor Pentatonic, Lydian, Aeolian);
- Minimize-to-tray on close with atomic JSON persistence.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import tkinter as tk
from typing import Callable, List, Optional

import customtkinter as ctk

from src.input.telemetry import InputTelemetry
from src.harmony.engine import HarmonyEngine
from src.dsp.engine import AudioEngine
from src.config.settings import AppConfig, SettingsManager, DEFAULT_CONFIG_PATH

BUILTIN_PRESETS = ["Warm Rhodes", "Crystal Bells", "Lo-Fi Felt Piano", "8-Bit Chiptune"]

SCALE_DISPLAY_MAP = {
    "dorian": "Dorian (Lo-Fi)",
    "pentatonic": "Minor Pentatonic (Space)",
    "lydian": "Lydian (Dreamy)",
    "aeolian": "Aeolian (Melancholy)"
}

REVERSE_SCALE_MAP = {
    "Dorian (Lo-Fi)": "dorian",
    "Minor Pentatonic (Space)": "pentatonic",
    "Lydian (Dreamy)": "lydian",
    "Aeolian (Melancholy)": "aeolian"
}


class SynthAppWindow(ctk.CTk):
    """Main Cyber-Night CustomTkinter GUI Window with persistent state management."""

    def __init__(
        self,
        telemetry: InputTelemetry,
        harmony: HarmonyEngine,
        audio: AudioEngine,
        on_exit: Optional[Callable[[], None]] = None,
        settings_manager: Optional[SettingsManager] = None,
        config_path: str = DEFAULT_CONFIG_PATH
    ) -> None:
        super().__init__()

        self.telemetry = telemetry
        self.harmony = harmony
        self.audio = audio
        self.on_exit_callback = on_exit

        # Initialize Settings Manager
        self.settings_manager = (
            settings_manager if settings_manager is not None else SettingsManager(config_path=config_path)
        )
        self.config: AppConfig = self.settings_manager.config

        # Apply persistent config directly to engines prior to rendering
        self._apply_config_to_engines()

        # Window Appearance & Geometry
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Procedural Keystroke Synth — Cyber-Night Studio")
        self.geometry("980x740")
        self.minsize(900, 680)
        self.configure(fg_color="#0D1117")

        # Visualizer animation state
        self._visualizer_pulse = 0.0
        self._last_event_count = 0

        # Cross-thread command queue for safe UI execution
        self._ui_command_queue = queue.Queue()

        # Build UI layout with loaded initial values
        self._build_header_section()
        self._build_controls_grid()
        self._build_presets_and_footer()

        # Intercept window close (minimize to tray)
        self.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

        # Start periodic GUI update loop (50 ms ~= 20 FPS)
        self.after(50, self._periodic_update)

    def _apply_config_to_engines(self) -> None:
        """Applies loaded AppConfig parameters to DSP and Harmony engines immediately."""
        cfg = self.config
        self.audio.set_master_volume(cfg.master_volume)
        self.audio.set_keys_volume(cfg.keys_volume)
        self.audio.set_sub_volume(cfg.sub_volume)
        self.audio.set_drone_volume(cfg.drone_volume)
        self.audio.enable_drone = cfg.drone_enabled
        self.audio.set_delay_wet(cfg.delay_mix)
        self.audio.set_delay_feedback(cfg.delay_feedback)
        self.audio.set_preset(cfg.current_preset)
        self.audio.is_muted = cfg.is_muted

        self.harmony.set_scale_mode(cfg.current_scale.lower())
        self.harmony.set_cooldown_ms(float(cfg.cooldown_ms))

    # ---------------------------------------------------------
    # UI CONSTRUCTION
    # ---------------------------------------------------------

    def _build_header_section(self) -> None:
        """Top section: WPM Gauge, Pulse Visualizer, Mute Toggle, Status Cards."""
        header_frame = ctk.CTkFrame(self, fg_color="#161B22", corner_radius=12)
        header_frame.pack(fill="x", padx=16, pady=(16, 8))

        # Left: WPM & Title
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=20, pady=16)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="KEYSTROKE SYNTH",
            font=("Consolas", 15, "bold"),
            text_color="#8B949E"
        )
        title_lbl.pack(anchor="w")

        self.wpm_label = ctk.CTkLabel(
            title_box,
            text="0.0 WPM",
            font=("Consolas", 38, "bold"),
            text_color="#00E5A3"
        )
        self.wpm_label.pack(anchor="w")

        # Center: Pulse Wave Canvas
        vis_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        vis_box.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        self.vis_canvas = tk.Canvas(
            vis_box,
            height=64,
            bg="#0D1117",
            highlightthickness=1,
            highlightbackground="#30363D"
        )
        self.vis_canvas.pack(fill="both", expand=True)

        # Right: Status & Mute Button
        right_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        right_box.pack(side="right", padx=20, pady=16)

        self.status_label = ctk.CTkLabel(
            right_box,
            text="Key: D Dorian | Ready",
            font=("Consolas", 13),
            text_color="#C9D1D9"
        )
        self.status_label.pack(anchor="e", pady=(0, 6))

        self.mute_btn = ctk.CTkButton(
            right_box,
            text="SOUND ACTIVE",
            font=("Consolas", 13, "bold"),
            fg_color="#008060",
            hover_color="#00A880",
            width=160,
            height=34,
            command=self.toggle_mute
        )
        self.mute_btn.pack(anchor="e")
        self._update_mute_button_visuals()

    def _build_controls_grid(self) -> None:
        """Middle section: 3-column control cards with pre-bound config values."""
        cfg = self.config
        grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        grid_frame.pack(fill="both", expand=True, padx=16, pady=8)
        grid_frame.columnconfigure((0, 1, 2), weight=1, uniform="col")

        # --- Card 1: Synthesizer Volumes ---
        card1 = ctk.CTkFrame(grid_frame, fg_color="#161B22", corner_radius=12)
        card1.grid(row=0, column=0, sticky="nsew", padx=6, pady=4)

        ctk.CTkLabel(card1, text="SYNTHESIS BUS", font=("Consolas", 14, "bold"), text_color="#00E5A3").pack(anchor="w", padx=16, pady=(14, 8))

        # Master Volume
        self.master_val_lbl = ctk.CTkLabel(card1, text=f"Master: {int(cfg.master_volume * 100)}%", font=("Consolas", 12))
        self.master_val_lbl.pack(anchor="w", padx=16)
        self.master_slider = ctk.CTkSlider(card1, from_=0.0, to=1.0, command=self._on_master_slider)
        self.master_slider.set(cfg.master_volume)
        self.master_slider.pack(fill="x", padx=16, pady=(2, 12))

        # Keys Volume
        self.keys_val_lbl = ctk.CTkLabel(card1, text=f"Keys Notes: {int(cfg.keys_volume * 100)}%", font=("Consolas", 12))
        self.keys_val_lbl.pack(anchor="w", padx=16)
        self.keys_slider = ctk.CTkSlider(card1, from_=0.0, to=1.0, command=self._on_keys_slider)
        self.keys_slider.set(cfg.keys_volume)
        self.keys_slider.pack(fill="x", padx=16, pady=(2, 12))

        # Sub-Bass Volume
        self.sub_val_lbl = ctk.CTkLabel(card1, text=f"Enter Sub-Bass: {int(cfg.sub_volume * 100)}%", font=("Consolas", 12))
        self.sub_val_lbl.pack(anchor="w", padx=16)
        self.sub_slider = ctk.CTkSlider(card1, from_=0.0, to=1.0, command=self._on_sub_slider)
        self.sub_slider.set(cfg.sub_volume)
        self.sub_slider.pack(fill="x", padx=16, pady=(2, 14))

        # --- Card 2: Space & Density FX ---
        card2 = ctk.CTkFrame(grid_frame, fg_color="#161B22", corner_radius=12)
        card2.grid(row=0, column=1, sticky="nsew", padx=6, pady=4)

        ctk.CTkLabel(card2, text="ATMOSPHERE & DELAY", font=("Consolas", 14, "bold"), text_color="#58A6FF").pack(anchor="w", padx=16, pady=(14, 8))

        # Delay Wet
        self.delay_wet_lbl = ctk.CTkLabel(card2, text=f"Ping-Pong Mix: {int(cfg.delay_mix * 100)}%", font=("Consolas", 12))
        self.delay_wet_lbl.pack(anchor="w", padx=16)
        self.delay_wet_slider = ctk.CTkSlider(card2, from_=0.0, to=1.0, command=self._on_delay_wet_slider)
        self.delay_wet_slider.set(cfg.delay_mix)
        self.delay_wet_slider.pack(fill="x", padx=16, pady=(2, 12))

        # Delay Feedback
        self.delay_fb_lbl = ctk.CTkLabel(card2, text=f"Delay Feedback: {int(cfg.delay_feedback * 100)}%", font=("Consolas", 12))
        self.delay_fb_lbl.pack(anchor="w", padx=16)
        self.delay_fb_slider = ctk.CTkSlider(card2, from_=0.0, to=0.85, command=self._on_delay_fb_slider)
        self.delay_fb_slider.set(cfg.delay_feedback)
        self.delay_fb_slider.pack(fill="x", padx=16, pady=(2, 12))

        # Cooldown / Density Threshold
        self.cooldown_lbl = ctk.CTkLabel(card2, text=f"Note Cooldown: {int(cfg.cooldown_ms)} ms", font=("Consolas", 12))
        self.cooldown_lbl.pack(anchor="w", padx=16)
        self.cooldown_slider = ctk.CTkSlider(card2, from_=40.0, to=200.0, command=self._on_cooldown_slider)
        self.cooldown_slider.set(cfg.cooldown_ms)
        self.cooldown_slider.pack(fill="x", padx=16, pady=(2, 14))

        # --- Card 3: Adaptive Ambient Drone ---
        card3 = ctk.CTkFrame(grid_frame, fg_color="#161B22", corner_radius=12)
        card3.grid(row=0, column=2, sticky="nsew", padx=6, pady=4)

        ctk.CTkLabel(card3, text="AMBIENT DRONE PAD", font=("Consolas", 14, "bold"), text_color="#D2A8FF").pack(anchor="w", padx=16, pady=(14, 8))

        # Drone Enable Toggle
        self.drone_switch = ctk.CTkSwitch(
            card3,
            text="Enable Sub-Pad",
            font=("Consolas", 12),
            command=self._on_drone_toggle
        )
        if cfg.drone_enabled:
            self.drone_switch.select()
        else:
            self.drone_switch.deselect()
        self.drone_switch.pack(anchor="w", padx=16, pady=(6, 12))

        # Drone Volume
        self.drone_val_lbl = ctk.CTkLabel(card3, text=f"Pad Volume: {int(cfg.drone_volume * 100)}%", font=("Consolas", 12))
        self.drone_val_lbl.pack(anchor="w", padx=16)
        self.drone_slider = ctk.CTkSlider(card3, from_=0.0, to=1.0, command=self._on_drone_slider)
        self.drone_slider.set(cfg.drone_volume)
        self.drone_slider.pack(fill="x", padx=16, pady=(2, 10))

        # Info callout about idle fade-out
        info_box = ctk.CTkLabel(
            card3,
            text="* Idle Fade-out: Drone automatically fades to absolute 0.0 in silence (>2s pause).",
            font=("Consolas", 10),
            text_color="#8B949E",
            wraplength=220,
            justify="left"
        )
        info_box.pack(anchor="w", padx=16, pady=(4, 10))

    def _build_presets_and_footer(self) -> None:
        """Bottom section: Sound Presets, Scales, Custom Preset Actions, Minimize to Tray, Exit."""
        cfg = self.config
        footer_frame = ctk.CTkFrame(self, fg_color="#161B22", corner_radius=12)
        footer_frame.pack(fill="x", padx=16, pady=(8, 16))

        # Top row inside footer: Presets and Scale selection
        selectors_row = ctk.CTkFrame(footer_frame, fg_color="transparent")
        selectors_row.pack(fill="x", padx=16, pady=(12, 6))

        # Sound Presets Dropdown
        ctk.CTkLabel(selectors_row, text="Sound Preset:", font=("Consolas", 12, "bold"), text_color="#C9D1D9").pack(side="left", padx=(0, 6))
        all_presets = self._get_combined_preset_list()
        self.preset_menu = ctk.CTkOptionMenu(
            selectors_row,
            values=all_presets,
            command=self._on_preset_change,
            width=180
        )
        if cfg.current_preset in all_presets:
            self.preset_menu.set(cfg.current_preset)
        else:
            self.preset_menu.set(all_presets[0])
        self.preset_menu.pack(side="left", padx=(0, 16))

        # Scale Modes Dropdown
        ctk.CTkLabel(selectors_row, text="Scale Mode:", font=("Consolas", 12, "bold"), text_color="#C9D1D9").pack(side="left", padx=(0, 6))
        self.scale_menu = ctk.CTkOptionMenu(
            selectors_row,
            values=["Dorian (Lo-Fi)", "Minor Pentatonic (Space)", "Lydian (Dreamy)", "Aeolian (Melancholy)"],
            command=self._on_scale_change,
            width=210
        )
        self.scale_menu.set(SCALE_DISPLAY_MAP.get(cfg.current_scale.lower(), "Dorian (Lo-Fi)"))
        self.scale_menu.pack(side="left")

        # Bottom row inside footer: Preset management & App actions
        actions_row = ctk.CTkFrame(footer_frame, fg_color="transparent")
        actions_row.pack(fill="x", padx=16, pady=(6, 12))

        # Custom Presets Buttons
        save_preset_btn = ctk.CTkButton(
            actions_row,
            text="Save Custom Preset...",
            fg_color="#1F6FEB",
            hover_color="#388BFD",
            width=170,
            command=self.save_custom_preset_dialog
        )
        save_preset_btn.pack(side="left", padx=(0, 8))

        reset_btn = ctk.CTkButton(
            actions_row,
            text="Reset to Defaults",
            fg_color="#30363D",
            hover_color="#484F58",
            width=140,
            command=self.reset_to_defaults
        )
        reset_btn.pack(side="left", padx=(0, 8))

        # App Actions: Minimize & Exit
        exit_btn = ctk.CTkButton(
            actions_row,
            text="Exit",
            fg_color="#8B1111",
            hover_color="#A81515",
            width=80,
            command=self.quit_application
        )
        exit_btn.pack(side="right", padx=(6, 0))

        tray_btn = ctk.CTkButton(
            actions_row,
            text="Minimize to Tray",
            fg_color="#21262D",
            hover_color="#30363D",
            width=130,
            command=self.minimize_to_tray
        )
        tray_btn.pack(side="right", padx=(0, 6))

    def _get_combined_preset_list(self) -> List[str]:
        """Returns builtin presets followed by saved user custom presets."""
        custom_names = list(self.config.custom_presets.keys())
        return BUILTIN_PRESETS + [f"Custom: {name}" for name in custom_names]

    def _refresh_preset_dropdown(self) -> None:
        """Refreshes options in preset dropdown."""
        all_presets = self._get_combined_preset_list()
        self.preset_menu.configure(values=all_presets)

    # ---------------------------------------------------------
    # CALLBACKS & EVENT HANDLERS
    # ---------------------------------------------------------

    def _on_master_slider(self, val: float) -> None:
        self.audio.set_master_volume(val)
        self.config.master_volume = val
        self.master_val_lbl.configure(text=f"Master: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_keys_slider(self, val: float) -> None:
        self.audio.set_keys_volume(val)
        self.config.keys_volume = val
        self.keys_val_lbl.configure(text=f"Keys Notes: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_sub_slider(self, val: float) -> None:
        self.audio.set_sub_volume(val)
        self.config.sub_volume = val
        self.sub_val_lbl.configure(text=f"Enter Sub-Bass: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_delay_wet_slider(self, val: float) -> None:
        self.audio.set_delay_wet(val)
        self.config.delay_mix = val
        self.delay_wet_lbl.configure(text=f"Ping-Pong Mix: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_delay_fb_slider(self, val: float) -> None:
        self.audio.set_delay_feedback(val)
        self.config.delay_feedback = val
        self.delay_fb_lbl.configure(text=f"Delay Feedback: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_cooldown_slider(self, val: float) -> None:
        self.harmony.set_cooldown_ms(val)
        self.config.cooldown_ms = int(val)
        self.cooldown_lbl.configure(text=f"Note Cooldown: {int(val)} ms")
        self.settings_manager.save_debounced()

    def _on_drone_toggle(self) -> None:
        is_on = self.drone_switch.get() == 1
        self.audio.enable_drone = is_on
        self.config.drone_enabled = is_on
        self.settings_manager.save_debounced()

    def _on_drone_slider(self, val: float) -> None:
        self.audio.set_drone_volume(val)
        self.config.drone_volume = val
        self.drone_val_lbl.configure(text=f"Pad Volume: {int(val * 100)}%")
        self.settings_manager.save_debounced()

    def _on_preset_change(self, preset: str) -> None:
        if preset.startswith("Custom: "):
            custom_name = preset.replace("Custom: ", "", 1)
            loaded = self.settings_manager.load_custom_preset(custom_name)
            if loaded:
                self._apply_config_to_engines()
                self._update_all_slider_positions()
        else:
            self.audio.set_preset(preset)
            self.config.current_preset = preset
            self.settings_manager.save_debounced()

    def _on_scale_change(self, choice: str) -> None:
        mode = REVERSE_SCALE_MAP.get(choice, "dorian")
        self.harmony.set_scale_mode(mode)
        self.config.current_scale = mode.capitalize()
        self.settings_manager.save_debounced()

    def toggle_mute(self) -> None:
        self.audio.toggle_mute()
        self.config.is_muted = self.audio.is_muted
        self._update_mute_button_visuals()
        self.settings_manager.save_debounced()

    def _update_mute_button_visuals(self) -> None:
        if self.audio.is_muted:
            self.mute_btn.configure(
                text="MUTED",
                fg_color="#FF3B30",
                hover_color="#D32F2F"
            )
        else:
            self.mute_btn.configure(
                text="SOUND ACTIVE",
                fg_color="#008060",
                hover_color="#00A880"
            )

    def _update_all_slider_positions(self) -> None:
        """Synchronizes GUI sliders with active AppConfig (after preset load or factory reset)."""
        cfg = self.config
        self.master_slider.set(cfg.master_volume)
        self.master_val_lbl.configure(text=f"Master: {int(cfg.master_volume * 100)}%")

        self.keys_slider.set(cfg.keys_volume)
        self.keys_val_lbl.configure(text=f"Keys Notes: {int(cfg.keys_volume * 100)}%")

        self.sub_slider.set(cfg.sub_volume)
        self.sub_val_lbl.configure(text=f"Enter Sub-Bass: {int(cfg.sub_volume * 100)}%")

        self.delay_wet_slider.set(cfg.delay_mix)
        self.delay_wet_lbl.configure(text=f"Ping-Pong Mix: {int(cfg.delay_mix * 100)}%")

        self.delay_fb_slider.set(cfg.delay_feedback)
        self.delay_fb_lbl.configure(text=f"Delay Feedback: {int(cfg.delay_feedback * 100)}%")

        self.cooldown_slider.set(cfg.cooldown_ms)
        self.cooldown_lbl.configure(text=f"Note Cooldown: {int(cfg.cooldown_ms)} ms")

        if cfg.drone_enabled:
            self.drone_switch.select()
        else:
            self.drone_switch.deselect()

        self.drone_slider.set(cfg.drone_volume)
        self.drone_val_lbl.configure(text=f"Pad Volume: {int(cfg.drone_volume * 100)}%")

        self.scale_menu.set(SCALE_DISPLAY_MAP.get(cfg.current_scale.lower(), "Dorian (Lo-Fi)"))

    # ---------------------------------------------------------
    # CUSTOM PRESET MANAGEMENT DIALOGS
    # ---------------------------------------------------------

    def save_custom_preset_dialog(self) -> None:
        """Prompts user to name and save current slider configuration as a preset."""
        dialog = ctk.CTkInputDialog(
            text="Enter custom preset name:",
            title="Save Custom Preset"
        )
        name = dialog.get_input()
        if name and name.strip():
            clean_name = name.strip()
            self.settings_manager.save_custom_preset(clean_name)
            self._refresh_preset_dropdown()
            self.preset_menu.set(f"Custom: {clean_name}")

    def reset_to_defaults(self) -> None:
        """Resets active parameters to factory defaults while preserving custom presets."""
        self.config = self.settings_manager.reset_to_defaults()
        self._apply_config_to_engines()
        self._update_all_slider_positions()
        self.preset_menu.set(self.config.current_preset)

    # ---------------------------------------------------------
    # ANIMATION LOOP & PERIODIC REFRESH
    # ---------------------------------------------------------

    def _periodic_update(self) -> None:
        try:
            # 0. Drain cross-thread UI requests
            self._drain_ui_queue()

            # 1. Update WPM
            wpm = self.telemetry.calculate_wpm()
            self.wpm_label.configure(text=f"{wpm:.1f} WPM")

            # Pulse trigger on new event
            current_event_count = self.harmony._arpeggio_step
            if current_event_count != self._last_event_count:
                self._visualizer_pulse = 1.0
                self._last_event_count = current_event_count

            # 2. Update Status Card
            root = self.harmony.current_root_name
            mode = self.harmony.scale_mode.capitalize()
            note = self.harmony.last_note_name
            self.status_label.configure(text=f"Scale: {root} {mode} | Note: {note}")

            # 3. Animate Visualizer Waveform
            self._render_canvas_wave()

            # 4. Sync Mute Button with global hotkey state
            self._update_mute_button_visuals()

        except Exception:
            pass

        self.after(50, self._periodic_update)

    def _drain_ui_queue(self) -> None:
        """Executes queued cross-thread UI actions safely on the main thread."""
        while not self._ui_command_queue.empty():
            try:
                cmd = self._ui_command_queue.get_nowait()
                cmd()
            except Exception:
                break

    def _render_canvas_wave(self) -> None:
        """Draws animated neon cyberpunk wave across canvas."""
        canvas = self.vis_canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()

        if w <= 10 or h <= 10:
            return

        canvas.delete("all")

        self._visualizer_pulse = max(0.08, self._visualizer_pulse * 0.88)
        amplitude = (h * 0.40) * self._visualizer_pulse
        mid_y = h / 2.0

        points = []
        phase_offset = time.monotonic() * 4.0

        for x in range(0, w, 4):
            rel_x = x / float(w)
            y = mid_y + math.sin(rel_x * 8.0 * math.pi + phase_offset) * amplitude
            points.append((x, y))

        if len(points) > 1:
            color = "#FF3B30" if self.audio.is_muted else "#00E5A3"
            flat_pts = [coord for pt in points for coord in pt]
            canvas.create_line(flat_pts, fill=color, width=2, smooth=True)

    def minimize_to_tray(self) -> None:
        """Flushes pending saves and hides window into tray."""
        self.settings_manager.flush()
        self.withdraw()

    def restore_from_tray(self) -> None:
        """Thread-safe window restoration from background tray thread."""
        def _do_restore() -> None:
            try:
                self.deiconify()
                self.state("normal")
                self.lift()
                self.attributes("-topmost", True)
                self.after_idle(self.attributes, "-topmost", False)
                self.focus_force()
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            _do_restore()
        else:
            self._ui_command_queue.put(_do_restore)
            try:
                self.after(0, _do_restore)
            except Exception:
                pass

    def quit_application(self) -> None:
        """Flushes pending saves and gracefully terminates application."""
        self.settings_manager.flush()
        self.destroy()
        if self.on_exit_callback is not None:
            self.on_exit_callback()
