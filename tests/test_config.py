"""Unit and integration tests for Settings Persistence and AppConfig management.

Validates:
- AppConfig schema, serialization, type casting, and defaults;
- SettingsManager auto-initialization when config file does not exist;
- Resilient recovery from corrupted/malformed JSON with .bak creation;
- Atomic save mechanics ensuring zero half-written files;
- Debounced save coalescing rapid slider updates;
- Explicit flush behavior;
- Custom presets library: save, load, delete, and validation;
- Factory reset preserving user presets.
"""

from __future__ import annotations

import json
import os
import time
from typing import Generator
import pytest

from src.config.settings import AppConfig, SettingsManager


@pytest.fixture
def temp_config_path(tmp_path) -> str:
    """Provides an isolated path for configuration testing."""
    return str(tmp_path / "test_config.json")


class TestAppConfigSchema:
    """Verifies AppConfig dataclass behavior and serialization."""

    def test_default_values(self):
        cfg = AppConfig()
        assert cfg.master_volume == 0.70
        assert cfg.keys_volume == 0.80
        assert cfg.sub_volume == 0.60
        assert cfg.drone_enabled is False
        assert cfg.drone_volume == 0.0
        assert cfg.delay_mix == 0.35
        assert cfg.delay_feedback == 0.40
        assert cfg.cooldown_ms == 90
        assert cfg.current_preset == "Warm Rhodes"
        assert cfg.current_scale == "Dorian"
        assert cfg.is_muted is False
        assert cfg.custom_presets == {}

    def test_to_dict_and_from_dict_roundtrip(self):
        cfg = AppConfig(
            master_volume=0.95,
            keys_volume=0.50,
            sub_volume=0.40,
            drone_enabled=True,
            drone_volume=0.25,
            delay_mix=0.60,
            delay_feedback=0.75,
            cooldown_ms=120,
            current_preset="Crystal Bells",
            current_scale="Lydian",
            is_muted=True,
            custom_presets={"Dreamscape": {"keys_volume": 0.5}}
        )
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["master_volume"] == 0.95
        assert d["current_preset"] == "Crystal Bells"

        restored = AppConfig.from_dict(d)
        assert restored.master_volume == 0.95
        assert restored.current_preset == "Crystal Bells"
        assert restored.drone_enabled is True
        assert restored.is_muted is True
        assert "Dreamscape" in restored.custom_presets

    def test_from_dict_partial_and_unknown_fields(self):
        raw = {
            "master_volume": 0.88,
            "current_scale": "Pentatonic",
            "unknown_extra_field": "ignore_me"
        }
        cfg = AppConfig.from_dict(raw)
        assert cfg.master_volume == 0.88
        assert cfg.current_scale == "Pentatonic"
        assert cfg.keys_volume == 0.80  # Default preserved
        assert not hasattr(cfg, "unknown_extra_field")

    def test_from_dict_type_casting_and_fallback(self):
        raw = {
            "master_volume": "0.45",     # Cast string to float
            "cooldown_ms": "150",        # Cast string to int
            "drone_enabled": 1,          # Cast int to bool
            "delay_mix": "invalid_val"   # Invalid float -> should retain default
        }
        cfg = AppConfig.from_dict(raw)
        assert cfg.master_volume == 0.45
        assert cfg.cooldown_ms == 150
        assert cfg.drone_enabled is True
        assert cfg.delay_mix == 0.35     # Retains default on error


