#!/usr/bin/env bash
# Makes the two LifeOS ISOs from the packages in /repo (built first by build-iso.sh or the "OS image" workflow):
#   AI-LifeOS-<ver>.iso            live system: boots into the 3D apartment; "Install AI LifeOS" installs it
#   AI-LifeOS-Installer-<ver>.iso  installer USB: boots straight into the installer window
# Usage (as root, from the repo):  bash scripts/mkisos.sh <version> <output-dir>
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$1
OUT=$2
mkdir -p "$OUT"
build() {   # name label application overlay
  local name=$1 label=$2 app=$3 overlay=$4 P=/tmp/lifeos-profile W=/tmp/lifeos-work
  rm -rf "$P" "$W" /tmp/lifeos-isoout && cp -r /usr/share/archiso/configs/releng "$P"
  cat os/arch/packages.extra >> "$P/packages.x86_64"
  sort -u -o "$P/packages.x86_64" "$P/packages.x86_64"
  sed -i "/^virtualbox-guest-utils-nox$/d" "$P/packages.x86_64"
  printf '\n[lifeos]\nSigLevel = Optional TrustAll\nServer = file:///repo\n' >> "$P/pacman.conf"
  cp -a os/arch/airootfs/. "$P/airootfs/"
  [ -n "$overlay" ] && cp -a "$overlay/." "$P/airootfs/"
  sed -i "s/^iso_name=.*/iso_name=\"$name\"/; s/^iso_label=.*/iso_label=\"$label\"/; s/^iso_publisher=.*/iso_publisher=\"AI LifeOS\"/; s/^iso_application=.*/iso_application=\"$app\"/" "$P/profiledef.sh"
  sed -i "s/^iso_version=.*/iso_version=\"$VER\"/" "$P/profiledef.sh"
  sed -i 's|file_permissions=(|file_permissions=(\n  ["/etc/sudoers.d/10-live"]="0:0:440"\n  ["/usr/local/bin/lifeos-install"]="0:0:755"\n  ["/usr/lib/lifeos-installer/install.sh"]="0:0:755"\n  ["/usr/lib/lifeos-installer/installer.py"]="0:0:755"|' "$P/profiledef.sh"
  sed -i "s/^airootfs_image_tool_options=.*/airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')/" "$P/profiledef.sh"
  mkarchiso -v -w "$W" -o /tmp/lifeos-isoout "$P"
  rm -rf "$W"
  mv /tmp/lifeos-isoout/*.iso "$OUT/$name-$VER.iso"
  echo "Made $OUT/$name-$VER.iso"
}
build AI-LifeOS LIFEOS "AI LifeOS live system" ""
build AI-LifeOS-Installer LIFEOS_INSTALL "AI LifeOS installer" os/arch/installer-airootfs
