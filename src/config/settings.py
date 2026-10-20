"""Settings and configuration management with atomic writes and debounced auto-saving.

Features:
- Strongly typed AppConfig schema with dataclass defaults;
- Atomic file replacement (tempfile + os.replace) preventing JSON corruption;
- Debounced auto-save (~500ms delay) protecting SSDs from rapid slider dragging;
- Graceful recovery and fallback on corrupted or missing config files;
- Custom user preset management (save, load, delete, reset to defaults).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import tempfile
import threading
from typing import Any, Dict, Optional

# Default root project configuration path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")


@dataclass
class AppConfig:
    """Strongly typed application configuration schema."""
    master_volume: float = 0.70
    keys_volume: float = 0.80
    sub_volume: float = 0.60
    drone_enabled: bool = False
    drone_volume: float = 0.0
    delay_mix: float = 0.35
    delay_feedback: float = 0.40
    cooldown_ms: int = 90
    current_preset: str = "Warm Rhodes"
    current_scale: str = "Dorian"
    is_muted: bool = False
    custom_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Converts config instance to serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppConfig:
        """Constructs AppConfig with type-safety and fallback defaults for missing keys."""
        valid_fields = cls.__dataclass_fields__
        filtered_data = {}
        for k, v in data.items():
            if k in valid_fields:
                t_raw = valid_fields[k].type
                t_str = str(t_raw).lower()
                try:
                    if t_raw is float or t_str == "float":
                        filtered_data[k] = float(v)
                    elif t_raw is int or t_str == "int":
                        filtered_data[k] = int(v)
                    elif t_raw is bool or t_str == "bool":
                        filtered_data[k] = bool(v)
                    elif t_raw is str or t_str == "str":
                        filtered_data[k] = str(v)
                    elif t_raw is dict or "dict" in t_str:
                        filtered_data[k] = dict(v) if isinstance(v, dict) else {}
                    else:
                        filtered_data[k] = v
                except (ValueError, TypeError):
                    pass  # Keep default if cast fails
        return cls(**filtered_data)


class SettingsManager:
    """Thread-safe settings manager with atomic persistence and debounced saves."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path: str = config_path if config_path is not None else DEFAULT_CONFIG_PATH
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self.config: AppConfig = self.load()

    def load(self) -> AppConfig:
        """
        Loads configuration from disk.
        Safely falls back to default AppConfig if file is missing, empty, or corrupted.
        """
        with self._lock:
            if not os.path.exists(self.config_path):
                self.config = AppConfig()
                self._save_atomic(self.config)
                return self.config

            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.config = AppConfig.from_dict(data)
                        return self.config
            except Exception as e:
                # Corrupted file -> create backup and restore defaults
                try:
                    backup_path = self.config_path + ".bak"
                    if os.path.exists(self.config_path):
                        os.replace(self.config_path, backup_path)
                except Exception:
                    pass

            self.config = AppConfig()
            self._save_atomic(self.config)
            return self.config

    def _save_atomic(self, config: AppConfig) -> None:
        """Internal atomic save using tempfile + os.replace to prevent partial writes."""
        dir_name = os.path.dirname(os.path.abspath(self.config_path))
        os.makedirs(dir_name, exist_ok=True)

        payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            dir=dir_name,
            delete=False,
            encoding="utf-8",
            suffix=".tmp"
        )
        temp_path = temp_file.name

        try:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_path, self.config_path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    def save(self, config: Optional[AppConfig] = None) -> None:
        """Immediately and atomically saves config to disk."""
        with self._lock:
            # Cancel any pending debounced timer
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            target = config if config is not None else self.config
            self.config = target
            self._save_atomic(self.config)

    def save_debounced(self, delay: float = 0.5) -> None:
        """
        Schedules an atomic save after `delay` seconds.
        Subsequent calls within the window reset the timer (protects SSD during slider dragging).
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(delay, self._timer_callback)
            self._timer.daemon = True
            self._timer.start()

    def _timer_callback(self) -> None:
        with self._lock:
            self._timer = None
            try:
                self._save_atomic(self.config)
            except Exception:
                pass

    def flush(self) -> None:
        """Forces any pending debounced save to write immediately."""
        self.save()

    # ---------------------------------------------------------
    # Custom Presets Management
    # ---------------------------------------------------------

    def save_custom_preset(self, preset_name: str) -> None:
        """Captures active sound parameters into custom presets library."""
        name = preset_name.strip()
        if not name:
            return

        preset_data = {
            "keys_volume": self.config.keys_volume,
            "sub_volume": self.config.sub_volume,
            "delay_mix": self.config.delay_mix,
            "delay_feedback": self.config.delay_feedback,
            "cooldown_ms": self.config.cooldown_ms,
            "sound_preset": self.config.current_preset,
            "current_scale": self.config.current_scale,
            "drone_enabled": self.config.drone_enabled,
            "drone_volume": self.config.drone_volume
        }
        self.config.custom_presets[name] = preset_data
        self.save()

    def load_custom_preset(self, preset_name: str) -> bool:
        """Applies saved custom preset parameters to current config."""
        name = preset_name.strip()
        if name not in self.config.custom_presets:
            return False

        data = self.config.custom_presets[name]
        self.config.keys_volume = data.get("keys_volume", self.config.keys_volume)
        self.config.sub_volume = data.get("sub_volume", self.config.sub_volume)
        self.config.delay_mix = data.get("delay_mix", self.config.delay_mix)
        self.config.delay_feedback = data.get("delay_feedback", self.config.delay_feedback)
        self.config.cooldown_ms = data.get("cooldown_ms", self.config.cooldown_ms)
        self.config.current_preset = data.get("sound_preset", self.config.current_preset)
        self.config.current_scale = data.get("current_scale", self.config.current_scale)
        self.config.drone_enabled = data.get("drone_enabled", self.config.drone_enabled)
        self.config.drone_volume = data.get("drone_volume", self.config.drone_volume)
        self.save()
        return True

    def delete_custom_preset(self, preset_name: str) -> bool:
        """Deletes a custom preset from the library."""
        name = preset_name.strip()
        if name in self.config.custom_presets:
            del self.config.custom_presets[name]
            self.save()
            return True
        return False

    def reset_to_defaults(self) -> AppConfig:
        """Resets active parameters to factory defaults while preserving custom presets."""
        custom_saved = self.config.custom_presets.copy()
        self.config = AppConfig(custom_presets=custom_saved)
        self.save()
        return self.config
