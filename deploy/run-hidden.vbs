' run-hidden.vbs -- zero-window launcher for console commands.
'
' Scheduled tasks registered with an InteractiveToken principal and a console
' executable (powershell.exe, cmd.exe, bash.exe) open a visible, focus-stealing
' console window in the user's session every time they fire. Launching the same
' command through wscript.exe (a GUI-subsystem host) with window style 0 never
' creates a console window at all -- unlike "powershell -WindowStyle Hidden",
' which still flashes the console host before the argument is parsed.
'
' Contract
'   Arguments : <command> [args...]
'               Preconditions: at least one argument; no argument may contain a
'               literal double quote (WScript strips quoting while parsing, so
'               such an argument cannot be rebuilt losslessly -- refuse rather
'               than silently corrupt the command line).
'   Behaviour : runs the command hidden (SW_HIDE) and waits for it to finish,
'               so the host process lives as long as the child. The scheduled
'               task's MultipleInstancesPolicy and Running state stay accurate.
'   Exit code : the child's exit code; 87 (ERROR_INVALID_PARAMETER) on a
'               violated argument contract.
'
' Installed onto fleet hosts by install-hidden-task-launcher.ps1.
Option Explicit

Dim shell, command, arg, i, exitCode

If WScript.Arguments.Count = 0 Then
  WScript.Quit(87)
End If

command = ""
For i = 0 To WScript.Arguments.Count - 1
  arg = WScript.Arguments(i)
  If InStr(arg, Chr(34)) > 0 Then
    WScript.Quit(87)
  End If
  If InStr(arg, " ") > 0 Then
    arg = Chr(34) & arg & Chr(34)
  End If
  If i = 0 Then
    command = arg
  Else
    command = command & " " & arg
  End If
Next

Set shell = CreateObject("WScript.Shell")
exitCode = shell.Run(command, 0, True)
WScript.Quit(exitCode)
