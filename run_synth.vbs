' Procedural Keystroke Synth - Silent Background Launcher
' Runs the utility via pythonw without displaying any CMD/PowerShell console window.

Set oShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strDir = fso.GetParentFolderName(WScript.ScriptFullName)
oShell.CurrentDirectory = strDir

' 0 = Hide window, False = Do not wait for script termination
oShell.Run "pythonw.exe -m src.main", 0, False
