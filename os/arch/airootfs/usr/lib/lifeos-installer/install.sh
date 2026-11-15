#!/bin/bash
# AI LifeOS installer back end. Run as root by the installer window:  install.sh /path/to/choices.conf
# Copies the live system (the untouched image on the USB stick, not the running copy) onto the chosen disk,
# turns it into an installed system (no live user, autologin or live services), sets language, keyboard, time
# zone, computer name, the user's account and LSIW, and installs the GRUB boot loader (with other systems in its
# menu). Prints "PROGRESS <percent> <text>" lines for the window; anything else is log.
set -Eeuo pipefail
CONF=$1
# shellcheck disable=SC1090
. "$CONF"
rm -f "$CONF"   # it holds the passwords

T=/mnt/lifeos-target
say() { echo "PROGRESS $1 $2"; }
fail() { echo "ERROR ${1:-The installation stopped (line ${BASH_LINENO[0]}).}"; exit 1; }
trap 'fail' ERR
log() { echo "  $*"; }

part_path() { case "$1" in *[0-9]) echo "${1}p$2" ;; *) echo "$1$2" ;; esac; }
settle() { partprobe "$1" 2>/dev/null || true; udevadm settle; sleep 1; }
newest_part() {   # the partition of $1 that was not in the list $2
  for p in $(lsblk -lnpo NAME,TYPE "$1" | awk '$2=="part"{print $1}'); do
    case " $2 " in *" $p "*) ;; *) echo "$p"; return ;; esac
  done
}
parts_of() { lsblk -lnpo NAME,TYPE "$1" | awk '$2=="part"{print $1}' | tr '\n' ' '; }
ESP_TYPE=c12a7328-f81f-11d2-ba4b-00a0c93ec93b

say 1 "Getting ready"
umount -R "$T" 2>/dev/null || true
swapoff -a 2>/dev/null || true
mkdir -p "$T"

# ---------------------------------------------------------------- the system image to copy
SRC=""
for d in /run/archiso/sfs/airootfs /run/archiso/airootfs; do [ -d "$d/usr/bin" ] && SRC=$d && break; done
if [ -z "$SRC" ]; then
  img=$(ls /run/archiso/copytoram/airootfs.sfs /run/archiso/bootmnt/arch/x86_64/airootfs.sfs 2>/dev/null | head -n 1 || true)
  [ -n "$img" ] || fail "The LifeOS system image wasn't found on the USB stick."
  mkdir -p /run/lifeos-sfs && mount -o ro,loop "$img" /run/lifeos-sfs && SRC=/run/lifeos-sfs
fi
log "image: $SRC"

# ---------------------------------------------------------------- partitions
say 3 "Preparing the disk"
ESP=""; ROOT=""; BIOSDISK="$DISK"
if [ "$MODE" = erase ]; then
  for p in $(parts_of "$DISK"); do umount "$p" 2>/dev/null || true; done
  wipefs -af "$DISK" >/dev/null
  sgdisk --zap-all "$DISK" >/dev/null
  if [ "$UEFI" = 1 ]; then
    sgdisk -n1:0:+1G -t1:ef00 -c1:EFI -n2:0:0 -t2:8304 -c2:LifeOS "$DISK" >/dev/null
  else
    sgdisk -n1:0:+1M -t1:ef02 -c1:BIOS -n2:0:0 -t2:8304 -c2:LifeOS "$DISK" >/dev/null
  fi
  settle "$DISK"
  [ "$UEFI" = 1 ] && ESP=$(part_path "$DISK" 1) && mkfs.fat -F 32 -n EFI "$ESP" >/dev/null
  ROOT=$(part_path "$DISK" 2)
