"""Agent-Lifecycle package: OS integration, system tray, and global hotkeys."""
from src.lifecycle.tray import TrayManager, create_tray_icon

__all__ = ["TrayManager", "create_tray_icon"]
