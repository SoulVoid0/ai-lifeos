#!/bin/sh
# Installs the LifeOS login session on a Linux PC (stage 2 of docs/OS-ROADMAP.md). Run with sudo from this folder.
set -e
command -v labwc >/dev/null || { echo "Install labwc first (Fedora: dnf install labwc, Arch: pacman -S labwc)"; exit 1; }
install -m 755 lifeos-session /usr/local/bin/lifeos-session
install -m 644 ai-lifeos.desktop /usr/share/wayland-sessions/ai-lifeos.desktop
install -D -m 644 lifeos-backend.service /usr/lib/systemd/user/lifeos-backend.service
echo "Done. Log out and pick 'AI LifeOS' on the login screen."
