import { useState } from "react";
import {
  Archive, Cloud, Download, FolderOpen, Pencil, Play, Plug, Plus, RotateCcw, Trash2, Upload, Volume2, X,
} from "lucide-react";
import {
  api, type CustomTool, type Settings as S, type Workspace, type WorkspaceItem,
} from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, Section, Spinner, notifyChanged, useToast } from "./ui";

type Props = { form: S; setForm: (s: S) => void; save: (patch: Partial<S>) => Promise<void> };

const KIND_HINT: Record<WorkspaceItem["kind"], string> = {
  file: "C:\\Users\\you\\Documents\\notes.docx",
  folder: "C:\\Users\\you\\Documents\\School",
  url: "https://...",
  app: "C:\\Program Files\\...\\app.exe, a .lnk shortcut, or spotify:",
};

// ---------------------------------------------------------------- workspaces (dashboard)

function WorkspaceEditor({ initial, onDone }: { initial?: Workspace; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(initial?.name ?? "");
  const [items, setItems] = useState<WorkspaceItem[]>(initial?.items ?? [{ kind: "folder", target: "" }]);
  const [busy, setBusy] = useState(false);
  const setItem = (i: number, patch: Partial<WorkspaceItem>) => setItems(items.map((it, j) => (j === i ? { ...it, ...patch } : it)));

  const submit = async () => {
    setBusy(true);
    try {
      await api.saveWorkspace({ name, items: items.filter((i) => i.target.trim()) }, initial?.id);
      toast(`Saved workspace "${name}"`);
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-line p-3">
      <input className="input" placeholder="Name, e.g. Study" value={name} onChange={(e) => setName(e.target.value)} />
      {items.map((it, i) => (
        <div key={i} className="flex gap-2">
          <select className="input w-24" value={it.kind} onChange={(e) => setItem(i, { kind: e.target.value as WorkspaceItem["kind"] })}>
            <option value="folder">Folder</option>
            <option value="file">File</option>
            <option value="url">Web page</option>
            <option value="app">App</option>
          </select>
          <input className="input flex-1 font-mono text-xs" placeholder={KIND_HINT[it.kind]} value={it.target}
            onChange={(e) => setItem(i, { target: e.target.value })} />
          <button className="btn-ghost px-1.5" title="Remove" onClick={() => setItems(items.filter((_, j) => j !== i))}><X size={14} /></button>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost text-xs" onClick={() => setItems([...items, { kind: "url", target: "" }])} disabled={items.length >= 20}>
          <Plus size={13} /> Add item
        </button>
        <span className="flex-1" />
        <button className="btn-ghost text-xs" onClick={onDone}>Cancel</button>
        <button className="btn-primary text-xs" onClick={submit} disabled={busy || !name.trim()}>{busy ? <Spinner size={13} /> : null} Save</button>
      </div>
    </div>
  );
}

export function WorkspacesCard({ className = "" }: { className?: string }) {
  const toast = useToast();
  const spaces = useAsync(api.workspaces, []);
  const [editing, setEditing] = useState<Workspace | "new" | null>(null);

  const open = async (w: Workspace) => {
    try {
      const r = await api.openWorkspace(w.id);
      toast(r.failed.length ? `Opened ${r.opened.length}; couldn't open ${r.failed.map((f) => f.target).join(", ")}` : `Opened ${w.name}`,
        r.failed.length ? "error" : "ok");
      spaces.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Section title="Workspaces" icon={<FolderOpen size={15} className="text-accent" />} className={className}
      actions={!editing && <button className="btn-ghost text-xs" onClick={() => setEditing("new")}><Plus size={13} /> New</button>}>
      {editing ? (
        <WorkspaceEditor initial={editing === "new" ? undefined : editing} onDone={() => { setEditing(null); spaces.reload(); notifyChanged(); }} />
      ) : (spaces.data ?? []).length === 0 ? (
        <Empty>Save the files, folders, pages and apps you use together, then open them in one click or say "prepare my study workspace".</Empty>
      ) : (
        <ul className="divide-y divide-line">
          {(spaces.data ?? []).map((w) => (
            <li key={w.id} className="group flex items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{w.name}</div>
                <div className="text-xs text-muted">{w.items.length} item(s){w.last_opened_at ? ` · opened ${ago(w.last_opened_at)}` : ""}</div>
              </div>
              <button className="btn-ghost px-1.5 opacity-0 group-hover:opacity-100" title="Edit" onClick={() => setEditing(w)}><Pencil size={13} /></button>
              <button className="btn-ghost px-1.5 opacity-0 hover:text-danger group-hover:opacity-100" title="Delete"
                onClick={async () => { await api.deleteWorkspace(w.id); spaces.reload(); }}><Trash2 size={13} /></button>
              <button className="btn-outline text-xs" onClick={() => open(w)}><Play size={12} /> Open</button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- cloud folders (settings)

export function CloudFolders({ onApproved }: { onApproved: () => void }) {
  const toast = useToast();
  const found = useAsync(api.cloudFolders, []);
  const list = found.data ?? [];
  if (!list.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium"><Cloud size={13} className="text-accent" /> Cloud storage on this PC</div>
      <p className="mb-2 text-xs text-muted">Files that are online-only are listed by name without downloading them.</p>
      <ul className="space-y-1">
        {list.map((c) => (
          <li key={c.path} className="flex items-center gap-2 text-sm">
            <span className="chip">{c.provider}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs">{c.path}</span>
            {c.approved ? <span className="text-xs text-ok">Approved</span> : (
              <button className="btn-outline text-xs" onClick={async () => {
                try {
                  const f = await api.addFolder(c.path, c.provider);
                  toast(`Approved ${f.path} (${f.file_count} files indexed)`);
                  found.reload();
                  onApproved();
                } catch (e) {
                  toast((e as Error).message, "error");
                }
              }}>Approve</button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- custom tools (settings)

type Draft = { name: string; kind: "webhook" | "prompt"; description: string; url: string; method: string; body: string;
  secret_header: string; secret: string; template: string };

const blank: Draft = { name: "", kind: "webhook", description: "", url: "", method: "POST", body: '{"text": "{{input}}"}',
  secret_header: "", secret: "", template: "" };

function toDraft(t: CustomTool): Draft {
  return { ...blank, name: t.name, kind: t.kind, description: t.description, url: t.config.url ?? "", method: t.config.method ?? "POST",
    body: t.config.body ?? "", secret_header: t.config.secret_header ?? "", template: t.config.template ?? "" };
}

export function CustomToolsSettings() {
  const toast = useToast();
  const list = useAsync(api.customTools, []);
  const [editing, setEditing] = useState<{ id?: number; d: Draft } | null>(null);
  const [testInput, setTestInput] = useState<Record<number, string>>({});
  const [result, setResult] = useState<Record<number, string>>({});

  const save = async () => {
    if (!editing) return;
    const d = editing.d;
    const config = d.kind === "webhook" ? { url: d.url, method: d.method, body: d.body, secret_header: d.secret_header } : { template: d.template };
    try {
      await api.saveCustomTool({ name: d.name, kind: d.kind, description: d.description, config, enabled: true,
        secret: d.kind === "webhook" && d.secret ? d.secret : null }, editing.id);
      setEditing(null);
      list.reload();
      toast(`Saved ${d.name}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const exportTool = async (t: CustomTool) => {
    const data = await api.exportCustomTool(t.id);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${t.name}.lifeos-tool.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importTool = async (file: File) => {
    try {
      const t = await api.importCustomTool(JSON.parse(await file.text()));
      list.reload();
      toast(`Imported ${t.name}${t.kind === "webhook" && t.config.secret_header ? ". Add its key with Edit." : ""}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const test = async (t: CustomTool) => {
    setResult({ ...result, [t.id]: "…" });
    try {
      const r = await api.testCustomTool(t.id, testInput[t.id] ?? "test");
      setResult({ ...result, [t.id]: (r.status ? `HTTP ${r.status}\n` : "") + r.response });
    } catch (e) {
      setResult({ ...result, [t.id]: (e as Error).message });
    }
  };

  const set = (patch: Partial<Draft>) => editing && setEditing({ ...editing, d: { ...editing.d, ...patch } });

  return (
    <Section title="Custom tools" icon={<Plug size={15} className="text-accent" />}
      actions={
        <div className="flex gap-1">
          <label className="btn-ghost cursor-pointer text-xs"><Upload size={13} /> Import
            <input type="file" accept=".json,application/json" className="hidden" onChange={(e) => e.target.files?.[0] && importTool(e.target.files[0])} />
          </label>
          {!editing && <button className="btn-ghost text-xs" onClick={() => setEditing({ d: { ...blank } })}><Plus size={13} /> New</button>}
        </div>
      }>
      <p className="mb-3 text-xs text-muted">
        Connect anything with a web address (Home Assistant, Todoist or Notion automations, Zapier, n8n, IFTTT) or save an
        instruction for the local model. The assistant and crew can use them by name; web calls ask you first
        (custom_webhook in Tool permissions). Keys are kept in the OS credential store and never exported.
      </p>
      {editing && (
        <div className="mb-3 space-y-2 rounded-lg border border-line p-3">
          <div className="grid gap-2 sm:grid-cols-3">
            <input className="input" placeholder="Name, e.g. Add to Todoist" value={editing.d.name} onChange={(e) => set({ name: e.target.value })} />
            <select className="input" value={editing.d.kind} onChange={(e) => set({ kind: e.target.value as Draft["kind"] })}>
              <option value="webhook">Web request</option>
              <option value="prompt">Saved instruction</option>
            </select>
            <input className="input" placeholder="What it does (helps the AI pick it)" value={editing.d.description}
              onChange={(e) => set({ description: e.target.value })} />
          </div>
          {editing.d.kind === "webhook" ? (
            <>
              <div className="flex gap-2">
                <select className="input w-24" value={editing.d.method} onChange={(e) => set({ method: e.target.value })}>
                  {["GET", "POST", "PUT", "PATCH"].map((m) => <option key={m}>{m}</option>)}
                </select>
                <input className="input flex-1 font-mono text-xs" placeholder="https://... ({{input}} allowed)" value={editing.d.url}
                  onChange={(e) => set({ url: e.target.value })} />
              </div>
              {editing.d.method !== "GET" && (
                <textarea className="input font-mono text-xs" rows={3} placeholder='{"text": "{{input}}"}' value={editing.d.body}
                  onChange={(e) => set({ body: e.target.value })} />
              )}
              <div className="grid gap-2 sm:grid-cols-2">
                <input className="input" placeholder="Key header (optional), e.g. Authorization" value={editing.d.secret_header}
                  onChange={(e) => set({ secret_header: e.target.value })} />
                <input className="input" type="password" placeholder={editing.id ? "Key value (leave empty to keep)" : "Key value, e.g. Bearer abc123"}
                  value={editing.d.secret} onChange={(e) => set({ secret: e.target.value })} disabled={!editing.d.secret_header} />
              </div>
            </>
          ) : (
            <textarea className="input text-sm" rows={3} placeholder="Rewrite this as my weekly report: {{input}}" value={editing.d.template}
              onChange={(e) => set({ template: e.target.value })} />
          )}
          <div className="flex justify-end gap-2">
            <button className="btn-ghost text-xs" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary text-xs" onClick={save}>Save</button>
          </div>
        </div>
      )}
      {(list.data ?? []).length === 0 && !editing ? <Empty>No custom tools yet.</Empty> : (
        <ul className="divide-y divide-line">
          {(list.data ?? []).map((t) => (
            <li key={t.id} className="py-2">
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm">
                    {t.name}
                    <span className="chip">{t.kind === "webhook" ? `${t.config.method} web` : "instruction"}</span>
                    {t.has_secret && <span className="chip">key stored</span>}
                  </div>
                  <div className="truncate text-xs text-muted">{t.description || t.config.url || t.config.template}</div>
                </div>
                <button className="btn-ghost px-1.5" title="Export" onClick={() => exportTool(t)}><Download size={13} /></button>
                <button className="btn-ghost px-1.5" title="Edit" onClick={() => setEditing({ id: t.id, d: toDraft(t) })}><Pencil size={13} /></button>
                <button className="btn-ghost px-1.5 hover:text-danger" title="Delete"
                  onClick={async () => { await api.deleteCustomTool(t.id); list.reload(); }}><Trash2 size={13} /></button>
              </div>
              <div className="mt-1 flex gap-2">
                <input className="input flex-1 text-xs" placeholder="Test input" value={testInput[t.id] ?? ""}
                  onChange={(e) => setTestInput({ ...testInput, [t.id]: e.target.value })} />
                <button className="btn-outline text-xs" onClick={() => test(t)}><Play size={12} /> Test</button>
              </div>
              {result[t.id] && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-panel-2 p-2 text-xs">{result[t.id]}</pre>}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- backup & sync (settings)

const mb = (b: number) => `${(b / 1048576).toFixed(1)} MB`;

export function BackupSettings({ form, setForm, save }: Props) {
  const toast = useToast();
  const cloud = useAsync(api.cloudFolders, []);
  const list = useAsync(api.backups, []);
  const [busy, setBusy] = useState(false);

  const backupNow = async () => {
    setBusy(true);
    try {
      const r = await api.backupNow();
      toast(`Backed up (${mb(r.bytes)}) to ${r.path}`);
      list.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (path: string) => {
    try {
      await api.restoreBackup(path);
      toast("Restore is set up. Quit LifeOS from the tray icon and start it again to finish.", "info");
      list.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const pending = list.data?.pending_restore;
  return (
    <Section title="Backup & sync" icon={<Archive size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">
        Backs up the crew, memories, tasks, workflows, lessons and settings. Pick a folder inside OneDrive, Google Drive or
        Dropbox and your other PCs can restore from it. Passwords and keys stay in each PC's credential store and are entered again there.
      </p>
      <label className="label">Backup folder</label>
      <div className="flex gap-2">
        <input className="input flex-1 font-mono text-xs" placeholder="C:\Users\you\OneDrive\AI-LifeOS backups" value={form.backup_folder ?? ""}
          onChange={(e) => setForm({ ...form, backup_folder: e.target.value })} onBlur={() => save({ backup_folder: form.backup_folder.trim() })} />
      </div>
      {(cloud.data ?? []).length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {(cloud.data ?? []).map((c) => {
            const sep = c.path.includes("\\") ? "\\" : "/";
            const target = `${c.path}${sep}AI-LifeOS backups`;
            return <button key={c.path} className="chip hover:border-accent" onClick={() => save({ backup_folder: target })}>Use {c.provider}</button>;
          })}
        </div>
      )}
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--color-accent)]" checked={form.backup_daily ?? false} onChange={(e) => save({ backup_daily: e.target.checked })} />
          Back up every day
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--color-accent)]" checked={form.backup_include_media ?? false}
            onChange={(e) => save({ backup_include_media: e.target.checked })} />
          Include generated images and videos
        </label>
        <div className="flex items-center gap-2 text-sm">
          Keep
          <input type="number" min={1} max={100} className="input w-16" value={form.backup_keep ?? 7}
            onChange={(e) => setForm({ ...form, backup_keep: Number(e.target.value) })}
            onBlur={() => save({ backup_keep: Math.min(100, Math.max(1, Number(form.backup_keep) || 7)) })} />
          backups
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="btn-primary" onClick={backupNow} disabled={busy || !form.backup_folder}>{busy ? <Spinner /> : <Archive size={14} />} Back up now</button>
        {form.backup_last_at && <span className="text-xs text-muted">Last backup {ago(form.backup_last_at)}</span>}
      </div>
      {pending && (
        <p className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-2 text-xs text-warn">
          Restoring {pending} on the next start. Quit LifeOS from the tray icon and open it again. Your current data is kept in a backup-before-restore folder.
        </p>
      )}
      {(list.data?.backups ?? []).length > 0 && (
        <ul className="mt-3 divide-y divide-line">
          {(list.data?.backups ?? []).map((b) => (
            <li key={b.path} className="flex items-center gap-2 py-1.5 text-sm">
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{b.name}</span>
              <span className="text-xs text-muted">{mb(b.bytes)} · {ago(b.created_at)}</span>
              <button className="btn-ghost text-xs" onClick={() => restore(b.path)}><RotateCcw size={12} /> Restore</button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- voice replies

export function speak(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const clean = text.replace(/```[\s\S]*?```/g, " code block. ").replace(/[*_#`>|]/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").slice(0, 1500);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(clean));
}

export function VoiceSettings({ form, save }: Props) {
  return (
    <label className="mt-3 flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={form.speak_replies ?? false}
        onChange={(e) => save({ speak_replies: e.target.checked })} />
      <span className="inline-flex items-center gap-1"><Volume2 size={13} /> Read the assistant's replies aloud
        <span className="block text-xs text-muted">&nbsp;Uses the voice built into Windows, so no GPU or download is needed.</span></span>
    </label>
  );
}