else
  table=$(blkid -o value -s PTTYPE "$DISK" 2>/dev/null || true)
  [ -n "$table" ] || fail "This disk has no partition table. Choose \"Erase the disk\" for it instead."
  if [ "$MODE" = free ]; then
    # the largest free area
    read -r fstart fend < <(parted -s -m "$DISK" unit MiB print free | awk -F: '$5 ~ /free/ {gsub("MiB","",$2); gsub("MiB","",$3); s=$3-$2; if (s>best) {best=s; a=$2; b=$3}} END {printf "%d %d\n", a+1, b-1}')
    [ $((fend - fstart)) -ge 20000 ] || fail "There isn't 20 GB of free space on this disk. Make room first (for example shrink Windows' partition in Windows Disk Management) or choose another option."
    if [ "$UEFI" = 1 ] && ! lsblk -lnpo PARTTYPE "$DISK" | grep -qi "$ESP_TYPE" && ! lsblk -lnpo PARTTYPE | grep -qi "$ESP_TYPE"; then
      before=$(parts_of "$DISK")
      parted -s "$DISK" unit MiB mkpart EFI fat32 "$fstart" $((fstart + 512)) set "$(parted -s -m "$DISK" print | tail -n 1 | cut -d: -f1)" esp on >/dev/null 2>&1 || true
      settle "$DISK"
      ESP=$(newest_part "$DISK" "$before")
      [ -n "$ESP" ] && mkfs.fat -F 32 -n EFI "$ESP" >/dev/null
      fstart=$((fstart + 513))
    fi
    if [ "$UEFI" != 1 ] && [ "$table" = gpt ] && ! lsblk -lnpo PARTTYPE "$DISK" | grep -qi 21686148-6449-6e6f-744e-656564454649; then
      before=$(parts_of "$DISK")
      parted -s "$DISK" unit MiB mkpart BIOS "$fstart" $((fstart + 2)) >/dev/null
      settle "$DISK"
      bp=$(newest_part "$DISK" "$before")
      [ -n "$bp" ] && parted -s "$DISK" set "${bp##*[!0-9]}" bios_grub on >/dev/null
      fstart=$((fstart + 3))
    fi
    before=$(parts_of "$DISK")
    if [ "$table" = gpt ]; then parted -s "$DISK" unit MiB mkpart LifeOS ext4 "$fstart" "$fend" >/dev/null
    else parted -s "$DISK" unit MiB mkpart primary ext4 "$fstart" "$fend" >/dev/null; fi
    settle "$DISK"
    ROOT=$(newest_part "$DISK" "$before")
    [ -n "$ROOT" ] || fail "The new partition didn't appear."
  else
    ROOT=$PART
    umount "$ROOT" 2>/dev/null || true
  fi
  if [ "$UEFI" = 1 ] && [ -z "$ESP" ]; then
    ESP=$(lsblk -lnpo NAME,PARTTYPE "$DISK" | awk -v t="$ESP_TYPE" 'tolower($2)==t {print $1; exit}')
    [ -n "$ESP" ] || ESP=$(lsblk -lnpo NAME,PARTTYPE | awk -v t="$ESP_TYPE" 'tolower($2)==t {print $1; exit}')
    [ -n "$ESP" ] || fail "No EFI system partition was found, and there was no room to make one."
  fi
fi
log "root: $ROOT  efi: ${ESP:-none}"
wipefs -af "$ROOT" >/dev/null
mkfs.ext4 -F -L LifeOS "$ROOT" >/dev/null 2>&1
mount "$ROOT" "$T"
if [ -n "$ESP" ]; then mkdir -p "$T/boot/efi" && mount "$ESP" "$T/boot/efi"; fi

LSIW_MOUNT=""
if [ "$LSIW_TIER" = standard ] || [ "$LSIW_TIER" = deep ]; then
  if [ "$LSIW_WHERE" = partition ] && [ -n "$LSIW_PART" ]; then
    umount "$LSIW_PART" 2>/dev/null || true
    wipefs -af "$LSIW_PART" >/dev/null
    mkfs.ext4 -F -L LSIW "$LSIW_PART" >/dev/null 2>&1
    LSIW_MOUNT=/lsiw
    mkdir -p "$T/lsiw" && mount "$LSIW_PART" "$T/lsiw"
  fi
fi

