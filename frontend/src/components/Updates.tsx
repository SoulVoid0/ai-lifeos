import { useEffect, useState } from "react";
import { DatabaseBackup, Download, KeyRound, Loader2, RefreshCw, Save } from "lucide-react";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { appVersion, checkUpdate, installUpdate, isDesktop, onDesktopEvent, type UpdateInfo } from "../lib/desktop";
import { Section, useToast } from "./ui";

type State =
  | { s: "idle" } | { s: "checking" } | { s: "none" } | { s: "error"; msg: string }
  | { s: "available"; info: UpdateInfo } | { s: "installing"; info: UpdateInfo; done: number; total: number | null };

// One shared state so the banner, the Settings card and the tray's "Check for updates" agree.
let state: State = { s: "idle" };
const subs = new Set<(s: State) => void>();
const setState = (s: State) => { state = s; subs.forEach((f) => f(s)); };

export async function runCheck() {
  if (!isDesktop || state.s === "checking" || state.s === "installing") return state;
  setState({ s: "checking" });
  try {
    const info = await checkUpdate();
    setState(info ? { s: "available", info } : { s: "none" });
  } catch (e) {
    // The updater only says the request failed; the backend's answer says why (no token, bad token, ...).
    let msg = String(e);
    try { await api.updateManifest(); } catch (why) { msg = (why as Error).message; }
    setState({ s: "error", msg });
  }
  return state;
}

export async function runInstall() {
  if (state.s !== "available") return;
  const info = state.info;
  setState({ s: "installing", info, done: 0, total: null });
  try {
    await installUpdate(); // the app closes and the installer reopens the new version
  } catch (e) {
    setState({ s: "error", msg: String(e) });
  }
}

function useUpdateState() {
  const [s, set] = useState(state);
  useEffect(() => { subs.add(set); return () => { subs.delete(set); }; }, []);
  return s;
}

