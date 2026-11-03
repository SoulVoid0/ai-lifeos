import { useMemo, useState, type ReactNode } from "react";
import {
  Battery, BatteryCharging, Cpu, HardDrive, Lock, LogOut, MemoryStick, Minus, Moon, Plus, Power, RefreshCw, RotateCcw, Search, Volume2, VolumeX, X,
} from "lucide-react";
import { api, type AppEntry, type PowerAction } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

const TILE = ["#8b5cf6", "#3b82f6", "#10b981", "#06b6d4", "#f43f5e", "#f59e0b", "#ec4899", "#84cc16"];
const tint = (name: string) => TILE[[...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TILE.length];

export function AppTile({ app, onOpen }: { app: AppEntry; onOpen: (a: AppEntry) => void }) {
  return (
    <button className="group flex flex-col items-center gap-1.5 rounded-xl p-2 text-center hover:bg-panel-2" title={app.comment || app.name}
      onClick={() => onOpen(app)}>
      <span className="grid h-11 w-11 place-items-center rounded-xl text-lg font-semibold text-white" style={{ background: tint(app.name) }}>
        {app.name.trim().charAt(0).toUpperCase()}
      </span>
      <span className="line-clamp-2 text-xs leading-tight">{app.name}</span>
    </button>
  );
}

export function useOpenApp() {
  const toast = useToast();
  return async (a: AppEntry) => {
    try {
      await api.launchApp(a.id);
      toast(`Opened ${a.name}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
}

function Meter({ icon, label, value, detail }: { icon: ReactNode; label: string; value: number; detail: string }) {
  const tone = value >= 90 ? "bg-danger" : value >= 75 ? "bg-warn" : "bg-accent";
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center gap-2 text-xs text-muted">{icon}{label}<span className="ml-auto text-text">{Math.round(value)}%</span></div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-panel-2"><div className={`h-full ${tone}`} style={{ width: `${Math.min(100, value)}%` }} /></div>
      <div className="mt-1.5 text-xs text-muted">{detail}</div>
    </div>
  );
}

/** A button that needs a second click within 4 seconds, for things that can't be undone. */
function ConfirmButton({ label, confirmLabel, icon, onConfirm, className = "btn-outline" }: {
  label: string; confirmLabel: string; icon: ReactNode; onConfirm: () => void; className?: string;
}) {
  const [armed, setArmed] = useState(false);
  return (
    <button className={armed ? "btn-danger" : className}
      onClick={() => {
        if (armed) { setArmed(false); onConfirm(); return; }
        setArmed(true);
        setTimeout(() => setArmed(false), 4000);
      }}>
      {icon} {armed ? confirmLabel : label}
    </button>
  );
}

export default function Computer() {
  const toast = useToast();
  const openApp = useOpenApp();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"memory" | "cpu">("memory");
  const list = useAsync(() => api.apps(), []);
  const status = useAsync(api.systemStatus, [], 5000);
  const programs = useAsync(() => api.programs(sort), [sort], 8000);

  const shown = useMemo(() => {
    const all = list.data ?? [];
    const s = q.trim().toLowerCase();
    return s ? all.filter((a) => a.name.toLowerCase().includes(s)) : all;
  }, [list.data, q]);
  const recent = useMemo(() => (list.data ?? []).filter((a) => a.launches > 0).sort((a, b) => b.launches - a.launches).slice(0, 8), [list.data]);

  const power = async (action: PowerAction) => {
    try {
      const r = await api.power(action);
      toast(r.summary, "info");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const volume = async (v: { level?: number; change?: "up" | "down" | "mute" }) => {
    try { await api.setVolume(v); } catch (e) { toast((e as Error).message, "error"); }
  };

  const st = status.data;
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <PageHeader title="Computer" subtitle="Your apps, what's running, and the controls for this PC. Press Ctrl+K anywhere to open an app."
        actions={<button className="btn-ghost text-xs" onClick={async () => { list.setData(await api.apps("", true)); }}><RefreshCw size={13} /> Refresh apps</button>} />

      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Section title="Status" icon={<Cpu size={15} className="text-accent" />} className="lg:col-span-2">
          {!st ? <Spinner /> : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Meter icon={<Cpu size={13} />} label="CPU" value={st.cpu_percent} detail={`${st.cpu_count} threads · up ${st.uptime_hours} h`} />
              <Meter icon={<MemoryStick size={13} />} label="Memory" value={st.memory.percent} detail={`${st.memory.used_gb} of ${st.memory.total_gb} GB`} />
              <Meter icon={<HardDrive size={13} />} label={`Disk ${st.disk.path}`} value={st.disk.percent} detail={`${st.disk.free_gb} GB free`} />
              {st.battery ? (
                <Meter icon={st.battery.plugged_in ? <BatteryCharging size={13} /> : <Battery size={13} />} label="Battery" value={st.battery.percent}
                  detail={st.battery.plugged_in ? "Plugged in" : st.battery.minutes_left ? `About ${st.battery.minutes_left} min left` : "On battery"} />
              ) : (
                <div className="rounded-lg border border-line p-3 text-xs text-muted"><Battery size={13} className="mb-1" />No battery (desktop PC)</div>
              )}
            </div>
          )}
        </Section>

        <Section title="Controls" icon={<Power size={15} className="text-accent" />}>
          <div className="flex items-center gap-2">
            <Volume2 size={15} className="text-muted" />
            <button className="btn-outline px-2" title="Quieter" onClick={() => volume({ change: "down" })}><Minus size={13} /></button>
            <input type="range" min={0} max={100} step={2} defaultValue={50} className="flex-1 accent-[var(--color-accent)]"
              onMouseUp={(e) => volume({ level: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => volume({ level: Number((e.target as HTMLInputElement).value) })} />
            <button className="btn-outline px-2" title="Louder" onClick={() => volume({ change: "up" })}><Plus size={13} /></button>
            <button className="btn-outline px-2" title="Mute or unmute" onClick={() => volume({ change: "mute" })}><VolumeX size={13} /></button>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button className="btn-outline" onClick={() => power("lock")}><Lock size={13} /> Lock</button>
            <ConfirmButton label="Sleep" confirmLabel="Sleep now?" icon={<Moon size={13} />} onConfirm={() => power("sleep")} />
            <ConfirmButton label="Restart" confirmLabel="Restart now?" icon={<RotateCcw size={13} />} onConfirm={() => power("restart")} />
            <ConfirmButton label="Shut down" confirmLabel="Shut down now?" icon={<Power size={13} />} onConfirm={() => power("shutdown")} />
            <ConfirmButton label="Sign out" confirmLabel="Sign out now?" icon={<LogOut size={13} />} onConfirm={() => power("sign_out")} className="btn-ghost col-span-2" />
          </div>
          <p className="mt-2 text-[11px] text-muted">Restart and shut down wait 10 seconds on Windows. The assistant can do these too, but always asks you first.</p>
        </Section>
      </div>

      <Section title="Apps" icon={<Search size={15} className="text-accent" />}
        actions={<div className="relative w-64"><Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input h-8 pl-8 text-sm" placeholder="Find an app" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && shown[0] && openApp(shown[0])} /></div>}>
        {recent.length > 0 && !q && (
          <>
            <div className="mb-1 text-xs text-muted">Most used</div>
            <div className="mb-3 grid grid-cols-4 gap-1 sm:grid-cols-8">{recent.map((a) => <AppTile key={a.id} app={a} onOpen={openApp} />)}</div>
            <div className="mb-1 text-xs text-muted">All apps</div>
          </>
        )}
        {list.data === undefined ? <Spinner /> : shown.length === 0 ? <Empty>No apps found.</Empty> : (
          <div className="grid max-h-[28rem] grid-cols-4 gap-1 overflow-y-auto sm:grid-cols-6 lg:grid-cols-10">
            {shown.map((a) => <AppTile key={a.id} app={a} onOpen={openApp} />)}
          </div>
        )}
      </Section>

      <Section title="Running programs" icon={<MemoryStick size={15} className="text-accent" />}
        actions={<div className="flex gap-1">{(["memory", "cpu"] as const).map((s) => (
          <button key={s} className={s === sort ? "btn-outline text-xs" : "btn-ghost text-xs"} onClick={() => setSort(s)}>{s === "memory" ? "Memory" : "CPU"}</button>
        ))}</div>}>
        <ul className="divide-y divide-line">
          {(programs.data ?? []).map((p) => (
            <li key={p.name} className="flex items-center gap-3 py-1.5 text-sm">
              <span className="min-w-0 flex-1 truncate">{p.name}{p.count > 1 && <span className="text-xs text-muted"> ×{p.count}</span>}</span>
              <span className="w-20 text-right text-xs text-muted">{p.memory_mb.toLocaleString()} MB</span>
              <span className="w-14 text-right text-xs text-muted">{p.cpu_percent}%</span>
              {p.protected ? <span className="w-16 text-right text-[11px] text-muted">system</span> : (
                <span className="w-16 text-right">
                  <ConfirmButton label="End" confirmLabel="Sure?" icon={<X size={12} />} className="btn-ghost px-1.5 text-xs"
                    onConfirm={async () => {
                      try {
                        const r = await api.endProgram(p.name);
                        toast(`Ended ${r.ended}`);
                        programs.reload();
                      } catch (e) { toast((e as Error).message, "error"); }
                    }} />
                </span>
              )}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
