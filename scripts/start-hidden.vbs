Option Explicit

Dim shell, fileSystem, scriptDir, powerShell, startScript, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
powerShell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
startScript = fileSystem.BuildPath(scriptDir, "start-dev.ps1")
command = Chr(34) & powerShell & Chr(34) & _
  " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & _
  Chr(34) & startScript & Chr(34)

If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "/dry-run" Then
    WScript.Echo command
    WScript.Quit 0
  End If
End If

' Window style 0 = hidden; False = return immediately so the BAT console can close.
shell.Run command, 0, False
