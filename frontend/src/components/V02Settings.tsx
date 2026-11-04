// Settings for the 0.2 features: voice, screen awareness, auto-tidy, phone remote, game mode, live wallpaper,
// self-upgrade and memory search. Each card loads and saves its own settings group.
import { useEffect, useState, type ReactNode } from "react";
import {
  Download, Eye, FolderSync, Gamepad2, GitBranch, History, Image, Mic, QrCode, Smartphone, Trash2, Volume2,
} from "lucide-react";
import { api, type GameMode, type RemoteStatus, type TidyPlan, type UpgradeStatus, type VoiceStatus } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { isDesktop, setWallpaper } from "../lib/desktop";
import { Section, Spinner, useToast } from "./ui";

const errMsg = (e: unknown) => (e as Error).message;

function Toggle({ checked, onChange, children, hint }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode; hint?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}{hint && <span className="block text-xs text-muted">{hint}</span>}</span>
    </label>
  );
}

const pct = (done: number, total: number) => (total ? `${Math.round((done / total) * 100)}%` : `${(done / 1e6).toFixed(0)} MB`);

// ---------------------------------------------------------------- voice

export function VoiceAssistantSettings() {
  const toast = useToast();
  const st = useAsync(api.voice, [], 2500);
  const mics = useAsync(api.microphones, []);
  const sys = useAsync(api.systemVoices, []);
  const [test, setTest] = useState("");
  const s: VoiceStatus | undefined = st.data;
  const save = async (c: Parameters<typeof api.setVoice>[0]) => {
    try { st.setData(await api.setVoice(c)); } catch (e) { toast(errMsg(e), "error"); }
  };
  const install = async (items: string[]) => {
    try { st.setData(await api.installVoice(items)); toast("Downloading in the background"); } catch (e) { toast(errMsg(e), "error"); }
  };
  if (!s) return <Section title="Voice" icon={<Mic size={15} className="text-accent" />}><Spinner /></Section>;
  const c = s.config;
  const sttOk = s.stt_models.find((m) => m.key === c.stt_model)?.installed;
  const defVoice = c.default_voice.startsWith("piper:") ? c.default_voice.slice(6) : "en_US-lessac-medium";
  const missing = [!s.piper && "piper", !sttOk && `stt:${c.stt_model}`, !s.voices_installed.includes(defVoice) && `voice:${defVoice}`].filter(Boolean) as string[];
  const jobs = s.jobs.filter((j) => j.state !== "done");
  return (
    <Section title="Voice: wake word and spoken replies" icon={<Mic size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Say <b>“{c.wake_phrase || "Hey LifeOS"}”</b>, then a command: “ask Forge to fix the build”, “what's on my screen”, “what was I doing on Tuesday”, “game mode on”, “stop”.
        Speech recognition (Vosk) and voices (Piper) run on this computer; nothing is sent anywhere.</p>
      {missing.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 p-2 text-sm">
          <span className="flex-1">Needs a one-time download (about {c.stt_model === "lgraph" ? "330" : "130"} MB): speech recognition, the Piper voice engine and one voice.
            Each agent's own voice (about 60 MB) downloads the first time it speaks.</span>
          <button className="btn-primary" onClick={() => install(missing)}><Download size={14} /> Download</button>
        </div>
      )}
      {jobs.map((j) => (
        <div key={j.item} className="mb-1 text-xs text-muted">{j.item}: {j.state === "failed" ? <span className="text-danger">{j.error}</span> : pct(j.done, j.total)}</div>
      ))}
      {!s.packages.vosk && <p className="mb-2 text-xs text-danger">The speech package isn't in this build. Spoken replies still work.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.wake_word} onChange={(v) => save({ wake_word: v })} hint={`Listener: ${s.listener.state}${s.listener.error ? ` (${s.listener.error})` : ""}`}>Listen for the wake word</Toggle>
        <Toggle checked={c.speak_agent_chat} onChange={(v) => save({ speak_agent_chat: v })}>Agents speak their chat replies</Toggle>
        <Toggle checked={c.chime} onChange={(v) => save({ chime: v })}>Chime when it hears you</Toggle>
        <Toggle checked={c.pause_in_games} onChange={(v) => save({ pause_in_games: v })}>Stop listening while a game runs</Toggle>
        <label className="block text-xs text-muted">Wake phrase
          <input className="input mt-1" defaultValue={c.wake_phrase} onBlur={(e) => e.target.value !== c.wake_phrase && save({ wake_phrase: e.target.value })} /></label>
        <label className="block text-xs text-muted">Microphone
          <select className="input mt-1" value={c.mic} onChange={(e) => save({ mic: e.target.value })}>
            <option value="">Default microphone</option>
            {(mics.data ?? []).map((m) => <option key={m.index} value={m.name}>{m.name}</option>)}
          </select></label>
        <label className="block text-xs text-muted">Recognition model
          <select className="input mt-1" value={c.stt_model} onChange={(e) => save({ stt_model: e.target.value as "small" | "lgraph" })}>
            {s.stt_models.map((m) => <option key={m.key} value={m.key}>{m.label} ({m.size}){m.installed ? "" : ", not downloaded"}</option>)}
          </select></label>
        <label className="block text-xs text-muted">Voice engine
          <select className="input mt-1" value={c.engine} onChange={(e) => save({ engine: e.target.value as VoiceStatus["config"]["engine"] })}>
            <option value="auto">Auto: Piper, else the computer's own voice</option>
            <option value="piper">Piper only</option>
            <option value="system">The computer's own voice ({s.system_tts ?? "none found"})</option>
            <option value="browser">Browser speech (app window only)</option>
          </select></label>
        <label className="block text-xs text-muted">Default voice (agents without their own)
          <select className="input mt-1" value={c.default_voice} onChange={(e) => save({ default_voice: e.target.value })}>
            {s.catalog.map((v) => <option key={v.id} value={`piper:${v.id}`}>{v.label}{v.installed ? "" : " (not downloaded)"}</option>)}
            {(sys.data ?? []).map((v) => <option key={v} value={`system:${v}`}>{v}</option>)}
          </select></label>
        <label className="block text-xs text-muted">Listen for (seconds after the wake word)
          <input type="number" min={3} max={30} className="input mt-1" value={c.listen_seconds} onChange={(e) => save({ listen_seconds: Number(e.target.value) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-outline" onClick={() => install(["voices"])}><Download size={14} /> All crew voices now (about 750 MB)</button>
        <button className="btn-outline" onClick={() => api.listen().then(() => toast("Listening… speak now")).catch((e) => toast(errMsg(e), "error"))}><Mic size={14} /> Talk now</button>
        <input className="input min-w-40 flex-1" placeholder="Type a command to test it" value={test} onChange={(e) => setTest(e.target.value)}
          onKeyDown={async (e) => {
            if (e.key !== "Enter" || !test.trim()) return;
            try { const r = await api.voiceCommand(test.trim()); toast(r.reply || "Done", "info"); setTest(""); } catch (err) { toast(errMsg(err), "error"); }
          }} />
        <button className="btn-ghost" onClick={() => api.stopSpeaking()} title="Stop talking"><Volume2 size={14} /> Hush</button>
      </div>
      {s.history.length > 0 && (
        <div className="mt-3 space-y-1 text-xs">
          {s.history.slice(0, 5).map((h, i) => <div key={i}><span className="text-muted">You:</span> {h.heard} <span className="text-muted">→ {h.agent ?? "LifeOS"}:</span> {h.reply.slice(0, 160)}</div>)}
        </div>
      )}
      <p className="mt-2 text-xs text-muted">Pick each agent's voice and speed in its personality (Station or Control Center → edit agent).</p>
    </Section>
  );
}

// ---------------------------------------------------------------- screen

export function ScreenSettings() {
  const toast = useToast();
  const st = useAsync(api.screen, [], 5000);
  const [pulling, setPulling] = useState("");
  const s = st.data;
  if (!s) return null;
  const pull = async (m: string) => {
    setPulling(m);
    try { await api.pullModel(m); toast(`Downloading ${m}. It shows up here when it's done.`); } catch (e) { toast(errMsg(e), "error"); } finally { setPulling(""); }
  };
  return (
    <Section title="Screen awareness" icon={<Eye size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Press <b>{s.hotkey ?? "Ctrl+Shift+Space"}</b> (desktop app) or say “what's on my screen” and ask about whatever you're looking at.
        A local vision model reads the screenshot; it never leaves the computer.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block text-xs text-muted">Vision model
          <select className="input mt-1" value={s.config.model} onChange={async (e) => st.setData(await api.setScreen({ model: e.target.value }))}>
            {s.models.map((m) => <option key={m.model} value={m.model}>{m.model} ({m.size}): {m.why}</option>)}
          </select></label>
        <div className="space-y-2 pt-4">
          <Toggle checked={s.config.hotkey} onChange={async (v) => st.setData(await api.setScreen({ hotkey: v }))}>Hotkey on</Toggle>
          <Toggle checked={s.config.keep_screenshot} onChange={async (v) => st.setData(await api.setScreen({ keep_screenshot: v }))} hint="Otherwise it's deleted after each answer">Keep the last screenshot</Toggle>
        </div>
      </div>
      {!s.installed && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 p-2 text-sm">
          <span className="flex-1">{s.config.model} isn't downloaded yet.</span>
          <button className="btn-primary" disabled={!!pulling} onClick={() => pull(s.config.model)}>{pulling ? <Spinner /> : <Download size={14} />} Download</button>
        </div>
      )}
      {s.last.answer && <p className="mt-3 text-xs text-muted">Last: “{s.last.question}” → {s.last.answer.slice(0, 200)}</p>}
    </Section>
  );
}

// ---------------------------------------------------------------- auto-tidy

export function TidySettings() {
  const toast = useToast();
  const st = useAsync(api.tidy, [], 15000);
  const [plan, setPlan] = useState<TidyPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const s = st.data;
  if (!s) return null;
  const c = s.config;
  const save = async (v: Parameters<typeof api.setTidy>[0]) => { try { st.setData(await api.setTidy(v)); } catch (e) { toast(errMsg(e), "error"); } };
  const preview = async () => { setBusy(true); try { setPlan(await api.tidyPreview()); } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); } };
  const run = async () => {
    setBusy(true);
    try {
      const r = await api.tidyRun();
      toast(r.status === "pending" ? "Tidy plan is waiting for your approval (Control Center or email)" : r.status === "clean" ? "Nothing to tidy" : r.status === "off" ? "Approve the folder first" : "Tidied. You can undo it in Activity.");
      setPlan(null); st.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  return (
    <Section title="Auto-tidy Downloads" icon={<FolderSync size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Archivist sorts new files into folders by type and gives vague names (IMG_2231, document(3)) readable ones from their contents. Every run can be undone.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })} hint={s.folder ? `${s.loose_files} loose file(s) in ${s.folder}` : "Approve your Downloads folder first (Approved folders above)"}>Tidy automatically</Toggle>
        <Toggle checked={c.rename} onChange={(v) => save({ rename: v })}>Rename vague file names</Toggle>
        <label className="block text-xs text-muted">Folder
          <input className="input mt-1" defaultValue={c.folder} placeholder={s.default_folder || s.os_downloads || "Downloads"} onBlur={(e) => e.target.value !== c.folder && save({ folder: e.target.value })} /></label>
        <label className="block text-xs text-muted">When
          <select className="input mt-1" value={c.mode} onChange={(e) => save({ mode: e.target.value as "ask" | "auto" })}>
            <option value="ask">Ask me first (notification and email)</option>
            <option value="auto">Just do it (undo any time)</option>
          </select></label>
        <label className="block text-xs text-muted">Check every (minutes)
          <input type="number" min={15} max={1440} className="input mt-1" value={c.interval_minutes} onChange={(e) => save({ interval_minutes: Number(e.target.value) })} /></label>
        <label className="block text-xs text-muted">Leave files younger than (minutes)
          <input type="number" min={0} max={1440} className="input mt-1" value={c.min_age_minutes} onChange={(e) => save({ min_age_minutes: Number(e.target.value) })} /></label>
      </div>
      <div className="mt-3 flex gap-2">
        <button className="btn-outline" onClick={preview} disabled={busy}>Preview</button>
        <button className="btn-primary" onClick={run} disabled={busy}>Tidy now</button>
        {c.last_run_at && <span className="self-center text-xs text-muted">Last run {ago(c.last_run_at)}</span>}
      </div>
      {plan && (
        <div className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-line p-2 text-xs">
          {plan.moves.length === 0 ? <p className="text-muted">Nothing to tidy.</p> : plan.moves.map((m) => (
            <div key={m.src} className="truncate"><span className="text-muted">{m.src.split(/[\\/]/).pop()}</span> → {m.dest.split(/[\\/]/).slice(-2).join("/")}{m.renamed && <span className="chip ml-1">renamed</span>}</div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- phone remote

export function RemoteSettings() {
  const toast = useToast();
  const st = useAsync(api.remote, [], 5000);
  const [qr, setQr] = useState<string | null>(null);
  const s: RemoteStatus | undefined = st.data;
  useEffect(() => {
    if (!s?.running || !s.pin) { setQr(null); return; }
    let url = "";
    api.remoteQr().then((u) => { url = u; setQr(u); }).catch(() => setQr(null));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [s?.running, s?.pin]);
  if (!s) return null;
  const save = async (v: { enabled?: boolean; port?: number }) => { try { st.setData(await api.setRemote(v)); } catch (e) { toast(errMsg(e), "error"); } };
  return (
    <Section title="Phone remote" icon={<Smartphone size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Control LifeOS from your phone's browser on the same Wi-Fi: send missions, approve actions, chat with agents, stop everything, toggle game mode.
        It only answers devices on your home network, and each phone must pair with a PIN shown here.</p>
      <div className="flex flex-wrap items-center gap-4">
        <Toggle checked={s.enabled} onChange={(v) => save({ enabled: v })} hint={s.error || (s.running ? "Running" : "Off")}>Phone remote on</Toggle>
        <label className="text-xs text-muted">Port <input type="number" className="input ml-1 inline w-24" defaultValue={s.port} onBlur={(e) => Number(e.target.value) !== s.port && save({ port: Number(e.target.value) })} /></label>
      </div>
      {s.enabled && (
        <div className="mt-3 flex flex-wrap gap-4">
          {qr && <img src={qr} alt="Pairing QR code" className="h-40 w-40 rounded-lg bg-white p-2" />}
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2"><QrCode size={14} /> Scan with your phone's camera, or open:</div>
            {s.urls.map((u) => <div key={u} className="font-mono text-xs">{u}</div>)}
            {s.pin && <div>PIN: <b className="font-mono text-lg tracking-widest">{s.pin}</b> <span className="text-xs text-muted">(changes in {Math.max(0, Math.round((s.pin_expires_in ?? 0) / 60))} min)</span></div>}
            <div className="flex gap-2">
              <button className="btn-ghost text-xs" onClick={async () => st.setData(await api.newRemotePin())}>New PIN</button>
              <button className="btn-ghost text-xs" onClick={async () => { const r = await api.remoteFirewall(); toast(r.note, r.ok ? "ok" : "error"); }}>Allow through the firewall</button>
            </div>
          </div>
        </div>
      )}
      {s.devices.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-muted">Paired phones</div>
          {s.devices.map((d) => (
            <div key={d.id} className="flex items-center gap-2 py-1 text-sm">
              <span className="flex-1">{d.name} <span className="text-xs text-muted">· {d.ip} · last seen {ago(d.last_seen)}</span></span>
              <button className="btn-ghost" title="Unpair" onClick={async () => { await api.removeRemoteDevice(d.id); st.reload(); }}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- game mode

export function GameModeSettings() {
  const toast = useToast();
  const st = useAsync(api.gameMode, [], 3000);
  const [games, setGames] = useState<string | null>(null);
  const g: GameMode | undefined = st.data;
  if (!g) return null;
  const c = g.config;
  const save = async (v: Parameters<typeof api.setGameModeConfig>[0]) => { try { st.setData(await api.setGameModeConfig(v)); } catch (e) { toast(errMsg(e), "error"); } };
  return (
    <Section title="Game mode" icon={<Gamepad2 size={15} className="text-accent" />}
      actions={<button className={g.active ? "btn-primary" : "btn-outline"} onClick={async () => st.setData(await api.setGameMode(g.active ? false : true))}>{g.active ? "End game mode" : "Start now"}</button>}>
      <p className="mb-3 text-xs text-muted">{g.active ? <>On{g.game ? ` for ${g.game}` : ""} ({g.reason}). {g.unloaded} model(s) unloaded.</> : "When a game starts, LifeOS frees the GPU (unloads AI models, pauses ComfyUI) and holds new AI work, then warms back up after you quit."}</p>
      <div className="grid gap-2 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })}>Game mode on</Toggle>
        <Toggle checked={c.auto_detect} onChange={(v) => save({ auto_detect: v })} hint="Steam, Epic, GOG, Battle.net, EA, Ubisoft and Xbox libraries">Detect games automatically</Toggle>
        <Toggle checked={c.fullscreen} onChange={(v) => save({ fullscreen: v })} hint="Windows: any full-screen Direct3D app">Treat full-screen apps as games</Toggle>
        <Toggle checked={c.free_gpu} onChange={(v) => save({ free_gpu: v })}>Unload AI models from the GPU</Toggle>
        <Toggle checked={c.stop_comfy} onChange={(v) => save({ stop_comfy: v })}>Stop ComfyUI completely</Toggle>
        <Toggle checked={c.hold_ai} onChange={(v) => save({ hold_ai: v })} hint="Missions and chats wait until the game closes">Hold AI work while playing</Toggle>
        <Toggle checked={c.reload_after} onChange={(v) => save({ reload_after: v })}>Reload models after</Toggle>
        <Toggle checked={c.notify} onChange={(v) => save({ notify: v })}>Notify me</Toggle>
      </div>
      <label className="mt-3 block text-xs text-muted">Also count these programs as games (one exe name per line)
        <textarea className="input mt-1 min-h-16 font-mono" value={games ?? c.games.join("\n")} onChange={(e) => setGames(e.target.value)}
          onBlur={() => { if (games !== null) { save({ games: games.split("\n").map((x) => x.trim()).filter(Boolean) }); setGames(null); } }} /></label>
      <button className="btn-ghost mt-2 text-xs" onClick={async () => { const d = await api.detectGame(); toast(d.game ? `Sees ${d.game} (${d.how})` : "No game running", "info"); }}>Check what's running</button>
    </Section>
  );
}

// ---------------------------------------------------------------- live wallpaper

export function WallpaperSettings() {
  const toast = useToast();
  const st = useAsync(api.wallpaper, []);
  const w = st.data;
  if (!w) return null;
  const save = async (v: Parameters<typeof api.setWallpaperConfig>[0]) => {
    try {
      const next = await api.setWallpaperConfig(v);
      st.setData(next);
      if ("enabled" in v) {
        const ok = await setWallpaper(!!v.enabled);
        if (v.enabled && !ok) toast(isDesktop ? "Couldn't attach to the desktop" : "Open #/wallpaper in a full-screen browser window, or use the desktop app", "info");
      }
    } catch (e) { toast(errMsg(e), "error"); }
  };
  return (
    <Section title="Live wallpaper" icon={<Image size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Your crew as animated characters on the desktop, behind your icons: working agents sit at their desks, idle ones wander and chat. It pauses during games.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={w.enabled} onChange={(v) => save({ enabled: v })}>Show on the desktop</Toggle>
        <Toggle checked={w.show_clock} onChange={(v) => save({ show_clock: v })}>Clock and date</Toggle>
        <Toggle checked={w.show_ticker} onChange={(v) => save({ show_ticker: v })}>What the crew is doing</Toggle>
        <span />
        <label className="block text-xs text-muted">Frame rate ({w.fps} fps, lower uses less power)
          <input type="range" min={5} max={60} step={5} className="mt-1 w-full" value={w.fps} onChange={(e) => save({ fps: Number(e.target.value) })} /></label>
        <label className="block text-xs text-muted">Dim ({Math.round(w.dim * 100)}%)
          <input type="range" min={0} max={0.9} step={0.05} className="mt-1 w-full" value={w.dim} onChange={(e) => save({ dim: Number(e.target.value) })} /></label>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- self-upgrade

export function UpgradeSettings() {
  const toast = useToast();
  const st = useAsync(api.upgrade, [], 5000);
  const [open, setOpen] = useState<number | null>(null);
  const [focus, setFocus] = useState("");
  const u: UpgradeStatus | undefined = st.data;
  if (!u) return null;
  const c = u.config;
  const save = async (v: Parameters<typeof api.setUpgrade>[0]) => { try { st.setData(await api.setUpgrade(v)); } catch (e) { toast(errMsg(e), "error"); } };
  const act = async (id: number, what: "approve" | "reject" | "undo") => {
    try {
      const a = what === "approve" ? await api.approve(id) : what === "reject" ? await api.reject(id) : await api.undo(id);
      toast(a.result?.summary ?? a.error ?? a.status, a.error ? "error" : "ok");
      st.reload();
    } catch (e) { toast(errMsg(e), "error"); }
  };
  return (
    <Section title="Self-upgrade" icon={<GitBranch size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Forge reads LifeOS's own code and your recent errors, then proposes a small fix. Nothing changes until you approve (here, Control Center or the email button).
        Approved changes go on a new git branch in a separate copy, with tests run first; your checkout is never touched.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={c.enabled} onChange={(v) => save({ enabled: v })} hint={u.repo ? `Repo: ${u.repo}` : "Set the repo folder below (git clone of ai-lifeos)"}>Propose upgrades on a schedule</Toggle>
        <label className="block text-xs text-muted">Every (days)
          <input type="number" min={1} max={60} className="input mt-1" value={c.every_days} onChange={(e) => save({ every_days: Number(e.target.value) })} /></label>
        <label className="block text-xs text-muted">LifeOS source folder
          <input className="input mt-1" defaultValue={c.repo_path} placeholder={u.repo ?? "A:\\ai-lifeos"} onBlur={(e) => e.target.value !== c.repo_path && save({ repo_path: e.target.value })} /></label>
        <label className="block text-xs text-muted">Model (empty: Forge's)
          <input className="input mt-1" defaultValue={c.model} placeholder={u.model ?? ""} onBlur={(e) => e.target.value !== c.model && save({ model: e.target.value })} /></label>
        <Toggle checked={c.run_tests} onChange={(v) => save({ run_tests: v })}>Run the tests before committing</Toggle>
        <Toggle checked={c.push} onChange={(v) => save({ push: v })} hint="Pushes the branch to origin so you can open a PR">Push the branch to GitHub</Toggle>
      </div>
      {!u.git && <p className="mt-2 text-xs text-danger">git isn't installed or wasn't found.</p>}
      <div className="mt-3 flex gap-2">
        <input className="input flex-1" placeholder="Focus (optional), e.g. make the Station load faster" value={focus} onChange={(e) => setFocus(e.target.value)} />
        <button className="btn-primary" disabled={u.busy || !u.repo} onClick={async () => { st.setData(await api.proposeUpgrade(focus || undefined)); toast("Forge is reading the code…", "info"); }}>
          {u.busy ? <Spinner /> : null} Propose now</button>
      </div>
      {u.last.state === "failed" && <p className="mt-2 text-xs text-danger">Last try failed: {u.last.error}</p>}
      {u.proposals.length > 0 && (
        <div className="mt-3 space-y-2">
          {u.proposals.map((p) => (
            <div key={p.id} className="rounded-lg border border-line p-2 text-sm">
              <div className="flex items-center gap-2">
                <button className="flex-1 truncate text-left hover:underline" onClick={() => setOpen(open === p.id ? null : p.id)}>{p.summary}</button>
                <span className="chip">{p.status}</span>
                {p.status === "pending" && <>
                  <button className="btn-primary px-2 py-1 text-xs" onClick={() => act(p.id, "approve")}>Apply</button>
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => act(p.id, "reject")}>Reject</button>
                </>}
                {p.status === "executed" && <button className="btn-ghost px-2 py-1 text-xs" onClick={() => act(p.id, "undo")}>Undo</button>}
              </div>
              {open === p.id && (
                <div className="mt-2">
                  {typeof p.preview?.summary === "string" && <p className="mb-2 text-xs text-muted">{p.preview.summary}</p>}
                  {typeof p.result?.summary === "string" && <p className="mb-2 text-xs">{p.result.summary}</p>}
                  <pre className="max-h-96 overflow-auto rounded bg-panel-2 p-2 text-[11px] leading-snug">{String(p.preview?.diff ?? "")}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- memory search

export function RecallSettings() {
  const toast = useToast();
  const st = useAsync(api.recall, [], 10000);
  const s = st.data;
  if (!s) return null;
  return (
    <Section title="Memory search" icon={<History size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">Ask “what was that article about GPUs I read last week?” in the search bar, to Sage, or by voice. It searches your chats, files, memories, missions and,
        if you allow it, a private copy of your browser history kept only in LifeOS's database.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Toggle checked={s.config.browser_history} onChange={async (v) => st.setData(await api.setRecall({ browser_history: v }))}
          hint={s.browsers_found.length ? `Found: ${s.browsers_found.join(", ")}` : "No browsers found"}>Include browser history</Toggle>
        <label className="block text-xs text-muted">Keep history for (days)
          <input type="number" min={7} max={3650} className="input mt-1" value={s.config.keep_days} onChange={async (e) => st.setData(await api.setRecall({ keep_days: Number(e.target.value) }))} /></label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>{s.visits} page visits stored{s.config.last_sync ? `, synced ${ago(s.config.last_sync)}` : ""}</span>
        <button className="btn-ghost text-xs" disabled={!s.config.browser_history} onClick={async () => { const r = await api.recallSync(); toast(`Added ${r.added} visit(s)${r.errors?.length ? `; ${r.errors.join("; ")}` : ""}`); st.reload(); }}>Sync now</button>
        <button className="btn-ghost text-xs text-danger" onClick={async () => { const r = await api.forgetHistory(); toast(`Deleted ${r.deleted} visit(s)`); st.reload(); }}>Delete stored history</button>
      </div>
    </Section>
  );
}

export function V02Settings() {
  return (
    <>
      <VoiceAssistantSettings />
      <ScreenSettings />
      <GameModeSettings />
      <WallpaperSettings />
      <TidySettings />
      <RemoteSettings />
      <RecallSettings />
      <UpgradeSettings />
    </>
  );
}
