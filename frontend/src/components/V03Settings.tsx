// Settings for the 0.3 features: mood lighting, sound effects, the desktop pet, the panic button and USB backup.
import type { ReactNode } from "react";
import { HardDriveUpload, OctagonX, PawPrint, Sparkles, Volume2 } from "lucide-react";
import { api, type PetConfig, type SfxConfig } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { isDesktop, setPet } from "../lib/desktop";
import { play, setSfxConfig, type Sfx } from "../lib/sfx";
import { Section, notifyChanged, useToast } from "./ui";

function Toggle({ checked, onChange, children, hint }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode; hint?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}{hint && <span className="block text-xs text-muted">{hint}</span>}</span>
    </label>
  );
}
const err = (e: unknown) => (e as Error).message;

export function MoodSettings() {
  const toast = useToast();
  const m = useAsync(api.mood, [], 30000);
  if (!m.data) return null;
  const c = m.data.config;
  const save = async (v: Parameters<typeof api.setMood>[0]) => { try { m.setData(await api.setMood(v)); notifyChanged(); } catch (e) { toast(err(e), "error"); } };
  return (
    <Section title="Mood lighting" icon={<Sparkles size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Colors (and optional ambient music) follow the time of day and what's going on: indigo at night, rose at dawn, cyan by day, amber at dusk;
        red when something needs you, lime in game mode. Right now: <b>{m.data.phase}</b>, <b>{m.data.activity}</b>.</p>
      <div className="grid gap-2 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })}>Mood lighting on</Toggle>
        <Toggle checked={c.colors} onChange={(v) => save({ colors: v })} hint="Off: your theme color from Appearance">Shift the colors</Toggle>
        <Toggle checked={c.music} onChange={(v) => save({ music: v })} hint="Generated live in the app; no files, silent during games">Ambient music</Toggle>
        <label className="text-xs text-muted">Music volume ({Math.round(c.volume * 100)}%)
          <input type="range" min={0} max={1} step={0.05} className="mt-1 w-full" value={c.volume} onChange={(e) => save({ volume: Number(e.target.value) })} /></label>
        <Toggle checked={c.follow_time} onChange={(v) => save({ follow_time: v })}>Follow the time of day</Toggle>
        <Toggle checked={c.follow_activity} onChange={(v) => save({ follow_activity: v })}>Follow what the crew is doing</Toggle>
      </div>
    </Section>
  );
}

const SAMPLES: Sfx[] = ["notify", "success", "error", "start", "approve", "levelup", "panic", "wake"];

export function SoundSettings() {
  const toast = useToast();
  const s = useAsync(api.sfx, []);
  if (!s.data) return null;
  const c = s.data;
  const save = async (v: Partial<SfxConfig>) => {
    try { const n = await api.setSfx(v); s.setData(n); setSfxConfig(n); } catch (e) { toast(err(e), "error"); }
  };
  return (
    <Section title="Sound effects" icon={<Volume2 size={15} className="text-accent" />}>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })}>Sounds on (notifications, missions, level-ups, panic)</Toggle>
        <Toggle checked={c.clicks} onChange={(v) => save({ clicks: v })}>Click sound on buttons</Toggle>
        <label className="text-xs text-muted">Sound pack
          <select className="input mt-1" value={c.theme} onChange={(e) => save({ theme: e.target.value as SfxConfig["theme"] })}>
            <option value="scifi">Sci-fi (bleeps and sweeps)</option><option value="soft">Soft (bells)</option><option value="retro">Retro (8-bit)</option>
          </select></label>
        <label className="text-xs text-muted">Volume ({Math.round(c.volume * 100)}%)
          <input type="range" min={0} max={1} step={0.05} className="mt-1 w-full" value={c.volume} onChange={(e) => save({ volume: Number(e.target.value) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">{SAMPLES.map((n) => <button key={n} className="chip hover:text-text" onClick={() => play(n)}>▶ {n}</button>)}</div>
    </Section>
  );
}

const SPECIES: [PetConfig["species"], string][] = [["bot", "Robot"], ["cat", "Cat"], ["slime", "Slime"], ["ghost", "Ghost"], ["fox", "Fox"]];

export function PetSettings() {
  const toast = useToast();
  const p = useAsync(api.pet, []);
  if (!p.data) return null;
  const c = p.data;
  const save = async (v: Partial<PetConfig>) => {
    try {
      const n = await api.setPet(v);
      p.setData(n);
      if (isDesktop && ("enabled" in v || "size" in v)) {
        await setPet(false, n.size);
        if (n.enabled) await setPet(true, n.size);
      }
    } catch (e) { toast(err(e), "error"); }
  };
  return (
    <Section title="Desktop pet" icon={<PawPrint size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">A little creature on your desktop that shows what the crew is up to: naps when it's quiet, types when agents work, waves when something needs you,
        celebrates level-ups. {isDesktop ? "Drag it anywhere; double-click it to open LifeOS." : "It lives in the corner of this window."}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })}>Show the pet</Toggle>
        <Toggle checked={c.roam} onChange={(v) => save({ roam: v })}>Let it wander</Toggle>
        <Toggle checked={c.speak} onChange={(v) => save({ speak: v })}>Speech bubbles (what agents are doing, alerts)</Toggle>
        <span />
        <label className="text-xs text-muted">Creature
          <select className="input mt-1" value={c.species} onChange={(e) => save({ species: e.target.value as PetConfig["species"] })}>
            {SPECIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></label>
        <label className="text-xs text-muted">Name
          <input className="input mt-1" defaultValue={c.name} onBlur={(e) => e.target.value !== c.name && save({ name: e.target.value })} /></label>
        <label className="text-xs text-muted">Size ({c.size}px)
          <input type="range" min={48} max={192} step={8} className="mt-1 w-full" value={c.size} onChange={(e) => save({ size: Number(e.target.value) })} /></label>
      </div>
    </Section>
  );
}

