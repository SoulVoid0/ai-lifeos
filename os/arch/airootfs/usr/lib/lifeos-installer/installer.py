#!/usr/bin/env python3
"""AI LifeOS installer: a step-by-step window (GTK 4) that installs LifeOS from the USB stick onto a disk.

Steps: language, keyboard, time zone, disk (erase it, use its free space next to another system, or replace one
partition), account (name, user name, password, computer name), LSIW (how deeply Windows programs are built in and
where their C: drive lives), summary, then the copy itself (install.sh, run as root through sudo).
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "install.sh")
UEFI = os.path.isdir("/sys/firmware/efi")

LANGUAGES = [
    ("English (United States)", "en_US.UTF-8", "us"), ("English (United Kingdom)", "en_GB.UTF-8", "gb"),
    ("Svenska", "sv_SE.UTF-8", "se"), ("Deutsch", "de_DE.UTF-8", "de"), ("Français", "fr_FR.UTF-8", "fr"),
    ("Español", "es_ES.UTF-8", "es"), ("Italiano", "it_IT.UTF-8", "it"), ("Nederlands", "nl_NL.UTF-8", "nl"),
    ("Norsk bokmål", "nb_NO.UTF-8", "no"), ("Dansk", "da_DK.UTF-8", "dk"), ("Suomi", "fi_FI.UTF-8", "fi"),
    ("Polski", "pl_PL.UTF-8", "pl"), ("Português (Brasil)", "pt_BR.UTF-8", "br"), ("Português", "pt_PT.UTF-8", "pt"),
    ("Čeština", "cs_CZ.UTF-8", "cz"), ("Magyar", "hu_HU.UTF-8", "hu"), ("Türkçe", "tr_TR.UTF-8", "tr"),
    ("Русский", "ru_RU.UTF-8", "ru"), ("Українська", "uk_UA.UTF-8", "ua"), ("Ελληνικά", "el_GR.UTF-8", "gr"),
    ("Japanese", "ja_JP.UTF-8", "jp"), ("Korean", "ko_KR.UTF-8", "kr"), ("Chinese (Simplified)", "zh_CN.UTF-8", "cn"),
    ("العربية", "ar_SA.UTF-8", "ara"), ("हिन्दी", "hi_IN.UTF-8", "in"),
]
# XKB layout -> Linux console keymap, where the names differ
CONSOLE_KEYMAPS = {"gb": "uk", "se": "sv-latin1", "dk": "dk-latin1", "no": "no-latin1", "de": "de-latin1",
                   "fr": "fr-latin1", "es": "es", "it": "it", "nl": "nl", "fi": "fi", "pl": "pl", "br": "br-abnt2",
                   "pt": "pt-latin1", "cz": "cz-qwertz", "hu": "hu", "tr": "trq", "ru": "ru", "ua": "ua",
                   "gr": "gr", "jp": "jp106", "ch": "sg-latin1", "be": "be-latin1", "ca": "cf", "us": "us"}

LSIW_TIERS = [
    ("off", "Off", "No Windows programs. LSIW is left out of the system (the tablet in the apartment does nothing)."),
    ("light", "Apps only", "Each user gets a private C: drive inside their home folder (~/.cdrive). Windows programs "
     "only run when you start them from LSIW or open them in the file manager; nothing is added to the rest of the system."),
    ("standard", "Standard", "One shared C: drive in a place you choose (a folder, or its own partition). .exe files "
     "start from the file manager and the terminal like any program."),
    ("deep", "Deep (recommended for LifeOS)", "Windows programs see the whole system: C: is the root of the file system "
     "(C:\\home\\you is /home/you), and Windows, Program Files, ProgramData and users are real folders in /. .exe files "
     "start from anywhere. The Windows folders can live on their own partition."),
]


def run(cmd, root=False):
    if root:
        cmd = ["sudo", "-n"] + cmd
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def boot_disk():
    """The USB stick LifeOS runs from: never offered as a target."""
    src = run(["findmnt", "-no", "SOURCE", "/run/archiso/bootmnt"]).strip()
    if not src:
        return ""
    parent = run(["lsblk", "-no", "PKNAME", src]).strip().splitlines()
    return parent[0] if parent and parent[0] else src.rsplit("/", 1)[-1]


def disks():
    out = run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,MODEL,TRAN,RO,FSTYPE,LABEL,PARTTYPE,PKNAME"])
    try:
        tree = json.loads(out)["blockdevices"]
    except (ValueError, KeyError):
        return []
    skip = boot_disk()
    res = []
    for d in tree:
        if d.get("type") != "disk" or d.get("ro") in (True, "1", 1) or d.get("name", "").startswith(("zram", "loop", "sr")):
            continue
        if skip and (d.get("name") == skip or d.get("path") == skip or d.get("path") == "/dev/" + skip):
            continue
        if int(d.get("size") or 0) < 8e9:
            continue
        parts = [p for p in d.get("children") or [] if p.get("type") == "part"]
        res.append({"path": d["path"], "size": int(d["size"]), "model": (d.get("model") or "").strip(),
                    "tran": d.get("tran") or "", "parts": parts})
    return res


def free_space(disk):
    out = run(["parted", "-s", "-m", disk, "unit", "B", "print", "free"], root=True)
    best = 0
    for line in out.splitlines():
        f = line.rstrip(";").split(":")
        if len(f) >= 5 and f[4] == "free":
            try:
                best = max(best, int(f[3].rstrip("B")))
            except ValueError:
                pass
    return best


def xkb_layouts():
    res = []
    try:
        with open("/usr/share/X11/xkb/rules/base.lst", encoding="utf-8") as f:
            section = ""
            for line in f:
                if line.startswith("!"):
                    section = line[1:].strip()
                    continue
                if section == "layout" and line.strip():
                    code, _, name = line.strip().partition(" ")
                    res.append((code, name.strip()))
    except OSError:
        res = [(c, c) for c in sorted(set(CONSOLE_KEYMAPS))]
    return sorted(res, key=lambda x: x[1].lower())


def timezones():
    out = run(["timedatectl", "list-timezones"])
    zones = [z for z in out.split() if "/" in z or z == "UTC"]
    return zones or ["UTC", "Europe/Stockholm", "Europe/London", "America/New_York"]


def label(text, cls=None, wrap=True, xalign=0.0):
    l = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    l.set_wrap_mode(2)  # word-char
    if cls:
        l.add_css_class(cls)
    return l


class Installer(Gtk.ApplicationWindow):
    STEPS = ["Language", "Keyboard", "Time zone", "Disk", "Account", "Windows programs", "Summary", "Installing"]

    def __init__(self, app):
        super().__init__(application=app, title="Install AI LifeOS")
        self.set_default_size(1000, 640)
        self.locale, self.xkb = "en_US.UTF-8", "us"
        self.tz = "UTC"
        self.disk = None
        self.mode = "erase"
        self.part = None
        self.lsiw_tier = "deep"
        self.lsiw_where = "folder"
        self.lsiw_part = None
        self.step = 0
        self.installing = False

        css = Gtk.CssProvider()
        css.load_from_string(
            ".title{font-size:24px;font-weight:700;margin-bottom:6px}.dim{opacity:.7}.warn{color:#e5534b;font-weight:600}"
            ".side{padding:24px 18px;background:alpha(@theme_fg_color,0.06)}.stepnow{font-weight:700}.stepdone{opacity:.6}"
            ".stepnext{opacity:.4}.page{padding:28px 36px}.big{font-size:15px}")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        side.add_css_class("side")
        side.append(label("AI LifeOS", "title"))
        self.step_labels = []
        for s in self.STEPS:
            l = label(s)
            side.append(l)
            self.step_labels.append(l)
        root.append(side)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True, hexpand=True)
        main.append(self.stack)
        nav = Gtk.Box(spacing=10, margin_top=10, margin_bottom=16, margin_start=36, margin_end=36)
        self.error = label("", "warn")
        self.error.set_hexpand(True)
        nav.append(self.error)
        self.back = Gtk.Button(label="Back")
        self.back.connect("clicked", lambda *_: self.go(-1))
        self.next = Gtk.Button(label="Next")
        self.next.add_css_class("suggested-action")
        self.next.connect("clicked", lambda *_: self.go(1))
        nav.append(self.back)
        nav.append(self.next)
        main.append(nav)
        root.append(main)
        self.set_child(root)

        self.pages = [self.page_language(), self.page_keyboard(), self.page_timezone(), self.page_disk(),
                      self.page_account(), self.page_lsiw(), self.page_summary(), self.page_install()]
        for i, p in enumerate(self.pages):
            # every page scrolls, so Back/Next stay on screen even at 1024x768
            outer = Gtk.ScrolledWindow(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            outer.set_child(p)
            self.stack.add_named(outer, str(i))
        self.show_step()

    # ------------------------------------------------------------ helpers
    def page(self, title, intro):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("page")
        box.append(label(title, "title"))
        if intro:
            box.append(label(intro, "dim"))
        return box

    def listbox(self, rows, on_select, selected=None):
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        for key, text in rows:
            r = Gtk.ListBoxRow()
            r.key = key
            r.set_child(label(text, wrap=False))
            lb.append(r)
            if key == selected:
                lb.select_row(r)
        lb.connect("row-selected", lambda _lb, r: r and on_select(r.key))
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=220)
        sw.set_child(lb)
        return sw, lb

    # ------------------------------------------------------------ pages
    def page_language(self):
        p = self.page("Welcome", "This installs AI LifeOS on this computer. Pick the language for the system "
                                 "(the installer itself is in English).")
        sw, _ = self.listbox([(i, l[0]) for i, l in enumerate(LANGUAGES)], self.set_language, 0)
        p.append(sw)
        return p

    def set_language(self, i):
        _name, self.locale, xkb = LANGUAGES[i]
        if hasattr(self, "kb_list"):
            self.select_key(self.kb_list, xkb)

    def page_keyboard(self):
        p = self.page("Keyboard", "Choose your keyboard layout. Try it in the box below.")
        self.layouts = xkb_layouts()
        sw, self.kb_list = self.listbox(self.layouts, self.set_keyboard, "us")
        p.append(sw)
        p.append(Gtk.Entry(placeholder_text="Type here to test the layout"))
        return p

    def set_keyboard(self, code):
        self.xkb = code
        # switch the live session's layout too, so the account page types what the keys say
        subprocess.Popen(["sh", "-c", f"kwriteconfig6 --file kxkbrc --group Layout --key LayoutList {shlex.quote(code)} && "
                                      "kwriteconfig6 --file kxkbrc --group Layout --key Use true && "
                                      "dbus-send --session --type=signal /Layouts org.kde.keyboard.reloadConfig"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def select_key(self, lb, key):
        r = lb.get_first_child()
        while r:
            if getattr(r, "key", None) == key:
                lb.select_row(r)
                break
            r = r.get_next_sibling()

    def page_timezone(self):
        p = self.page("Time zone", "Where are you? The clock and the crew's daily routine follow this.")
        search = Gtk.SearchEntry(placeholder_text="Search, for example Stockholm")
        p.append(search)
        zones = timezones()
        guess = run(["sh", "-c", "curl -fsS -m 3 https://ipapi.co/timezone 2>/dev/null"]).strip()
        if guess in zones:
            self.tz = guess
        sw, lb = self.listbox([(z, z.replace("_", " ")) for z in zones], lambda z: setattr(self, "tz", z), self.tz)
        lb.set_filter_func(lambda r: search.get_text().lower().replace(" ", "_") in r.key.lower())
        search.connect("search-changed", lambda *_: lb.invalidate_filter())
        p.append(sw)
        return p

    def page_disk(self):
        p = self.page("Disk", "Where should LifeOS go?")
        self.disk_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        p.append(self.disk_box)
        modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8)
        self.r_erase = Gtk.CheckButton(label="Erase the whole disk and install LifeOS")
        self.r_free = Gtk.CheckButton(label="Install next to the other systems, in the disk's free space", group=self.r_erase)
        self.r_part = Gtk.CheckButton(label="Replace one partition (only that partition is erased)", group=self.r_erase)
        self.r_erase.set_active(True)
        for r, m in ((self.r_erase, "erase"), (self.r_free, "free"), (self.r_part, "part")):
            r.connect("toggled", lambda b, m=m: b.get_active() and self.set_mode(m))
            modes.append(r)
        p.append(modes)
        self.part_drop = Gtk.DropDown()
        self.part_drop.set_visible(False)
        self.part_drop.connect("notify::selected", lambda *_: self.update_disk_info())
        p.append(self.part_drop)
        self.disk_info = label("", "dim")
        p.append(self.disk_info)
        p.append(label("Booting: " + ("UEFI" if UEFI else "BIOS (legacy)") + ". Next to Windows, shrink Windows' "
                       "partition first in Windows' Disk Management (at least 20 GB free) and choose the second option; "
                       "a menu then lets you pick the system at every start.", "dim"))
        return p

    def refresh_disks(self):
        c = self.disk_box.get_first_child()
        while c:
            n = c.get_next_sibling()
            self.disk_box.remove(c)
            c = n
        self.disk_list = disks()
        if not self.disk_list:
            self.disk_box.append(label("No disk of at least 8 GB was found (the USB stick LifeOS runs from isn't shown).", "warn"))
            self.disk = None
            return
        group = None
        for d in self.disk_list:
            name = f"{d['path']}   {human(d['size'])}   {d['model'] or 'disk'}" + (f" ({d['tran'].upper()})" if d['tran'] else "")
            parts = ", ".join(f"{(q.get('label') or q.get('fstype') or 'partition')} {human(int(q.get('size') or 0))}" for q in d["parts"])
            b = Gtk.CheckButton(label=name + (f"\n      contains: {parts}" if parts else "\n      empty"), group=group)
            group = group or b
            b.connect("toggled", lambda btn, d=d: btn.get_active() and self.set_disk(d))
            self.disk_box.append(b)
            if self.disk is None or self.disk["path"] == d["path"]:
                b.set_active(True)
                self.set_disk(d)

    def set_disk(self, d):
        self.disk = d
        self.free = None
        names = [f"{q['path']}  {q.get('label') or ''} {q.get('fstype') or 'no file system'}  {human(int(q.get('size') or 0))}"
                 for q in d["parts"]]
        self.part_drop.set_model(Gtk.StringList.new(names or ["(no partitions)"]))
        self.update_disk_info()

    def set_mode(self, m):
        self.mode = m
        self.update_disk_info()

    def update_disk_info(self):
        self.part_drop.set_visible(self.mode == "part")
        if not self.disk:
            return
        if self.mode == "erase":
            self.disk_info.set_text(f"Everything on {self.disk['path']} is deleted.")
        elif self.mode == "free":
            if self.free is None:
                self.free = free_space(self.disk["path"])
            self.disk_info.set_text(f"Largest free area: {human(self.free)} (LifeOS needs 20 GB). Nothing else is changed.")
        else:
            i = self.part_drop.get_selected()
            self.part = self.disk["parts"][i]["path"] if self.disk["parts"] and i < len(self.disk["parts"]) else None
            self.disk_info.set_text(f"{self.part} is erased; the other partitions are kept." if self.part else "This disk has no partitions.")

    def page_account(self):
        p = self.page("Your account", "You log in with this, and it's the administrator account (sudo).")
        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        self.e_full = Gtk.Entry(placeholder_text="Your name")
        self.e_user = Gtk.Entry(placeholder_text="lower case, no spaces")
        self.e_pw = Gtk.PasswordEntry(show_peek_icon=True)
        self.e_pw2 = Gtk.PasswordEntry(show_peek_icon=True)
        self.e_host = Gtk.Entry(text="lifeos-pc")
        self.c_auto = Gtk.CheckButton(label="Log in automatically (straight into the apartment)")
        self.c_root = Gtk.CheckButton(label="Use the same password for the root account (otherwise root is locked)")
        self.user_touched = False
        self.e_user.connect("changed", lambda *_: setattr(self, "user_touched", self.e_user.has_focus() or self.user_touched))
        self.e_full.connect("changed", self.suggest_user)
        rows = [("Your name", self.e_full), ("User name", self.e_user), ("Password", self.e_pw),
                ("Password again", self.e_pw2), ("Computer name", self.e_host)]
        for i, (t, w) in enumerate(rows):
            grid.attach(label(t), 0, i, 1, 1)
            w.set_hexpand(True)
            grid.attach(w, 1, i, 1, 1)
        p.append(grid)
        p.append(self.c_auto)
        p.append(self.c_root)
        return p

    def suggest_user(self, *_):
        if self.user_touched:
            return
        first = (self.e_full.get_text().strip().split() or [""])[0].lower()
        self.e_user.set_text(re.sub(r"[^a-z0-9_-]", "", first)[:32])
        if first and self.e_host.get_text() in ("lifeos-pc", ""):
            self.e_host.set_text("lifeos-pc")

    def page_lsiw(self):
        p = self.page("Windows programs (LSIW)", "LSIW runs Windows programs on LifeOS. Choose how deeply it's built in "
                                                  "and where its C: drive is kept.")
        group = None
        self.tier_buttons = {}
        for key, name, desc in LSIW_TIERS:
            b = Gtk.CheckButton(group=group)
            group = group or b
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(label(name, "big"))
            box.append(label(desc, "dim"))
            b.set_child(box)
            b.connect("toggled", lambda btn, k=key: btn.get_active() and self.set_tier(k))
            self.tier_buttons[key] = b
            p.append(b)
        p.append(Gtk.Separator(margin_top=6))
        self.where_title = label("Where LSIW keeps its files", "big")
        p.append(self.where_title)
        self.w_folder = Gtk.CheckButton(label="A folder on the LifeOS partition")
        self.w_part = Gtk.CheckButton(label="Its own partition (erased and formatted)", group=self.w_folder)
        self.w_folder.set_active(True)
        self.w_folder.connect("toggled", lambda b: b.get_active() and self.set_where("folder"))
        self.w_part.connect("toggled", lambda b: b.get_active() and self.set_where("partition"))
        self.e_lsiw_path = Gtk.Entry(text="/lsiw")
        self.lsiw_drop = Gtk.DropDown()
        row = Gtk.Box(spacing=10)
        row.append(self.w_folder)
        row.append(self.e_lsiw_path)
        p.append(row)
        row2 = Gtk.Box(spacing=10)
        row2.append(self.w_part)
        row2.append(self.lsiw_drop)
        p.append(row2)
        self.where_note = label("", "dim")
        p.append(self.where_note)
        self.tier_buttons["deep"].set_active(True)
        self.set_tier("deep")
        return p

    def set_tier(self, k):
        self.lsiw_tier = k
        on = k in ("standard", "deep")
        for w in (self.where_title, self.w_folder, self.w_part, self.e_lsiw_path, self.lsiw_drop):
            w.set_sensitive(on)
        self.e_lsiw_path.set_sensitive(k == "standard" and self.lsiw_where == "folder")
        notes = {"off": "", "light": "Each user's C: drive is ~/.cdrive in their home folder.",
                 "standard": "C: is the folder (or the partition, mounted at /lsiw).",
                 "deep": "C: is / either way. With a partition, Windows, Program Files, ProgramData and users are kept on it "
                         "(mounted at /lsiw and linked into /)."}
        self.where_note.set_text(notes[k])

    def set_where(self, w):
        self.lsiw_where = w
        self.set_tier(self.lsiw_tier)

    def refresh_lsiw_parts(self):
        """Partitions that can hold LSIW's files: not the ones this install uses."""
        self.lsiw_parts = []
        for d in disks():
            for q in d["parts"]:
                if self.mode == "erase" and d["path"] == self.disk["path"]:
                    continue
                if self.mode == "part" and q["path"] == self.part:
                    continue
                if (q.get("parttype") or "").lower() == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b":
                    continue
                self.lsiw_parts.append(q)
        names = [f"{q['path']}  {q.get('label') or ''} {q.get('fstype') or ''}  {human(int(q.get('size') or 0))}" for q in self.lsiw_parts]
        self.lsiw_drop.set_model(Gtk.StringList.new(names or ["(no other partition)"]))
        self.w_part.set_sensitive(bool(self.lsiw_parts) and self.lsiw_tier in ("standard", "deep"))
        if not self.lsiw_parts:
            self.w_folder.set_active(True)

    def page_summary(self):
        p = self.page("Ready to install", None)
        self.summary = label("", "big")
        self.summary.set_selectable(True)
        p.append(self.summary)
        self.summary_warn = label("", "warn")
        p.append(self.summary_warn)
        return p

    def page_install(self):
        p = self.page("Installing", "This takes about 10 to 30 minutes. Keep the USB stick in.")
        self.bar = Gtk.ProgressBar(show_text=True)
        p.append(self.bar)
        self.status = label("Starting...", "big")
        p.append(self.status)
        exp = Gtk.Expander(label="Details")
        self.logview = Gtk.TextView(editable=False, monospace=True, vexpand=True)
        sw = Gtk.ScrolledWindow(vexpand=True, min_content_height=260)
        sw.set_child(self.logview)
        exp.set_child(sw)
        p.append(exp)
        self.finish = Gtk.Box(spacing=10)
        restart = Gtk.Button(label="Restart now")
        restart.add_css_class("suggested-action")
        restart.connect("clicked", lambda *_: subprocess.Popen(["systemctl", "reboot"]))
        self.finish.append(restart)
        self.finish.set_visible(False)
        p.append(self.finish)
        return p

    # ------------------------------------------------------------ navigation
    def show_step(self):
        self.stack.set_visible_child_name(str(self.step))
        for i, l in enumerate(self.step_labels):
            for c in ("stepnow", "stepdone", "stepnext"):
                l.remove_css_class(c)
            l.add_css_class("stepnow" if i == self.step else "stepdone" if i < self.step else "stepnext")
        self.back.set_visible(0 < self.step < 7)
        self.back.set_label("Back")
        self.next.set_visible(self.step < 7)
        self.next.set_label("Install" if self.step == 6 else "Next")
        self.error.set_text("")
        if self.step == 3:
            self.refresh_disks()
        if self.step == 5:
            self.refresh_lsiw_parts()
        if self.step == 6:
            self.fill_summary()

    def check(self):
        if self.step == 3:
            if not self.disk:
                return "Choose a disk."
            if self.mode == "free" and (self.free or 0) < 20e9:
                return "There isn't 20 GB of free space on this disk."
            if self.mode == "part":
                self.update_disk_info()
                if not self.part:
                    return "Choose the partition to replace."
                q = next(q for q in self.disk["parts"] if q["path"] == self.part)
                if int(q.get("size") or 0) < 20e9:
                    return "That partition is smaller than 20 GB."
        if self.step == 4:
            u = self.e_user.get_text()
            if not self.e_full.get_text().strip():
                return "Type your name."
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", u) or u in ("root", "lifeos-installer", "bin", "daemon", "nobody", "ollama", "sddm"):
                return "The user name must start with a letter and use only lower-case letters, digits, - and _."
            if len(self.e_pw.get_text()) < 4:
                return "Choose a password (at least 4 characters)."
            if self.e_pw.get_text() != self.e_pw2.get_text():
                return "The two passwords are different."
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", self.e_host.get_text()):
                return "The computer name may only use letters, digits and -."
        if self.step == 5:
            if self.lsiw_tier == "standard" and self.lsiw_where == "folder" and not re.fullmatch(r"/[^\0\n]*", self.e_lsiw_path.get_text()):
                return "The LSIW folder must be a full path, like /lsiw."
            if self.lsiw_tier in ("standard", "deep") and self.lsiw_where == "partition":
                i = self.lsiw_drop.get_selected()
                if not self.lsiw_parts or i >= len(self.lsiw_parts):
                    return "Choose the partition for LSIW."
                self.lsiw_part = self.lsiw_parts[i]["path"]
        return ""

    def fill_summary(self):
        lang = next(l[0] for l in LANGUAGES if l[1] == self.locale)
        how = {"erase": f"Erase {self.disk['path']} and install LifeOS on it",
               "free": f"Install in the free space of {self.disk['path']}, next to what's there",
               "part": f"Replace partition {self.part}"}[self.mode]
        tier = next(t[1] for t in LSIW_TIERS if t[0] == self.lsiw_tier)
        where = ""
        if self.lsiw_tier in ("standard", "deep"):
            where = f" on {self.lsiw_part} (erased)" if self.lsiw_where == "partition" else (
                f" in {self.e_lsiw_path.get_text()}" if self.lsiw_tier == "standard" else "")
        self.summary.set_text(
            f"Language: {lang}\nKeyboard: {self.xkb}\nTime zone: {self.tz}\nDisk: {how}\n"
            f"Account: {self.e_full.get_text()} ({self.e_user.get_text()}) on \"{self.e_host.get_text()}\"\n"
            f"Log in automatically: {'yes' if self.c_auto.get_active() else 'no'}\n"
            f"Windows programs (LSIW): {tier}{where}\nBoot loader: GRUB ({'UEFI' if UEFI else 'BIOS'})")
        warn = []
        if self.mode == "erase":
            warn.append(f"Everything on {self.disk['path']} will be deleted.")
        if self.mode == "part":
            warn.append(f"Everything on {self.part} will be deleted.")
        if self.lsiw_where == "partition" and self.lsiw_part and self.lsiw_tier in ("standard", "deep"):
            warn.append(f"Everything on {self.lsiw_part} will be deleted.")
        self.summary_warn.set_text(" ".join(warn) + (" This can't be undone." if warn else ""))

    def go(self, d):
        if d > 0:
            err = self.check()
            if err:
                self.error.set_text(err)
                return
        if self.step == 6 and d > 0:
            self.step = 7
            self.show_step()
            self.start_install()
            return
        self.step = max(0, min(6, self.step + d))
        self.show_step()

    # ------------------------------------------------------------ the install
    def start_install(self):
        self.installing = True
        mem_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        keymap = CONSOLE_KEYMAPS.get(self.xkb, self.xkb)
        if not run(["sh", "-c", f"find /usr/share/kbd/keymaps -name {shlex.quote(keymap)}.map.gz | head -n 1"]).strip():
            keymap = "us"
        conf = {
            "DISK": self.disk["path"], "MODE": self.mode, "PART": self.part or "", "UEFI": "1" if UEFI else "0",
            "LOCALE": self.locale, "XKB": self.xkb, "XKB_VARIANT": "", "KEYMAP": keymap, "TZ": self.tz,
            "HOSTNAME_NEW": self.e_host.get_text(), "FULLNAME": self.e_full.get_text().strip(),
            "USERNAME": self.e_user.get_text(), "PASSWORD": self.e_pw.get_text(),
            "ROOT_SAME": "1" if self.c_root.get_active() else "0", "AUTOLOGIN": "1" if self.c_auto.get_active() else "0",
            "LSIW_TIER": self.lsiw_tier, "LSIW_WHERE": self.lsiw_where,
            "LSIW_PATH": self.e_lsiw_path.get_text(), "LSIW_PART": self.lsiw_part or "",
            "SWAP_GB": str(int(min(8, max(2, round(mem_gb))))),
        }
        fd, path = tempfile.mkstemp(prefix="lifeos-install-", suffix=".conf")
        with os.fdopen(fd, "w") as f:
            for k, v in conf.items():
                f.write(f"{k}={shlex.quote(v)}\n")
        threading.Thread(target=self.worker, args=(path,), daemon=True).start()

    def worker(self, path):
        cmd = ["sudo", "-n", "bash", BACKEND, path] if os.geteuid() else ["bash", BACKEND, path]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        error = None
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.startswith("PROGRESS "):
                _, pct, text = line.split(" ", 2)
                GLib.idle_add(self.progress, int(pct), text)
            elif line.startswith("ERROR "):
                error = line[6:]
            else:
                GLib.idle_add(self.add_log, line)
        code = p.wait()
        try:
            os.unlink(path)
        except OSError:
            pass
        GLib.idle_add(self.finished, code == 0 and error is None, error)

    def progress(self, pct, text):
        self.bar.set_fraction(pct / 100)
        self.status.set_text(text)

    def add_log(self, line):
        buf = self.logview.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")

    def finished(self, ok, error):
        self.installing = False
        if ok:
            self.bar.set_fraction(1)
            self.status.set_text("LifeOS is installed. Restart, and take the USB stick out when the screen goes dark.")
            self.finish.set_visible(True)
        else:
            self.status.set_text("The installation failed: " + (error or "see Details") + " Nothing else was changed after that point.")
            self.status.add_css_class("warn")
            self.back.set_label("Back to the summary")
            self.back.set_visible(True)


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.lifeos.Installer")

    def do_activate(self):
        win = Installer(self)
        win.maximize()
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv[:1]))