class TestSettingsManagerPersistence:
    """Verifies atomic writes, error recovery, and debounced saving."""

    def test_initial_load_creates_file_with_defaults(self, temp_config_path):
        assert not os.path.exists(temp_config_path)

        manager = SettingsManager(config_path=temp_config_path)
        assert os.path.exists(temp_config_path)
        assert manager.config.master_volume == 0.70
        assert manager.config.current_preset == "Warm Rhodes"

        # Verify disk contents are valid JSON
        with open(temp_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["master_volume"] == 0.70

    def test_load_existing_valid_config(self, temp_config_path):
        initial_data = {
            "master_volume": 0.55,
            "keys_volume": 0.65,
            "current_preset": "Lo-Fi Felt Piano",
            "current_scale": "Aeolian"
        }
        with open(temp_config_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        manager = SettingsManager(config_path=temp_config_path)
        assert manager.config.master_volume == 0.55
        assert manager.config.current_preset == "Lo-Fi Felt Piano"
        assert manager.config.current_scale == "Aeolian"
        assert manager.config.sub_volume == 0.60  # Default filled in

    def test_recovery_from_corrupted_json(self, temp_config_path):
        # Write malformed JSON
        with open(temp_config_path, "w", encoding="utf-8") as f:
            f.write("{\n  this is definitely not valid json : [")

        manager = SettingsManager(config_path=temp_config_path)

        # Fallback to default must succeed without crashing
        assert manager.config.master_volume == 0.70
        assert manager.config.current_preset == "Warm Rhodes"

        # Backup file must be created
        backup_path = temp_config_path + ".bak"
        assert os.path.exists(backup_path)
        with open(backup_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "this is definitely not valid json" in content

        # Active file must be restored to valid defaults
        with open(temp_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["master_volume"] == 0.70

    def test_immediate_save(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.config.master_volume = 0.92
        manager.config.is_muted = True
        manager.save()

        # Re-read file to verify persistence
        with open(temp_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["master_volume"] == 0.92
            assert data["is_muted"] is True

    def test_debounced_save(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)

        # Rapidly update values (e.g. simulated slider dragging)
        for v in [0.1, 0.2, 0.3, 0.4, 0.85]:
            manager.config.master_volume = v
            manager.save_debounced(delay=0.10)

        # Immediately after loop, disk file should still have old/initial value
        with open(temp_config_path, "r", encoding="utf-8") as f:
            data_before = json.load(f)
            assert data_before["master_volume"] == 0.70

        # Wait for debounce timer to fire
        time.sleep(0.18)

        # Now disk file must have the latest value (0.85)
        with open(temp_config_path, "r", encoding="utf-8") as f:
            data_after = json.load(f)
            assert data_after["master_volume"] == 0.85

    def test_flush_cancels_timer_and_writes_immediately(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.config.master_volume = 0.42
        manager.save_debounced(delay=5.0)  # Large delay

        # Flush forces immediate save
        manager.flush()
        assert manager._timer is None

        with open(temp_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["master_volume"] == 0.42


class TestCustomPresetsManagement:
    """Verifies saving, loading, deleting, and resetting presets."""

    def test_save_and_load_custom_preset(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.config.keys_volume = 0.45
        manager.config.sub_volume = 0.75
        manager.config.delay_mix = 0.60
        manager.config.delay_feedback = 0.50
        manager.config.cooldown_ms = 130
        manager.config.current_preset = "Crystal Bells"
        manager.config.current_scale = "Lydian"
        manager.config.drone_enabled = True
        manager.config.drone_volume = 0.30

        # Save preset
        manager.save_custom_preset("Midnight Astral")
        assert "Midnight Astral" in manager.config.custom_presets

        # Modify active config to something different
        manager.config.keys_volume = 1.0
        manager.config.current_preset = "8-Bit Chiptune"
        manager.config.drone_enabled = False

        # Load preset back
        success = manager.load_custom_preset("Midnight Astral")
        assert success is True
        assert manager.config.keys_volume == 0.45
        assert manager.config.current_preset == "Crystal Bells"
        assert manager.config.drone_enabled is True
        assert manager.config.drone_volume == 0.30

    def test_save_empty_preset_name_ignored(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.save_custom_preset("   ")
        assert len(manager.config.custom_presets) == 0

    def test_load_non_existent_preset(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        success = manager.load_custom_preset("Ghost Preset")
        assert success is False

    def test_delete_custom_preset(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.save_custom_preset("Cyberpunk")
        assert "Cyberpunk" in manager.config.custom_presets

        deleted = manager.delete_custom_preset("Cyberpunk")
        assert deleted is True
        assert "Cyberpunk" not in manager.config.custom_presets

        deleted_again = manager.delete_custom_preset("Cyberpunk")
        assert deleted_again is False

    def test_reset_to_defaults_preserves_custom_presets(self, temp_config_path):
        manager = SettingsManager(config_path=temp_config_path)
        manager.save_custom_preset("My Sound")

        # Mutate active settings
        manager.config.master_volume = 0.20
        manager.config.keys_volume = 0.15
        manager.config.drone_enabled = True
        manager.config.current_preset = "8-Bit Chiptune"

        # Factory reset
        manager.reset_to_defaults()

        # Active settings restored to defaults
        assert manager.config.master_volume == 0.70
        assert manager.config.keys_volume == 0.80
        assert manager.config.drone_enabled is False
        assert manager.config.current_preset == "Warm Rhodes"

        # Custom presets must remain intact
        assert "My Sound" in manager.config.custom_presets
