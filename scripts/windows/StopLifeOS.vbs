' Stops the AI LifeOS backend started by LifeOS.vbs, plus Ollama/ComfyUI if LifeOS started them.
Option Explicit
Dim http
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1000, 1000, 5000, 5000
http.Open "POST", "http://127.0.0.1:8765/api/system/shutdown", False
http.setRequestHeader "X-LifeOS-Client", "1"
http.Send
If Err.Number <> 0 Then
  MsgBox "AI LifeOS isn't running.", vbInformation, "AI LifeOS"
Else
  MsgBox "AI LifeOS stopped.", vbInformation, "AI LifeOS"
End If
