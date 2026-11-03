' AI LifeOS launcher without a terminal window (no build needed).
' Starts the backend hidden (it starts Ollama, and ComfyUI when needed), then opens LifeOS in an app window.
' Double-click this file, or use the shortcuts made by install-shortcuts.ps1.
Option Explicit
Dim shell, fso, here, backendDir, logFile, url, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
backendDir = fso.GetAbsolutePathName(fso.BuildPath(here, "..\..\backend"))
logFile = shell.ExpandEnvironmentStrings("%APPDATA%") & "\AI-LifeOS\backend.log"
url = "http://127.0.0.1:8765/"

Function Healthy()
  Dim http
  Healthy = False
  On Error Resume Next
  Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  http.setTimeouts 500, 500, 1000, 1000
  http.Open "GET", url & "api/health", False
  http.Send
  If Err.Number = 0 Then Healthy = (http.Status = 200)
  On Error GoTo 0
End Function

If Not Healthy() Then
  If Not fso.FolderExists(shell.ExpandEnvironmentStrings("%APPDATA%") & "\AI-LifeOS") Then
    fso.CreateFolder shell.ExpandEnvironmentStrings("%APPDATA%") & "\AI-LifeOS"
  End If
  shell.CurrentDirectory = backendDir
  ' Window style 0 = hidden. Output goes to %APPDATA%\AI-LifeOS\backend.log.
  shell.Run "cmd /c python -m lifeos >> """ & logFile & """ 2>&1", 0, False
  For i = 1 To 60
    WScript.Sleep 500
    If Healthy() Then Exit For
  Next
  If Not Healthy() Then
    MsgBox "AI LifeOS didn't start. Check " & logFile, vbExclamation, "AI LifeOS"
    WScript.Quit 1
  End If
End If

' Edge's app mode gives LifeOS its own window without browser tabs or address bar.
On Error Resume Next
shell.Run "msedge --app=" & url, 1, False
If Err.Number <> 0 Then
  Err.Clear
  shell.Run url, 1, False
End If