export function PanicSettings() {
  const toast = useToast();
  const p = useAsync(api.panic, []);
  if (!p.data) return null;
  const c = p.data.config;
  const save = async (v: Parameters<typeof api.setPanic>[0]) => { try { p.setData(await api.setPanic(v)); } catch (e) { toast(err(e), "error"); } };
  return (
    <Section title="Panic button" icon={<OctagonX size={15} className="text-danger" />}>
      <p className="mb-3 text-xs text-muted">The red button at the top, <b>Ctrl+Shift+F12</b> anywhere (desktop app), the phone remote, or saying “panic”. It always stops every mission and job. Also:</p>
      <div className="grid gap-2 md:grid-cols-2">
        <Toggle checked={c.hide_windows} onChange={(v) => save({ hide_windows: v })}>Hide LifeOS (windows, wallpaper, pet)</Toggle>
        <Toggle checked={c.pause_automation} onChange={(v) => save({ pause_automation: v })} hint="Until you press All clear">Pause automation</Toggle>
        <Toggle checked={c.unload_models} onChange={(v) => save({ unload_models: v })}>Unload AI models from the GPU</Toggle>
        <Toggle checked={c.mute} onChange={(v) => save({ mute: v })}>Mute the PC</Toggle>
        <Toggle checked={c.stop_media} onChange={(v) => save({ stop_media: v })}>Cancel image and video jobs</Toggle>
      </div>
    </Section>
  );
}

export function UsbBackupSettings() {
  const toast = useToast();
  const u = useAsync(api.usb, [], 5000);
  if (!u.data) return null;
  const c = u.data.config;
  const save = async (v: Parameters<typeof api.setUsb>[0]) => { try { u.setData(await api.setUsb(v)); } catch (e) { toast(err(e), "error"); } };
  return (
    <Section title="USB backup" icon={<HardDriveUpload size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Plug in a drive you've allowed and LifeOS backs up the crew, memories, tasks and settings to an “AI-LifeOS backups” folder on it, then says it's safe to unplug.
        A new drive is never written to until you allow it here.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })}>Back up when an allowed drive is plugged in</Toggle>
        <Toggle checked={c.include_media} onChange={(v) => save({ include_media: v })}>Include generated images and videos</Toggle>
        <Toggle checked={c.ask_new} onChange={(v) => save({ ask_new: v })}>Ask about new drives</Toggle>
        <span />
        <label className="text-xs text-muted">Keep backups on each drive
          <input type="number" min={1} max={50} className="input mt-1" value={c.keep} onChange={(e) => save({ keep: Number(e.target.value) })} /></label>
        <label className="text-xs text-muted">At most one every (hours)
          <input type="number" min={0} max={720} className="input mt-1" value={c.min_hours} onChange={(e) => save({ min_hours: Number(e.target.value) })} /></label>
      </div>
      {u.data.error && <p className="mt-2 text-xs text-danger">{u.data.error}</p>}
      <div className="mt-3 space-y-1.5">
        {u.data.drives.length === 0 && <p className="text-xs text-muted">Plug in a USB drive to see it here.</p>}
        {u.data.drives.map((d) => (
          <div key={d.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${d.present ? "bg-ok" : "bg-muted/40"}`} />
            <span className="flex-1">{d.label} <span className="text-xs text-muted">{d.mount ?? "not plugged in"}{d.free ? ` · ${(d.free / 1e9).toFixed(0)} GB free` : ""}
              {d.last_backup_at ? ` · last backup ${ago(d.last_backup_at)}` : ""}</span></span>
            {u.data!.running === d.id ? <span className="text-xs text-accent">Backing up…</span> : <>
              <button className={d.allowed ? "btn-ghost text-xs" : "btn-primary px-2 py-1 text-xs"} onClick={async () => u.setData(await api.allowUsb(d.id, !d.allowed))}>{d.allowed ? "Disallow" : "Allow"}</button>
              {d.allowed && d.present && <button className="btn-outline px-2 py-1 text-xs" onClick={async () => { try { await api.runUsb(d.id); toast(`Backed up to ${d.label}`); u.reload(); } catch (e) { toast(err(e), "error"); } }}>Back up now</button>}
              {!d.present && <button className="btn-ghost text-xs" onClick={async () => u.setData(await api.forgetUsb(d.id))}>Forget</button>}
            </>}
          </div>
        ))}
      </div>
    </Section>
  );
}

export function V03Settings() {
  return (
    <>
      <MoodSettings />
      <SoundSettings />
      <PetSettings />
      <PanicSettings />
      <UsbBackupSettings />
    </>
  );
}
