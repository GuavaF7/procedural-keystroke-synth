"""Automated standalone executable build script using PyInstaller.

Packages Procedural Keystroke Synth into a single, autonomous Windows .exe
(KeystrokeSynth.exe) running without a console window.
"""

import os
import subprocess
import sys


def build():
    print("[*] Starting PyInstaller build for Procedural Keystroke Synth...")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    main_script = os.path.join(base_dir, "src", "main.py")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name=KeystrokeSynth",
        "--onefile",
        "--noconsole",
        "--clean",
        f"--paths={base_dir}",
        "--hidden-import=sounddevice",
        "--hidden-import=numpy",
        "--hidden-import=pynput.keyboard._win32",
        "--hidden-import=pynput.mouse._win32",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL",
        "--collect-all=pynput",
        "--collect-all=pystray",
        "--collect-all=sounddevice",
        "--collect-all=customtkinter",
        "--collect-all=darkdetect",
        main_script
    ]

    print("[*] Command:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=base_dir)

    if result.returncode == 0:
        exe_path = os.path.join(base_dir, "dist", "KeystrokeSynth.exe")
        print("\n" + "=" * 60)
        print(f"[+] Build successful! Standalone executable created:")
        print(f"    -> {exe_path}")
        print("=" * 60)
    else:
        print(f"\n[-] Build failed with exit code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    build()
