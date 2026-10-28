# Builds the Windows app: Python backend -> lifeos-backend.exe sidecar, then the Tauri tray app and its installer.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File A:\ai-lifeos\scripts\build-windows.ps1 -Install
# -Install runs the installer afterwards with the install folder set to -InstallDir (default A:\AI-LifeOS).
# Needs: Python 3.11+, Node 18+, Rust (rustup, MSVC toolchain), VS Build Tools (C++) and the WebView2 runtime.
# -TauriConfig is the extra Tauri config merged in (the release workflow passes one with the version and updater settings).
param([switch]$Install, [string]$InstallDir = "A:\AI-LifeOS", [string]$TauriConfig = "src-tauri/tauri.release.conf.json")
$ErrorActionPreference = "Stop"
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
  throw "Rust isn't installed. Install it with:  winget install Rustlang.Rustup   then open a new PowerShell window."
}
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$work = Join-Path $root "build"

Write-Host "==> Backend dependencies"
Push-Location $backend
python -m pip install -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Freezing backend with PyInstaller"
python -m PyInstaller --noconfirm --clean --onefile --console --name lifeos-backend `
  --distpath (Join-Path $work "dist") --workpath (Join-Path $work "pyi") --specpath $work `
  --paths $backend `
  --collect-submodules lifeos --collect-submodules uvicorn --collect-all keyring --collect-data tzdata `
  --hidden-import win32ctypes.core `
  (Join-Path $backend "packaging\entry.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Pop-Location

$triple = (rustc -vV | Select-String "^host:").ToString().Split(" ")[1].Trim()
$bin = Join-Path $frontend "src-tauri\binaries"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $work "dist\lifeos-backend.exe") (Join-Path $bin "lifeos-backend-$triple.exe") -Force

Write-Host "==> Building the desktop app"
Push-Location $frontend
if ($env:CI) { npm.cmd ci } else { npm.cmd install }
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
npm.cmd run tauri -- build --config $TauriConfig
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
Pop-Location

$setup = Get-ChildItem (Join-Path $frontend "src-tauri\target\release\bundle\nsis") -Filter *-setup.exe |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "==> Done. Installer: $($setup.FullName)"
if ($Install) {
  Write-Host "==> Installing to $InstallDir (quit AI LifeOS from the tray first if it's running)"
  Start-Process -FilePath $setup.FullName -ArgumentList "/D=$InstallDir" -Wait
}
