#!/usr/bin/env bash
# Builds the AI LifeOS ISOs (live + installer) on any Arch Linux machine (also Arch on WSL), the same way the "OS image" workflow does.
# Run as root from a checkout of the repo:  bash scripts/build-iso.sh [output-dir]   (default /mnt/a/LifeOS-ISO)
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
OUT=${1:-/mnt/a/LifeOS-ISO}
VER=$(date +%Y%m%d%H%M)
[ "$(id -u)" = 0 ] || { echo "Run this as root."; exit 1; }

echo "== Tools"
pacman -Syu --noconfirm --needed archiso base-devel python python-pip nodejs npm git zstd curl sdl2 sdl2_ttf gtk4 pkgconf dbus
id builder >/dev/null 2>&1 || useradd -m builder

echo "== Web UI"
(cd frontend && npm ci --no-audit --no-fund && npm run build)

echo "== Package sources"
S=os/arch/pkg/src-stage
rm -rf "$S" && mkdir -p $S/ollama $S/files
python -m pip install --no-cache-dir --break-system-packages --target $S/site ./backend
cp -a frontend/dist $S/web
cp -a os/files/. $S/files/
base=https://github.com/ollama/ollama/releases/latest/download
if curl -fsSL -o /tmp/o.tar.zst "$base/ollama-linux-amd64.tar.zst"; then zstd -dc /tmp/o.tar.zst | tar -x -C $S/ollama
else curl -fsSL "$base/ollama-linux-amd64.tgz" | tar -xz -C $S/ollama; fi
rm -rf $S/ollama/lib/ollama/cuda_* $S/ollama/lib/ollama/*cuda* 2>/dev/null || true

echo "== 3D apartment"
make -C apartment clean && make -C apartment
cp apartment/lifeos-apartment $S/lifeos-apartment-bin

echo "== Packages"
rm -rf /repo && mkdir -p /repo
chown -R builder os/arch/lsiw os/arch/pkg
(cd os/arch/lsiw && rm -f ./*.pkg.tar.zst && sudo -u builder makepkg -f --nodeps --noconfirm && cp lsiw-*.pkg.tar.zst /repo/)
sed -i "s/^pkgver=.*/pkgver=0.2.$VER/" os/arch/pkg/PKGBUILD
(cd os/arch/pkg && rm -f ./*.pkg.tar.zst && sudo -u builder makepkg -f --nodeps --noconfirm && cp lifeos-*.pkg.tar.zst /repo/)
repo-add /repo/lifeos.db.tar.gz /repo/*.pkg.tar.zst

echo "== ISOs (live and installer)"
bash scripts/mkisos.sh "$VER" "$OUT"
git checkout -- os/arch/pkg/PKGBUILD 2>/dev/null || true

cp /repo/*.pkg.tar.zst "$OUT/"
echo
echo "Done: $OUT/AI-LifeOS-$VER.iso (live) and $OUT/AI-LifeOS-Installer-$VER.iso (installer)"
