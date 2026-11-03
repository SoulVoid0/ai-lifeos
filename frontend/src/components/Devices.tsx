import { useEffect, useState } from "react";
import { Bell, Bluetooth, BluetoothOff, Check, FolderPlus, Lock, Monitor, RefreshCw, Sparkles, Sun, Wifi, WifiOff } from "lucide-react";
import { api, type BtDevice, type DevicesOverview, type DisplayInfo, type WifiNetwork } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Section, Spinner, useToast } from "./ui";

const ok = <T,>(v: T | { error: string } | undefined): T | undefined =>
  v && typeof v === "object" && "error" in (v as object) ? undefined : (v as T | undefined);

function Bars({ signal }: { signal: number }) {
  const n = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
  return (
    <span className="inline-flex items-end gap-px" title={`${signal}%`}>
      {[1, 2, 3, 4].map((i) => <span key={i} className={`w-1 rounded-sm ${i <= n ? "bg-accent" : "bg-line"}`} style={{ height: 3 + i * 3 }} />)}
    </span>
  );
}

export function WifiPanel({ compact = false }: { compact?: boolean }) {
  const toast = useToast();
  const nets = useAsync(() => api.wifiNetworks(), [], 20000);
  const [pick, setPick] = useState<WifiNetwork | null>(null);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState("");

  const connect = async (n: WifiNetwork, password = "") => {
    setBusy(n.ssid);
    try {
      await api.wifiConnect(n.ssid, password);
      toast(`Connected to ${n.ssid}`);
      setPick(null); setPw("");
      nets.reload();
    } catch (e) {
      const msg = (e as Error).message;
      if (/password|secret/i.test(msg) && !password) setPick(n);
      else toast(msg, "error");
    } finally { setBusy(""); }
  };

  if (nets.error) return <p className="text-xs text-muted">{nets.error}</p>;
  if (!nets.data) return <Spinner />;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-muted">
        <span>{nets.data.length} networks</span>
        <button className="btn-ghost px-1.5 text-xs" onClick={async () => nets.setData(await api.wifiNetworks(true))}><RefreshCw size={12} /> Scan</button>
      </div>
      <ul className={`divide-y divide-line overflow-y-auto ${compact ? "max-h-56" : "max-h-72"}`}>
        {nets.data.map((n) => (
          <li key={n.ssid} className="py-1.5">
            <div className="flex items-center gap-2 text-sm">
              <Bars signal={n.signal} />
              <span className="min-w-0 flex-1 truncate">{n.ssid}</span>
              {n.secure && <Lock size={11} className="text-muted" />}
              {n.connected ? (
                <span className="flex items-center gap-1 text-xs text-accent"><Check size={12} /> Connected</span>
              ) : (
                <button className="btn-outline px-2 py-0.5 text-xs" disabled={!!busy}
                  onClick={() => (n.secure ? setPick(pick?.ssid === n.ssid ? null : n) : connect(n))}>
                  {busy === n.ssid ? <Spinner size={12} /> : "Connect"}
                </button>
              )}
            </div>
            {pick?.ssid === n.ssid && (
              <form className="mt-1.5 flex gap-2" onSubmit={(e) => { e.preventDefault(); connect(n, pw); }}>
                <input className="input h-8 flex-1 text-sm" type="password" autoFocus placeholder={`Password for ${n.ssid}`}
                  value={pw} onChange={(e) => setPw(e.target.value)} />
                <button className="btn-primary h-8 text-xs" disabled={!pw || !!busy}>Join</button>
              </form>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function BluetoothPanel({ powered }: { powered: boolean | null }) {
  const toast = useToast();
  const list = useAsync(() => api.btDevices(), [], 15000);
  const [busy, setBusy] = useState("");
  const act = async (action: Parameters<typeof api.btAction>[0], d?: BtDevice) => {
    setBusy(d?.address ?? action);
    try {
      const r = await api.btAction(action, d?.address ?? "");
      toast(r.summary);
      list.reload();
    } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(""); }
  };
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        <button className="btn-ghost px-1.5 text-xs" onClick={() => act(powered ? "power_off" : "power_on")}>
          {powered ? <><BluetoothOff size={12} /> Turn off</> : <><Bluetooth size={12} /> Turn on</>}
        </button>
        <button className="btn-ghost ml-auto px-1.5 text-xs" disabled={busy === "scan"}
          onClick={async () => { setBusy("scan"); try { list.setData(await api.btDevices(8)); } finally { setBusy(""); } }}>
          {busy === "scan" ? <Spinner size={12} /> : <RefreshCw size={12} />} Find devices
        </button>
      </div>
      {!list.data ? <Spinner /> : list.data.length === 0 ? <p className="text-xs text-muted">No devices yet. Put one in pairing mode and press Find devices.</p> : (
        <ul className="max-h-60 divide-y divide-line overflow-y-auto">
          {list.data.map((d) => (
            <li key={d.address} className="flex items-center gap-2 py-1.5 text-sm">
              <span className="min-w-0 flex-1 truncate">{d.name}{d.battery != null && <span className="text-xs text-muted"> · {d.battery}%</span>}</span>
              {busy === d.address ? <Spinner size={12} /> : !d.paired ? (
                <button className="btn-outline px-2 py-0.5 text-xs" onClick={() => act("pair", d)}>Pair</button>
              ) : d.connected ? (
                <button className="btn-ghost px-2 py-0.5 text-xs" onClick={() => act("disconnect", d)}>Disconnect</button>
              ) : (
                <>
                  <button className="btn-outline px-2 py-0.5 text-xs" onClick={() => act("connect", d)}>Connect</button>
                  <button className="btn-ghost px-1.5 py-0.5 text-xs text-muted" title="Forget" onClick={() => act("forget", d)}>Forget</button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DisplayRow({ d, onDone }: { d: DisplayInfo; onDone: () => void }) {
  const toast = useToast();
  const current = d.width ? `${d.width}x${d.height}@${d.refresh}` : "";
  const set = async (v: { mode?: string; scale?: number }) => {
    try { await api.setDisplay({ name: d.name, ...v }); onDone(); } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="rounded-lg border border-line p-2.5 text-sm">
      <div className="flex items-center gap-2"><Monitor size={14} className="text-muted" /><span className="flex-1 truncate">{d.label}</span>
        <span className="text-xs text-muted">{d.width}×{d.height}{d.refresh ? ` · ${d.refresh} Hz` : ""}</span></div>
      {d.modes.length > 0 && (
        <div className="mt-2 flex gap-2">
          <select className="input h-8 flex-1 text-xs" value={current} onChange={(e) => set({ mode: e.target.value })}>
            {!d.modes.includes(current) && current && <option value={current}>{current}</option>}
            {d.modes.map((m) => <option key={m} value={m}>{m.replace("x", " × ").replace("@", " · ")} Hz</option>)}
          </select>
          <select className="input h-8 w-24 text-xs" value={String(d.scale ?? 1)} onChange={(e) => set({ scale: Number(e.target.value) })}>
            {[1, 1.25, 1.5, 1.75, 2].map((s) => <option key={s} value={String(s)}>{Math.round(s * 100)}%</option>)}
          </select>
        </div>
      )}
    </div>
  );
}

/** Wi-Fi, Bluetooth, displays, brightness and notifications: the computer's own settings, inside LifeOS. */
export function DeviceSettings() {
  const toast = useToast();
  const dev = useAsync(api.devices, [], 15000);
  const o: DevicesOverview | undefined = dev.data;
  const wifi = ok(o?.wifi);
  const bt = ok(o?.bluetooth);
  const shown = ok(o?.displays) ?? [];
  const bright = typeof o?.brightness === "number" ? o.brightness : null;
  const win = o?.platform === "windows";
  const [level, setLevel] = useState<number | null>(null);
  useEffect(() => { if (bright != null) setLevel(bright); }, [bright]);

  if (!o) return <Section title="Settings" icon={<Wifi size={15} className="text-accent" />}><Spinner /></Section>;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-3">
      <Section title={wifi?.connected ? `Wi-Fi · ${wifi.connected}` : "Wi-Fi"} icon={wifi?.connected ? <Wifi size={15} className="text-accent" /> : <WifiOff size={15} className="text-muted" />}
        actions={!win && wifi?.available ? (
          <button className="btn-ghost text-xs" onClick={async () => { try { await api.wifiPower(!wifi.enabled); dev.reload(); } catch (e) { toast((e as Error).message, "error"); } }}>
            {wifi.enabled ? "Turn off" : "Turn on"}</button>
        ) : undefined}>
        {!wifi?.available ? <p className="text-xs text-muted">No Wi-Fi on this computer{o.platform === "linux" ? " (or NetworkManager isn't running)" : ""}. A network cable works without setup.</p>
          : !wifi.enabled ? <p className="text-xs text-muted">Wi-Fi is off.</p> : <WifiPanel />}
      </Section>

      <Section title="Bluetooth" icon={<Bluetooth size={15} className={bt?.powered ? "text-accent" : "text-muted"} />}
        actions={win ? <button className="btn-ghost text-xs" onClick={() => api.openOsSettings("bluetooth")}>Open Windows settings</button> : undefined}>
        {!bt?.available ? <p className="text-xs text-muted">No Bluetooth adapter found.</p>
          : bt.settings_only ? <p className="text-xs text-muted">Windows pairs Bluetooth devices in its own settings. The assistant opens them when you ask it to connect something.</p>
            : <BluetoothPanel powered={bt.powered} />}
      </Section>

      <Section title="Display" icon={<Monitor size={15} className="text-accent" />}
        actions={win ? <button className="btn-ghost text-xs" onClick={() => api.openOsSettings("display")}>Open Windows settings</button> : undefined}>
        <div className="space-y-2">
          {shown.length === 0 && <p className="text-xs text-muted">No display information.</p>}
          {shown.map((d) => <DisplayRow key={d.name} d={d} onDone={dev.reload} />)}
          {level != null && (
            <label className="flex items-center gap-2 text-xs text-muted"><Sun size={14} />
              <input type="range" min={5} max={100} value={level} className="flex-1 accent-[var(--color-accent)]"
                onChange={(e) => setLevel(Number(e.target.value))}
                onMouseUp={() => api.setBrightness(level).catch((e) => toast((e as Error).message, "error"))}
                onKeyUp={() => api.setBrightness(level).catch(() => {})} />
              <span className="w-8 text-right">{level}%</span>
            </label>
          )}
          {o.notifications.supported && (
            <p className="flex items-center gap-1.5 pt-1 text-[11px] text-muted"><Bell size={12} />
              {o.notifications.running ? "Other apps' notifications go to the LifeOS bell." : `App notifications: ${o.notifications.error || "off"}`}</p>
          )}
        </div>
      </Section>
    </div>
  );
}

/** First start: name, Wi-Fi (on LifeOS), local AI models and the folders LifeOS may read. */
export function SetupScreen() {
  const toast = useToast();
  const state = useAsync(api.setup, []);
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [folders, setFolders] = useState<string[]>([]);
  const [models, setModels] = useState(true);
  const [closed, setClosed] = useState(false);
  useEffect(() => { if (state.data) setFolders(state.data.suggested_folders.filter((f) => !/Videos|Music/.test(f))); }, [state.data]);

  const s = state.data;
  if (!s?.needed || closed) return null;
  const linux = s.platform === "linux";
  const steps = ["Welcome", ...(linux ? ["Wi-Fi"] : []), "Folders", "AI models"];
  const last = step === steps.length - 1;

  const finish = async (skip = false) => {
    try {
      if (!skip) {
        if (name.trim()) await api.updateSettings({ user_name: name.trim() });
        for (const f of folders) await api.addFolder(f).catch(() => {});
        if (models) await api.applyLean(true).catch(() => {});
      }
      await api.finishSetup();
      setClosed(true);
      if (!skip) toast(models ? "All set. The AI models are downloading in the background." : "All set.");
      window.dispatchEvent(new Event("lifeos:changed"));
    } catch (e) { toast((e as Error).message, "error"); }
  };

  const page = steps[step];
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-bg/95 p-4 backdrop-blur">
      <div className="card w-full max-w-lg p-6 shadow-2xl">
        <div className="mb-4 flex gap-1">{steps.map((t, i) => <span key={t} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-accent" : "bg-line"}`} />)}</div>
        {page === "Welcome" && (
          <>
            <div className="mb-2 flex items-center gap-2 text-lg font-semibold"><Sparkles size={18} className="text-accent" /> Welcome to AI LifeOS</div>
            <p className="mb-4 text-sm text-muted">A few quick choices and your assistant and crew are ready. Everything stays on this computer.</p>
            <label className="text-xs text-muted">What should the assistant call you?</label>
            <input className="input mt-1" autoFocus placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && setStep(1)} />
          </>
        )}
        {page === "Wi-Fi" && (
          <>
            <div className="mb-2 flex items-center gap-2 text-lg font-semibold"><Wifi size={18} className="text-accent" /> Connect to the internet</div>
            <p className="mb-3 text-sm text-muted">Needed once to download the AI models. Skip this if you use a network cable.</p>
            <WifiPanel compact />
          </>
        )}
        {page === "Folders" && (
          <>
            <div className="mb-2 flex items-center gap-2 text-lg font-semibold"><FolderPlus size={18} className="text-accent" /> Which folders may LifeOS read?</div>
            <p className="mb-3 text-sm text-muted">It searches, sorts and summarizes files only in these. Moving or deleting always asks first. You can change this later in Settings.</p>
            <div className="space-y-1">
              {s.suggested_folders.map((f) => (
                <label key={f} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-panel-2">
                  <input type="checkbox" checked={folders.includes(f)} onChange={(e) => setFolders(e.target.checked ? [...folders, f] : folders.filter((x) => x !== f))} />
                  <span className="truncate">{f}</span>
                </label>
              ))}
              {s.suggested_folders.length === 0 && <p className="text-xs text-muted">No standard folders found. Add folders later in Settings → Permissions.</p>}
            </div>
          </>
        )}
        {page === "AI models" && (
          <>
            <div className="mb-2 flex items-center gap-2 text-lg font-semibold"><Sparkles size={18} className="text-accent" /> Local AI models</div>
            <p className="mb-3 text-sm text-muted">LifeOS runs its AI on your own graphics card. The light model set (a few GB) runs well on most PCs and downloads in the background. You can use LifeOS right away.</p>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={models} onChange={(e) => setModels(e.target.checked)} /> Download the light model set now</label>
          </>
        )}
        <div className="mt-6 flex items-center gap-2">
          <button className="btn-ghost text-xs text-muted" onClick={() => finish(true)}>Skip setup</button>
          {step > 0 && <button className="btn-outline ml-auto" onClick={() => setStep(step - 1)}>Back</button>}
          <button className={`btn-primary ${step === 0 ? "ml-auto" : ""}`} onClick={() => (last ? finish() : setStep(step + 1))}>{last ? "Start using LifeOS" : "Next"}</button>
        </div>
      </div>
    </div>
  );
}