# ---------------------------------------------------------------- copy the system
say 8 "Copying LifeOS to the disk"
total=$(du -sxm "$SRC" 2>/dev/null | cut -f1 || echo 6000)
rsync -aAXH --numeric-ids --info=progress2 --no-inc-recursive \
  --exclude=/proc/ --exclude=/sys/ --exclude=/dev/ --exclude=/run/ --exclude=/tmp/ --exclude=/mnt/ \
  "$SRC"/ "$T"/ 2>&1 | tr '\r' '\n' | { last=-1; while read -r line; do
    if [[ $line =~ \ ([0-9]+)%\  ]] && [ "${BASH_REMATCH[1]}" != "$last" ]; then
      last=${BASH_REMATCH[1]}
      say $((8 + last * 64 / 100)) "Copying LifeOS to the disk ($last%)"
    elif [[ $line != *%* && -n ${line// /} ]]; then
      log "$line"
    fi
  done; }
mkdir -p "$T"/{proc,sys,dev,run,tmp,mnt}
chmod 1777 "$T/tmp"
log "copied about ${total} MB"

# ---------------------------------------------------------------- remove the live system's parts
say 74 "Setting up the installed system"
cd "$T"
rm -f etc/sddm.conf.d/zz-live-autologin.conf etc/sudoers.d/10-live etc/xdg/autostart/lifeos-installer.desktop \
      etc/mkinitcpio.conf.d/archiso.conf etc/motd etc/ssh/sshd_config.d/10-archiso.conf \
      etc/systemd/journald.conf.d/volatile-storage.conf etc/systemd/logind.conf.d/do-not-suspend.conf \
      etc/systemd/resolved.conf.d/archiso.conf etc/systemd/networkd.conf.d/ipv6-privacy-extensions.conf \
      etc/systemd/network/20-ethernet.network etc/systemd/network/20-wlan.network etc/systemd/network/20-wwan.network \
      etc/systemd/system-generators/systemd-gpt-auto-generator etc/systemd/system/etc-pacman.d-gnupg.mount \
      etc/systemd/system/choose-mirror.service etc/systemd/system/livecd-alsa-unmuter.service \
      etc/systemd/system/livecd-talk.service etc/systemd/system/pacman-init.service \
      root/.automated_script.sh root/.zlogin usr/local/bin/choose-mirror usr/local/bin/Installation_guide \
      usr/local/bin/livecd-sound usr/local/bin/lifeos-install usr/local/bin/lifeos-installer \
      usr/share/applications/lifeos-installer.desktop
rm -rf etc/systemd/system/getty@tty1.service.d usr/local/share/livecd-sound usr/lib/lifeos-installer opt/lifeos-repo \
       etc/systemd/system/cloud-init.target.wants etc/systemd/system/systemd-networkd-wait-online.service.d
for w in choose-mirror iwd livecd-talk pacman-init sshd systemd-networkd systemd-resolved vboxservice vmtoolsd \
         vmware-vmblock-fuse hv_fcopy_daemon hv_kvp_daemon hv_vss_daemon; do
  rm -f "etc/systemd/system/multi-user.target.wants/$w.service"
done
rm -f etc/systemd/system/network-online.target.wants/systemd-networkd-wait-online.service \
      etc/systemd/system/sockets.target.wants/systemd-networkd.socket etc/systemd/system/sound.target.wants/livecd-alsa-unmuter.service \
      etc/systemd/system/dbus-org.freedesktop.network1.service etc/systemd/system/dbus-org.freedesktop.resolve1.service
rm -f etc/resolv.conf && touch etc/resolv.conf
sed -i '/^\[lifeos\]/,+2d; /^NoExtract = usr\/share\/\(doc\|locale\)/d' etc/pacman.conf
cd /

# ---------------------------------------------------------------- file systems and swap
genfstab -U "$T" > "$T/etc/fstab"
# genfstab falls back to device names when udev hasn't seen a new file system yet; device names change between boots
for dev in "$ROOT" "$ESP" "${LSIW_PART:-}"; do
  [ -n "$dev" ] || continue
  uuid=$(blkid -p -s UUID -o value "$dev" 2>/dev/null || true)
  [ -n "$uuid" ] && sed -i "s#^$dev[[:space:]]#UUID=$uuid\t#" "$T/etc/fstab"
done
if [ "${SWAP_GB:-0}" -gt 0 ]; then
  mkswap -U clear --size "${SWAP_GB}G" --file "$T/swapfile" >/dev/null 2>&1 || {
    dd if=/dev/zero of="$T/swapfile" bs=1M count=$((SWAP_GB * 1024)) status=none && chmod 600 "$T/swapfile" && mkswap "$T/swapfile" >/dev/null; }
  echo "/swapfile none swap defaults 0 0" >> "$T/etc/fstab"
fi

# ---------------------------------------------------------------- language, keyboard, time, name
say 77 "Language, keyboard and time zone"
sed -i "s/^#\(${LOCALE//./\\.} \)/\1/; s/^#\(en_US\.UTF-8 \)/\1/" "$T/etc/locale.gen"
echo "LANG=$LOCALE" > "$T/etc/locale.conf"
arch-chroot "$T" locale-gen >/dev/null
echo "KEYMAP=$KEYMAP" > "$T/etc/vconsole.conf"
mkdir -p "$T/etc/X11/xorg.conf.d" "$T/etc/xdg"
printf 'Section "InputClass"\n  Identifier "system-keyboard"\n  MatchIsKeyboard "on"\n  Option "XkbLayout" "%s"\n%sEndSection\n' \
  "$XKB" "${XKB_VARIANT:+  Option \"XkbVariant\" \"$XKB_VARIANT\"\n}" > "$T/etc/X11/xorg.conf.d/00-keyboard.conf"
printf '[Layout]\nLayoutList=%s\nVariantList=%s\nUse=true\n' "$XKB" "${XKB_VARIANT:-}" > "$T/etc/xdg/kxkbrc"
ln -sf "/usr/share/zoneinfo/$TZ" "$T/etc/localtime"
arch-chroot "$T" hwclock --systohc >/dev/null 2>&1 || true
echo "$HOSTNAME_NEW" > "$T/etc/hostname"
printf '127.0.0.1 localhost\n::1 localhost\n127.0.1.1 %s.localdomain %s\n' "$HOSTNAME_NEW" "$HOSTNAME_NEW" > "$T/etc/hosts"

# ---------------------------------------------------------------- the account
say 80 "Creating your account"
arch-chroot "$T" useradd -m -G wheel,audio,video,input -s /bin/bash -c "$FULLNAME" "$USERNAME"
printf '%s:%s\n' "$USERNAME" "$PASSWORD" | arch-chroot "$T" chpasswd
if [ "${ROOT_SAME:-0}" = 1 ]; then printf 'root:%s\n' "$PASSWORD" | arch-chroot "$T" chpasswd
else arch-chroot "$T" passwd -l root >/dev/null; fi
echo '%wheel ALL=(ALL:ALL) ALL' > "$T/etc/sudoers.d/10-wheel" && chmod 440 "$T/etc/sudoers.d/10-wheel"
mkdir -p "$T/var/lib/lifeos" && touch "$T/var/lib/lifeos/firstuser-done"
if [ "${AUTOLOGIN:-0}" = 1 ]; then
  printf '[Autologin]\nUser=%s\nSession=ai-lifeos-apartment\n' "$USERNAME" > "$T/etc/sddm.conf.d/20-autologin.conf"
fi

# ---------------------------------------------------------------- LSIW (Windows programs)
say 83 "Setting up LSIW"
lsiw_conf() {
  mkdir -p "$T/etc/lsiw"
  {
    echo "# LSIW system-wide configuration on LifeOS (/etc/lsiw/lsiw.conf), chosen in the installer: $LSIW_TIER."
    echo "# Each user's ~/.config/lsiw/lsiw.conf is read after this file and wins. The full list: lsiw help config"
    echo
    echo "[engine]"
    echo "#default = auto"
    echo
    echo "[drive]"
    echo "c_drive = $1"
    # the folders Windows expects right under C:\ live in the chosen C: (LSIW's own defaults are in the home folder)
    local r=${1%/}
    echo
    echo "[variables]"
    echo "WINDIR = $r/windows"
    echo "CSIDL_PROGRAM_FILES = $r/Program Files"
    echo "CSIDL_PROGRAM_FILESX86 = $r/Program Files (x86)"
    echo "CSIDL_COMMON_APPDATA = $r/ProgramData"
    echo "PROFILESFOLDER = $r/users"
  } > "$T/etc/lsiw/lsiw.conf"
}
WINFOLDERS=("windows" "Program Files" "Program Files (x86)" "ProgramData" "users")
drop_root_folders() { for f in "${WINFOLDERS[@]}"; do rmdir "$T/$f" 2>/dev/null || true; done; }
mkdir -p "$T/etc/tmpfiles.d" "$T/etc/binfmt.d"
case "$LSIW_TIER" in
  off)
    arch-chroot "$T" pacman -Rdd --noconfirm lsiw >/dev/null 2>&1 || true
    rm -f "$T/usr/lib/tmpfiles.d/lifeos-lsiw.conf" "$T/usr/bin/lifeos-enable-wine"
    drop_root_folders
    ;;
  light)
    lsiw_conf '${HOME}/.cdrive'
    ln -sf /dev/null "$T/etc/tmpfiles.d/lifeos-lsiw.conf"
    ln -sf /dev/null "$T/etc/binfmt.d/zz-lsiw.conf"
    drop_root_folders
    ;;
  standard)
    where=${LSIW_MOUNT:-${LSIW_PATH:-/lsiw}}
    lsiw_conf "$where"
    mkdir -p "$T$where" && chmod 1777 "$T$where"
    ln -sf /dev/null "$T/etc/tmpfiles.d/lifeos-lsiw.conf"
    drop_root_folders
    ;;
  deep|*)
    lsiw_conf /
    if [ -n "$LSIW_MOUNT" ]; then
      # the Windows folders under C:\ (= /) live on their own partition
      : > "$T/etc/tmpfiles.d/lifeos-lsiw.conf"
      for f in "${WINFOLDERS[@]}"; do
        rmdir "$T/$f" 2>/dev/null || rm -rf "${T:?}/$f"
        mkdir -p "$T/lsiw/$f" && chmod 1777 "$T/lsiw/$f"
        ln -s "/lsiw/$f" "$T/$f"
        printf 'd "/lsiw/%s" 1777 root root -\n' "$f" >> "$T/etc/tmpfiles.d/lifeos-lsiw.conf"
      done
    fi
    ;;