/** Banner under the header: checks shortly after launch and when the tray asks, then offers Install & restart. */
export function UpdateBanner() {
  const toast = useToast();
  const s = useUpdateState();
  useEffect(() => {
    if (!isDesktop) return;
    const t = setTimeout(() => { if (state.s === "idle") runCheck(); }, 15_000);
    const offCheck = onDesktopEvent("check-updates", async () => {
      const r = await runCheck();
      if (r.s === "none") toast("AI LifeOS is up to date");
      if (r.s === "error") toast(r.msg, "error");
    });
    const offProgress = onDesktopEvent<[number, number | null]>("update-progress", ([done, total]) => {
      if (state.s === "installing") setState({ ...state, done, total });
    });
    return () => { clearTimeout(t); offCheck(); offProgress(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (s.s !== "available" && s.s !== "installing") return null;
  const pct = s.s === "installing" && s.total ? Math.round((s.done / s.total) * 100) : null;
  return (
    <div className="flex items-center gap-3 border-b border-accent/30 bg-accent/10 px-6 py-2 text-sm">
      <Download size={15} className="text-accent" />
      <span>Update available: <b>{s.info.version}</b> <span className="text-muted">(you have {s.info.current})</span></span>
      {s.s === "installing" ? (
        <span className="ml-auto flex items-center gap-2 text-muted"><Loader2 size={14} className="animate-spin" />
          {pct === null ? "Downloading…" : pct < 100 ? `Downloading… ${pct}%` : "Installing, LifeOS will reopen"}</span>
      ) : (
        <button className="btn-primary ml-auto" onClick={runInstall}>Install &amp; restart</button>
      )}
    </div>
  );
}

/** Settings card: version, check now, and where updates come from (with the read token for a private repo). */
export function UpdateSettings() {
  const toast = useToast();
  const s = useUpdateState();
  const cfg = useAsync(() => api.updateConfig(), []);
  const version = useAsync(() => appVersion(), []);
  const [repo, setRepo] = useState<string | null>(null);
  const [token, setToken] = useState("");

  const save = async (patch: { repo?: string; token?: string }) => {
    try {
      await api.setUpdateConfig(patch);
      setToken("");
      if (patch.repo !== undefined) setRepo(null);
      cfg.reload();
      toast(patch.token === "" ? "Token removed" : "Saved");
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  // Check now saves whatever was typed first, so a pasted token is never left unsaved.
  const checkNow = async () => {
    const patch: { repo?: string; token?: string } = {};
    if (token.trim()) patch.token = token;
    if (repo !== null && cfg.data && repo !== cfg.data.repo) patch.repo = repo;
    if (Object.keys(patch).length && !(await save(patch))) return;
    runCheck();
  };
  const c = cfg.data;
  return (
    <Section title="Updates" icon={<Download size={15} className="text-accent" />}
      actions={isDesktop ? (
        <button className="btn-outline" onClick={checkNow} disabled={s.s === "checking" || s.s === "installing"}>
          {s.s === "checking" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Check now
        </button>) : undefined}>
      <div className="mb-3 text-sm">
        {!isDesktop ? <span className="text-muted">Updates are installed by the desktop app. This is the browser version.</span> : <>
          Version <b>{version.data || "…"}</b>
          {s.s === "none" && <span className="text-ok"> · up to date</span>}
          {s.s === "available" && <> · <b>{s.info.version}</b> is available <button className="btn-primary ml-2" onClick={runInstall}>Install &amp; restart</button></>}
          {s.s === "installing" && <span className="text-muted"> · installing {s.info.version}…</span>}
          {s.s === "error" && <div className="mt-1 text-danger">{s.msg}</div>}
        </>}
      </div>
      {c && (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block text-xs text-muted">GitHub repository with the releases
            <div className="mt-1 flex gap-2">
              <input className="input" value={repo ?? c.repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/name" />
              {repo !== null && repo !== c.repo && <button className="btn-outline" onClick={() => save({ repo })}><Save size={14} /></button>}
            </div>
          </label>
          <label className="block text-xs text-muted">Read token (only for a private repository) · {c.has_token ? <span className="text-ok">saved</span> : "none"}
            <div className="mt-1 flex gap-2">
              <input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && token.trim()) save({ token }); }}
                placeholder={c.has_token ? "•••••••• (paste a new one to replace)" : "github_pat_…"} autoComplete="off" />
              <button className="btn-outline" onClick={() => save({ token })} disabled={!token.trim()} title="Save token"><KeyRound size={14} /> Save</button>
              {c.has_token && <button className="btn-ghost text-danger" onClick={() => save({ token: "" })}>Remove</button>}
            </div>
          </label>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">The token is stored in Windows Credential Manager. It only needs read access to this repository's contents. Every update is checked against the signing key built into the app before it installs; your data in %APPDATA%\AI-LifeOS is never touched.</p>
    </Section>
  );
}

/** Shown when LifeOS once ran with Microsoft Store Python, whose data lives in a folder the app can't see. */
export function OldDataSettings() {
  const toast = useToast();
  const d = useAsync(() => api.oldData(), []);
  if (!d.data?.found) return null;
  const { found, imported, pending } = d.data;
  const go = async () => {
    try {
      await api.importOldData();
      d.reload();
      toast("Quit AI LifeOS from the tray and open it again to import");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Section title="Data from before the app" icon={<DatabaseBackup size={15} className="text-accent" />}
      actions={<button className="btn-outline" onClick={go} disabled={pending}>{pending ? "Imports on next start" : imported ? "Import again" : "Import old data"}</button>}>
      <p className="text-sm">
        {imported ? "Imported. " : ""}Data saved while LifeOS ran with Microsoft Store Python (agents' models, folders, lessons, gallery, chats)
        is in <code className="break-all text-xs">{found}</code>.
      </p>
      <p className="mt-1 text-xs text-muted">
        {pending ? "Quit AI LifeOS from the tray and open it again; it imports before loading anything. "
          : "Importing replaces what's in the app now. "}The app's current database is kept in a backup-before-import folder in %APPDATA%\AI-LifeOS.
      </p>
    </Section>
  );
}
