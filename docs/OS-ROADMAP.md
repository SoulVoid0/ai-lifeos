# From app to operating system

The end goal is a real AI LifeOS: a computer that boots straight into LifeOS. On that computer the assistant, the crew,
your files, apps and automations are the desktop itself, not a program running on top of Windows.

The plan is to finish the app first and to build it so that it can become the OS shell. A whole operating system
written from scratch (kernel, drivers, GPU support) would take years and would still lack drivers for an RX 6800 XT.
LifeOS will therefore do what ChromeOS, SteamOS and Android do: use the Linux kernel and drivers, and replace
everything the user sees.

## Layers

| Layer | What it is | Status |
| --- | --- | --- |
| Brain | Backend (`backend/lifeos`): assistant, crew, memory, files, tasks, automations, approvals and undo | Done, runs on Windows, Linux and macOS |
| Platform layer | `apps.py` (installed apps), `system.py` (status, programs, volume, power), `files.launch`, `services.py`. One function each, with Windows, Linux and macOS versions | Done in 0.1.18 |
| Shell UI | The Tauri app: Computer page (launcher, status, power), Ctrl+K command palette, status bar, full screen with F11 | Done in 0.1.18 |
| Session | LifeOS starts at login instead of a normal desktop and stays full screen, while other apps open in normal windows | Done: labwc session, device settings, notifications, setup screen |
| System image | A bootable, installable LifeOS image with drivers, Ollama, ComfyUI and LifeOS preinstalled | First Arch builds (live USB and installer) |
| Own compositor | A window manager made for LifeOS: the crew can arrange windows, and the Station can be the home screen | Later |

## Why Linux underneath (and what it gives your PC)

- **AMD GPU:** the RX 6800 XT (gfx1030) is supported by ROCm on Linux. Ollama and ComfyUI use it directly there,
  with no ZLUDA and no Vulkan fallback. Image and video generation is typically much faster than on Windows.
- **Atomic updates:** the system is updated as a whole and can roll back after a bad update. This is the same idea as
  the app's signed updates.
- **Total control:** LifeOS can be the only thing on screen at login and can own power, Wi-Fi, audio and displays.
  The platform layer already has Linux versions of these: `.desktop` apps, `wpctl`/`pactl` volume, and
  `systemctl`/`loginctl` power.

## Steps

### 1. App as shell (this release)
- Launcher for the installed apps, with ranking by use and search. The assistant can only open what is installed.
- Assistant tools: `launch_app`, `list_apps`, `system_status`, `list_programs`, `end_program` (asks first),
  `set_volume`, and `power_action` (asks first).
- Offline commands, for example "open Spotify", "volume 30", "mute", "lock my PC", "what's using my RAM",
  "how's my PC".
- Full-screen shell mode (F11), a live status bar and a command palette (Ctrl+K).

### 2. LifeOS session on Linux (started: `packaging/linux/`)
- A Wayland session called "AI LifeOS" that you pick on the login screen. It runs a small stacking compositor
  (labwc), with LifeOS as the full-screen home layer and the dock. Other apps open as normal windows above it.
- The backend runs as a `systemd --user` service, with Ollama and ComfyUI as services.
  Data goes in `~/.local/share/ai-lifeos`, because `config.data_dir` already follows XDG on Linux.
- Done: Wi-Fi, Bluetooth, display and brightness settings (`devices.py`: `nmcli`, `bluetoothctl`, `wlr-randr`,
  `brightnessctl`; `netsh` and WMI on Windows) on the Computer page, and as the assistant tools `wifi`,
  `bluetooth` and `set_brightness`. Audio volume was already in `system.py`.
- Done: notifications through the freedesktop notification spec (`notify_bus.py`), so the bell receives other apps'
  notifications too.
- Done: a first-start setup screen (name, Wi-Fi, folders, the light model set).

### 3. LifeOS image (first build: `os/`)
- An Arch Linux base built with archiso in GitHub Actions: a live USB that boots into LifeOS, plus `lifeos-install`
  (archinstall with the LifeOS package set). LifeOS ships as the `lifeos` pacman package.
- Preinstalled:
  - ROCm.
  - Ollama with the Low-resource model set.
  - ComfyUI with PyTorch ROCm.
  - The LifeOS backend and app.
  - Flatpak, so other apps can be installed.
- First boot: a LifeOS setup screen covering language, Wi-Fi, your name, model downloads and the folders to watch.
  This replaces the usual desktop setup.
- Try it first from a USB stick or alongside Windows (dual boot) before replacing anything.

### 4. LifeOS compositor
- A compositor written in Rust with Smithay, the same language as the Tauri shell, so the AI can see and arrange
  windows.
- What it would allow:
  - "Put my study apps side by side."
  - A focus mode that hides distractions.
  - Window layouts saved as part of workspaces.
  - The Station world as the home screen.

## Rules that carry over

- Everything the AI does goes through the permissioned tool layer. Destructive actions always ask, every action is
  logged, and file changes can be undone.
- Local first: models run on your GPU, and cloud use stays opt-in under a budget.
- The app keeps working on Windows. The OS is an extra way to run the same LifeOS, not a fork.

## Trying the LifeOS image

LifeOS is now built on **Arch Linux** (archiso). The base is small and rolling, so you get the newest Mesa and kernel
for the RX 6800 XT, and the whole system is tailored to LifeOS. LifeOS itself is a pacman package (`lifeos`), so
updating it is `sudo pacman -Syu` once the package repository is online. Build: `.github/workflows/os-image.yml`
(push a commit whose message contains `[os-image]`), with the files in `os/arch/` and `os/files/`.

1. Download the newest `os-arch-N` pre-release `.iso` from the Releases page and write it to a USB stick
   (4 GB or more) with Rufus in DD mode.
2. Boot from the stick (F12/F8 boot menu). It starts straight into LifeOS as the user `lifeos` (password `lifeos`),
   without installing anything, so you can try it safely. Nothing is saved on the stick.
3. To install, boot the AI-LifeOS-Installer ISO (or run `lifeos-install` on the live system). The LifeOS installer asks for the
   language, keyboard, time zone, the disk (erase it, use its free space next to Windows, or replace one partition),
   your account and password, and how LSIW is set up.
4. After restarting, log in. Ollama uses the GPU through Vulkan. For images and video, run `lifeos-install-comfyui`.
