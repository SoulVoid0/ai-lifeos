# The LifeOS desktop: the 3D apartment

On the LifeOS operating system (Arch-based ISO), logging in to the **AI LifeOS** session starts KDE Plasma with the
3D apartment full screen on top. The apartment is the desktop; the one-monitor PC in the master bedroom is the real
KDE Plasma desktop.

| | |
|---|---|
| Walk / run / look | WASD or arrows, Shift, mouse |
| Interact | E (talk to an agent, sit on the couch, TV, bed, PC, shop kiosk, whiteboard) |
| Menu (Return to the PC, Settings, Quit) / shop / decorate / help | Esc / P / C / F1 |
| Look without a captured mouse (Hyper-V, VirtualBox) | Left/Right arrows, PgUp/PgDn, or hold the right mouse button and drag |
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

**Settings** (Esc > Settings): graphics quality, mouse sensitivity and invert, mouse look mode (captured, or right-drag
for VMs), walking and turn speed, field of view, frame rate limit (30/45/60/none), "Save power when idle" (5 fps in the
background, 10 fps after a minute without input unless the TV plays), and every movement key can be rebound.
While the apartment is up, KDE's shortcuts (Meta/start menu, Alt+Tab) are blocked: KDE is reached through the PC only.
