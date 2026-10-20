@echo off
cd /d "%~dp0"
title Procedural Keystroke Synth
echo [*] Starting Procedural Keystroke Synth...
py -m src.main %*
if %ERRORLEVEL% NEQ 0 (
    echo [!] Process exited with code %ERRORLEVEL%.
    pause
)
