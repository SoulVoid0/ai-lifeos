# Builds the Windows installer: Python backend -> lifeos-backend.exe sidecar, then the Tauri app.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File A:\ai-lifeos\scripts\build-windows.ps1
# Needs: Python 3.11+, Node 18+, Rust (rustup, MSVC toolchain) and the WebView2 runtime.
$ErrorActionPreference = "Stop"
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
npm.cmd install
if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
npm.cmd run tauri -- build --config src-tauri/tauri.release.conf.json
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
Pop-Location

Write-Host "==> Done. Installers:"
Get-ChildItem (Join-Path $frontend "src-tauri\target\release\bundle") -Recurse -Include *.msi, *.exe | ForEach-Object { $_.FullName }
