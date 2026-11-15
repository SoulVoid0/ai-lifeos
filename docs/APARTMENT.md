# The LifeOS desktop: the 3D apartment

On the LifeOS operating system (Arch-based ISO), logging in to the **AI LifeOS** session starts KDE Plasma with the
3D apartment full screen on top. The apartment is the desktop; the one-monitor PC in the master bedroom is the real
KDE Plasma desktop.

| | |
|---|---|
| Walk / run / look | WASD or arrows, Shift, mouse |
| Interact | E (talk to an agent, sit on the couch, TV, bed, tablet, PC, shop kiosk, whiteboard) |
| Menu (Return to the PC, AI crew, Settings, Quit) / AI crew / shop / decorate / help | Esc / K / P / C / F1 |
| Look | The mouse: the cursor is hidden and held in the middle. In VMs with an absolute mouse (Hyper-V, VirtualBox, QEMU tablet) it's hidden and pushing it against a screen edge keeps turning. Also Left/Right, PgUp/PgDn, or right-drag |
| Windows programs | The tablet on the master bed shows LSIW Control live; E picks it up and you use the real LSIW Control in your hands (Esc or a click outside puts it down) |
| TV | E on the remote (or R on the couch) switches Steam mode (Big Picture) and video mode; Enter on the couch plays Steam |
| Record | E on the camera in the hallway (or Shift+F5 anywhere): records everything on screen, PC and tablet included, with sound, to ~/Videos/LifeOS recordings |
| Graphics quality | F2 (Low renders at half resolution and 30 fps; picked automatically on software rendering) |
| Use the PC | E at the desk: the apartment closes and Plasma is yours |
| Back to the apartment | Shift+F4 on the Plasma desktop (or "Back to the apartment" in the menu) |

**Rooms:** living room (couch you can sit on, TV that plays your own videos from ~/Videos, ~/Movies, ~/Downloads or a
USB stick), master bedroom (bed, PC), kitchen, bathroom, two AI bedrooms (bunk beds, one per agent), work room (a desk
per agent, whiteboard with running missions and your tasks), hallway (shop kiosk, clock).

**The crew** (from `/api/crew/status`) lives there with a routine on the real clock: asleep in their bunks at night
(bedtime ~22:30-23:45 per agent, up ~06:15-07:30), meals in the kitchen around 07:30, 12:15 and 18:30, free time
otherwise. An agent with a task (working, waiting for approval or asking a question) walks to its desk in the work
room. Press E on one for the chat box; Tab switches to Task mode, which starts a mission for that agent and reports
back in the chat when it finishes.

**Points** (`backend/lifeos/apartment.py`, `/api/apartment`): 1 per AI call, 1 per 1000 tokens, 2 per message you
send, 10 per finished task, 20 per finished mission. Spend them on paint, floors, couches, furniture and extras.

**Extra monitors:** every monitor besides the apartment's gets three Plasma widgets once (LifeOS Clock with points,
LifeOS Tasks, LifeOS Crew). Move or remove them in Plasma's edit mode; add them again from "Add Widgets".

**LSIW** is built in with C: at `/`: `C:\home\you` is `/home/you`, `C:\windows` is `/windows`, `C:\Program Files` is
`/Program Files`. `lifeos-enable-wine` installs Wine for programs LSIW's own engine can't run yet.

Code: `apartment/` (C, SDL2, OpenGL 1.x, SDL2_ttf, libcurl), session scripts in `os/files/usr/bin/lifeos-apartment*`,
`lifeos-desktop-setup`, `lifeos-screens`; widgets in `os/files/usr/share/plasma/plasmoids/`; LSIW packaging in
`os/arch/lsiw/`. Try it on any Linux desktop: `make -C apartment && LIFEOS_APARTMENT_WINDOWED=1 apartment/lifeos-apartment`.

**The PC monitor** shows the live KDE desktop and whatever program is open (Esc > Settings > PC screen: Live or
Picture). `apartment/src/pccap.c` gets the window stack from a small KWin script and captures each window through
KWin's ScreenShot2 D-Bus interface (allowed by `/usr/share/applications/lifeos-apartment-bin.desktop`), only while
you're in the master bedroom.

**AI crew panel** (K, or Esc > AI crew): every AI with its model and on/off switch; add one, or open one to change
its name, job title, model, color, max steps, description, personality, speaking style and quirks, or remove it
(built-in AIs can only be switched off). Uses `/api/crew/agents`.

**AI models** (top row of the AI crew panel): the models on this computer (Ollama, built in), a list of good ones to
download with one Enter, or type any Ollama name. Enter twice removes one. A fresh install downloads a small starter
model by itself on first start (the full live ISO already carries it).

**Updates:** `lifeos-update` checks GitHub Releases every 6 hours. A new version drops a letter through the front door
(hallway, opposite the shop); "Update now" installs it over the current one in a terminal (pacman), keeping files,
settings and crew, and the apartment restarts.

**Settings** (Esc > Settings): graphics quality, mouse sensitivity and invert, mouse look (Auto, hidden and locked,
hidden with edge turning for VMs, or right-drag), walking and turn speed, field of view, frame rate limit (30/45/60/none), "Save power when idle" (5 fps in the
background, 10 fps after a minute without input unless the TV plays), screen resolution (through KDE), text and menu
size, PC screen (live or picture), and every movement key can be rebound.
While the apartment is up, KDE's shortcuts (Meta/start menu, Alt+Tab) are blocked: KDE is reached through the PC only.

## Installing

Each release has two ISOs. **AI-LifeOS** is the live system (the apartment; "Install AI LifeOS" is in its app menu, or
run `lifeos-install`). **AI-LifeOS-Installer** boots straight into the installer window
(`os/arch/airootfs/usr/lib/lifeos-installer/`): language, keyboard, time zone, disk (erase it, install in its free
space next to another system, or replace one partition), your account (name, user name, password, computer name,
automatic login), LSIW (off, apps only with a C: drive in each home folder, standard with one shared C: drive in a
folder or partition you choose, or deep with C: at `/`, optionally with the Windows folders on their own partition),
then GRUB with every other system in its menu. `install.sh` copies the untouched system image from the USB stick
and removes the live parts. Both ISOs are made by `scripts/mkisos.sh`.
