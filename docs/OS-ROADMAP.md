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
| Session | LifeOS starts at login instead of a normal desktop and stays full screen, while other apps open in normal windows | Next |
| System image | A bootable, installable LifeOS image with drivers, Ollama, ComfyUI and LifeOS preinstalled | After the session |
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
- Wi-Fi, Bluetooth, display and audio settings through NetworkManager, BlueZ, `wlr-randr` and PipeWire, added to
  `system.py` as more tools.
- Notifications through the freedesktop notification spec, so the bell receives other apps' notifications too.

### 3. LifeOS image
- A Fedora bootc (image-mode) base. The image is a container image built in GitHub Actions like the Windows
  installer. `bootc-image-builder` turns it into an installer ISO, and updates are `bootc upgrade` with rollback.
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
