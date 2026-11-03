# Logging in on the first text console starts LifeOS, in case the graphical login screen didn't start.
if [ -z "$WAYLAND_DISPLAY" ] && [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ] && [ "$(id -u)" -ge 1000 ]; then
  exec lifeos-session
fi
