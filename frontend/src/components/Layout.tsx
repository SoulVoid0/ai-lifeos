import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Activity, Bell, Bot, Brain, BookOpenText, CalendarDays, FolderGit2, FolderOpen, Gamepad2, GraduationCap, Images, Landmark, LayoutDashboard,
  Monitor, Network, Pause, Play, Search, Settings as Cog, Stethoscope, Swords, Trophy, Workflow, Wrench,
} from "lucide-react";
import { api, type Notification } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { notifyChanged, useToast } from "./ui";
import logo from "../assets/logo.svg";
import { UpdateBanner } from "./Updates";
import { isDesktop } from "../lib/desktop";
import { CommandPalette } from "./CommandPalette";
import { StatusBar } from "./StatusBar";
import { SetupScreen } from "./Devices";
import { GameChip, ScreenAsk, VoiceOrb, useWallpaperAutostart } from "./V02Overlay";
import { MoodLayer, PanicButton, useEventSounds, usePetAutostart } from "./V03Overlay";
import { PetOverlay } from "../pages/Pet";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/computer", label: "Computer", icon: Monitor },
  { to: "/crew", label: "Control Center", icon: Network },
  { to: "/station", label: "Station", icon: Gamepad2 },
  { to: "/gallery", label: "Gallery", icon: Images },
  { to: "/arena", label: "Arena", icon: Swords },
  { to: "/council", label: "Council", icon: Landmark },
  { to: "/stats", label: "Crew stats", icon: Trophy },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/learn", label: "Learn", icon: GraduationCap },
  { to: "/stories", label: "Stories", icon: BookOpenText },
  { to: "/doctor", label: "PC doctor", icon: Stethoscope },
  { to: "/assistant", label: "Assistant", icon: Bot },
  { to: "/automations", label: "Automations", icon: Workflow },
  { to: "/projects", label: "Projects", icon: FolderGit2 },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/files", label: "Files", icon: FolderOpen },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/settings", label: "Settings", icon: Cog },
];

