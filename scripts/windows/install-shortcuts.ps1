# Creates "AI LifeOS" shortcuts (Desktop and Start menu) that start LifeOS with no terminal window,
# plus "Stop AI LifeOS" in the Start menu. Add -Startup to also start LifeOS when you sign in to Windows,
# or -RemoveStartup to undo that.
#   powershell -ExecutionPolicy Bypass -File A:\ai-lifeos\scripts\windows\install-shortcuts.ps1
param([switch]$Startup, [switch]$RemoveStartup)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $here)
$icon = Join-Path $root "frontend\src-tauri\icons\icon.ico"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"
$shell = New-Object -ComObject WScript.Shell

function New-Shortcut($path, $script, $description) {
  $s = $shell.CreateShortcut($path)
  $s.TargetPath = $wscript
  $s.Arguments = '"' + (Join-Path $here $script) + '"'
  $s.WorkingDirectory = $here
  $s.Description = $description
  if (Test-Path $icon) { $s.IconLocation = $icon }
  $s.Save()
  Write-Host "Created $path"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
$startupDir = [Environment]::GetFolderPath("Startup")
New-Shortcut (Join-Path $desktop "AI LifeOS.lnk") "LifeOS.vbs" "Start AI LifeOS"
New-Shortcut (Join-Path $programs "AI LifeOS.lnk") "LifeOS.vbs" "Start AI LifeOS"
New-Shortcut (Join-Path $programs "Stop AI LifeOS.lnk") "StopLifeOS.vbs" "Stop AI LifeOS"
if ($Startup) { New-Shortcut (Join-Path $startupDir "AI LifeOS.lnk") "LifeOS.vbs" "Start AI LifeOS" }
if ($RemoveStartup) {
  $p = Join-Path $startupDir "AI LifeOS.lnk"
  if (Test-Path $p) { Remove-Item $p; Write-Host "Removed $p" }
}