esac

# ---------------------------------------------------------------- kernel, initramfs, boot loader
say 86 "Making the system bootable"
kdir=""
for d in "$T"/usr/lib/modules/*; do [ "$(cat "$d/pkgbase" 2>/dev/null)" = linux ] && kdir=$d; done
[ -n "$kdir" ] || kdir=$(ls -d "$T"/usr/lib/modules/* | head -n 1)
install -Dm644 "$kdir/vmlinuz" "$T/boot/vmlinuz-linux"
if [ ! -f "$T/etc/mkinitcpio.d/linux.preset" ] || grep -q archiso "$T/etc/mkinitcpio.d/linux.preset"; then
  printf "ALL_kver=\"/boot/vmlinuz-linux\"\nPRESETS=('default' 'fallback')\ndefault_image=\"/boot/initramfs-linux.img\"\nfallback_image=\"/boot/initramfs-linux-fallback.img\"\nfallback_options=\"-S autodetect\"\n" > "$T/etc/mkinitcpio.d/linux.preset"
fi
arch-chroot "$T" mkinitcpio -P >/dev/null 2>&1 || arch-chroot "$T" mkinitcpio -p linux
say 92 "Installing the boot loader"
sed -i 's/^#\?GRUB_DISABLE_OS_PROBER=.*/GRUB_DISABLE_OS_PROBER=false/; s/^GRUB_DISTRIBUTOR=.*/GRUB_DISTRIBUTOR="AI LifeOS"/; s/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=4/' "$T/etc/default/grub"
grep -q '^GRUB_DISABLE_OS_PROBER' "$T/etc/default/grub" || echo 'GRUB_DISABLE_OS_PROBER=false' >> "$T/etc/default/grub"
if [ "$UEFI" = 1 ]; then
  # some firmware refuses new boot entries: then rely on the fallback path alone
  arch-chroot "$T" grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=LifeOS --recheck ||
    arch-chroot "$T" grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=LifeOS --no-nvram --removable
  # firmware that forgets its boot entries (and most VMs) start the fallback path; only claim it on our own disk
  [ "$MODE" = erase ] && arch-chroot "$T" grub-install --target=x86_64-efi --efi-directory=/boot/efi --removable >/dev/null 2>&1 || true
else
  arch-chroot "$T" grub-install --target=i386-pc --recheck "$BIOSDISK"
fi
arch-chroot "$T" grub-mkconfig -o /boot/grub/grub.cfg 2>&1 | sed 's/^/  /'

say 96 "Finishing"
arch-chroot "$T" pacman-key --init >/dev/null 2>&1 || true
arch-chroot "$T" pacman-key --populate archlinux >/dev/null 2>&1 || true
sync
umount -R "$T" || umount -lR "$T"
say 100 "Done"
echo "DONE"