export function Layout() {
  const navigate = useNavigate();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [bellOpen, setBellOpen] = useState(false);
  const settings = useAsync(api.settings, []);
  const notes = useAsync(api.notifications, [], 8000);
  const crew = useAsync(api.crewStatus, [], 5000);
  const health = useAsync(api.health, [], 15000);
  const seen = useRef<Set<number>>(new Set());
  useWallpaperAutostart();
  const primed = useRef(false);

  // Surface new reminders, crew questions and workflow summaries as toasts and OS notifications.
  useEffect(() => {
    if (!notes.data) return;
    const quiet = inQuietHours(settings.data?.quiet_hours);
    const first = !primed.current;
    primed.current = true;
    for (const n of notes.data ?? []) {
      if (seen.current.has(n.id)) continue;
      seen.current.add(n.id);
      if (first) continue; // already waiting in the bell; only pop up what arrives while the app is open
      toast(n.title + (n.body ? `\n${n.body.slice(0, 300)}` : ""), "info");
      if (!quiet && "Notification" in window && Notification.permission === "granted") new Notification(n.title, { body: (n.body ?? "").slice(0, 300) });
    }
  }, [notes.data, toast, settings.data?.quiet_hours]);

  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    const reload = settings.reload;
    window.addEventListener("lifeos:changed", reload);
    return () => window.removeEventListener("lifeos:changed", reload);
  }, [settings.reload]);

  useEventSounds(notes.data);
  usePetAutostart();

  const paused = settings.data?.automation_paused ?? false;
  const togglePause = async () => {
    const s = await api.pause(!paused);
    settings.setData(s);
    toast(s.automation_paused ? "Automation paused. Every action now needs your approval." : "Automation resumed.", "info");
    notifyChanged();
  };

  const unread: Notification[] = notes.data ?? [];
  const working = (crew.data?.agents ?? []).filter((a) => a.state === "working").length;
  const needsYou = (crew.data?.briefs.length ?? 0) + (crew.data?.pending_actions.length ?? 0);

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex items-center gap-2 px-4 py-4">
          <img src={logo} alt="" className="h-8 w-8" />
          <div>
            <div className="text-sm font-semibold">AI LifeOS</div>
            <div className="flex items-center gap-1 text-[11px] text-muted">
              <span className={`h-1.5 w-1.5 rounded-full ${health.error ? "bg-danger" : "bg-ok"}`} />
              {health.error ? "backend offline" : "local"}
            </div>
          </div>
        </div>
        <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm transition ${
                  isActive ? "bg-accent-soft text-text" : "text-muted hover:bg-panel-2 hover:text-text"
                }`
              }
            >
              <Icon size={16} /> {label}
              {to === "/crew" && (working > 0 || needsYou > 0) && (
                <span className={`ml-auto rounded-full px-1.5 text-[10px] ${needsYou ? "bg-warn/20 text-warn" : "bg-ok/15 text-ok"}`}
                  title={needsYou ? `${needsYou} waiting for you` : `${working} agent(s) working`}>
                  {needsYou || working}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="p-3">
          <button
            onClick={togglePause}
            className={`btn w-full justify-center ${paused ? "bg-warn/15 text-warn" : "border border-line text-muted hover:text-text"}`}
            title="Kill switch: when paused, nothing runs without your approval"
          >
            {paused ? <Play size={14} /> : <Pause size={14} />} {paused ? "Resume automation" : "Pause automation"}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line px-6 py-3">
          <form
            className="relative max-w-xl flex-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
            }}
          >
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input pl-9"
              placeholder="Search files, memories, tasks and conversations… (Ctrl+K: apps and commands)"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </form>
          {paused && <span className="chip border-warn/40 text-warn">Automation paused</span>}
          <GameChip />
          <div className="ml-auto"><StatusBar /></div>
          <ScreenAsk />
          <VoiceOrb />
          <PanicButton />
          <div className="relative">
            <button className="btn-ghost relative" onClick={() => setBellOpen((o) => !o)} aria-label="Notifications">
              <Bell size={17} />
              {unread.length > 0 && (
                <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] text-white">
                  {unread.length}
                </span>
              )}
            </button>
            {bellOpen && (
              <div className="card absolute right-0 z-40 mt-2 w-80 p-2 shadow-xl">
                <div className="flex items-center justify-between px-2 py-1">
                  <span className="text-sm font-semibold">Notifications</span>
                  {unread.length > 0 && (
                    <button
                      className="text-xs text-muted hover:text-text"
                      onClick={async () => {
                        await api.markNotificationsRead();
                        notes.reload();
                      }}
                    >
                      Mark all read
                    </button>
                  )}
                </div>
                {unread.length === 0 && <p className="px-2 py-4 text-center text-sm text-muted">Nothing new.</p>}
                {unread.map((n) => (
                  <div key={n.id} className="rounded-lg px-2 py-2 hover:bg-panel-2">
                    <div className="text-sm">{n.title}</div>
                    {n.body && <div className="whitespace-pre-line text-xs text-muted">{n.body}</div>}
                    <div className="text-[11px] text-muted">{ago(n.created_at)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <UpdateBanner />
          {health.error && (
            <div className="border-b border-danger/30 bg-danger/10 px-6 py-2 text-sm text-danger">
              {health.error} {isDesktop ? "It's starting; if this stays, quit AI LifeOS from the tray and open it again." : <>Start it with <code>python -m lifeos</code> in <code>backend/</code>.</>}
            </div>
          )}
          <Outlet />
        </main>
        <CommandPalette nav={NAV} />
        <MoodLayer themeAccent={settings.data?.theme_accent} />
        {!isDesktop && <PetOverlay />}
        <SetupScreen />
      </div>
    </div>
  );
}

function inQuietHours(q?: { enabled: boolean; start: string; end: string }): boolean {
  if (!q?.enabled) return false;
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  const toMin = (t: string) => { const [h, m] = t.split(":").map(Number); return h * 60 + (m || 0); };
  const s = toMin(q.start), e = toMin(q.end);
  return s <= e ? mins >= s && mins < e : mins >= s || mins < e;
}
