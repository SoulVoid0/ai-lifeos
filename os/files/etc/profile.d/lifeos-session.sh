# Logging in on the first text console starts LifeOS, in case the graphical login screen didn't start.
# Only for real text-console logins: SDDM's Wayland sessions also run a login shell on tty1 (XDG_SESSION_TYPE=wayland).
if [ "${XDG_SESSION_TYPE:-tty}" = "tty" ] && [ -z "$WAYLAND_DISPLAY" ] && [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ] && [ "$(id -u)" -ge 1000 ]; then
  lifeos-session || echo "Type lifeos-session to try again."
fi
