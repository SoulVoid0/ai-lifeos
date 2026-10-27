import { useEffect, useState } from "react";
import { Cpu, FolderPlus, Palette, Shield, SlidersHorizontal, Trash2, Zap } from "lucide-react";
import { api, type Settings as S } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ErrorBox, PageHeader, Section, Spinner, notifyChanged, useToast } from "../components/ui";

const ACCENTS = ["violet", "blue", "emerald", "cyan", "rose", "amber"];
const ACCENT_HEX: Record<string, string> = { violet: "#8b5cf6", blue: "#3b82f6", emerald: "#10b981", rose: "#f43f5e", amber: "#f59e0b", cyan: "#06b6d4" };

export default function Settings() {
  const toast = useToast();
  const settings = useAsync(api.settings, []);
  const folders = useAsync(api.folders, []);
  const tools = useAsync(api.tools, []);
  const [form, setForm] = useState<S | null>(null);
  const [newFolder, setNewFolder] = useState("");
  const [llm, setLlm] = useState<{ ok: boolean; models: string[] } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => { if (settings.data) setForm(settings.data); }, [settings.data]);

  if (!form) return <div className="p-8">{settings.error ? <ErrorBox error={settings.error} /> : <Spinner />}</div>;

  const save = async (patch: Partial<S>) => {
    try {
      const s = await api.updateSettings(patch);
      setForm(s);
      settings.setData(s);
      tools.reload();
      notifyChanged();
      toast("Settings saved");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const testModel = async () => {
    setTesting(true);
    try {
      await api.updateSettings({ ollama_url: form.ollama_url, chat_model: form.chat_model, embed_model: form.embed_model });
      setLlm(await api.llmStatus());
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };

  const addFolder = async () => {
    if (!newFolder.trim()) return;
    try {
      const f = await api.addFolder(newFolder.trim());
      setNewFolder("");
      folders.reload();
      toast(`Approved ${f.path} (${f.file_count} files indexed)`);
      notifyChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <PageHeader title="Settings" subtitle="Models, permissions, privacy and personalization. Everything stays on this computer." />

      <Section title="Approved folders" icon={<FolderPlus size={15} className="text-accent" />}>
        <p className="mb-3 text-xs text-muted">
          LifeOS can only see and change files inside these folders. Your whole home folder or system folders can't be approved.
        </p>
        <div className="mb-3 flex gap-2">
          <input className="input flex-1" placeholder="/home/you/Downloads or C:\Users\you\Downloads" value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addFolder()} />
          <button className="btn-primary" onClick={addFolder}><FolderPlus size={14} /> Approve</button>
        </div>
        <ul className="divide-y divide-line">
          {(folders.data ?? []).map((f) => (
            <li key={f.id} className="flex items-center gap-3 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-xs">{f.path}</div>
                <div className="text-xs text-muted">{f.file_count} files · indexed {ago(f.last_indexed_at)}</div>
              </div>
              <button className="btn-danger" onClick={async () => { await api.removeFolder(f.id); folders.reload(); notifyChanged(); }}>
                <Trash2 size={13} /> Revoke
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Local AI (Ollama)" icon={<Cpu size={15} className="text-accent" />}>
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <label className="label">Server URL</label>
            <input className="input" value={form.ollama_url} onChange={(e) => setForm({ ...form, ollama_url: e.target.value })} />
          </div>
          <div>
            <label className="label">Chat model</label>
            <input className="input" list="models" value={form.chat_model} onChange={(e) => setForm({ ...form, chat_model: e.target.value })} />
          </div>
          <div>
            <label className="label">Embedding model</label>
            <input className="input" list="models" value={form.embed_model} onChange={(e) => setForm({ ...form, embed_model: e.target.value })} />
          </div>
          <datalist id="models">{llm?.models.map((m) => <option key={m} value={m} />)}</datalist>
        </div>
        <div className="mt-3">
          <label className="label">Planner</label>
          <select className="input sm:w-96" value={form.planner} onChange={(e) => save({ planner: e.target.value as S["planner"] })}>
            <option value="auto">Hybrid: built-in rules first, local model for the rest</option>
            <option value="llm">Always use the local model</option>
            <option value="rules">Rules only (no AI model)</option>
          </select>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="btn-primary" onClick={testModel} disabled={testing}>{testing ? <Spinner /> : <Zap size={14} />} Save & test connection</button>
          {llm && (
            <span className={`text-sm ${llm.ok ? "text-ok" : "text-danger"}`}>
              {llm.ok
                ? `Connected. ${llm.models.length} models installed${llm.models.includes(form.chat_model) ? "" : `; run "ollama pull ${form.chat_model}"`}.`
                : "Not reachable. Install Ollama from ollama.com and start it."}
            </span>
          )}
        </div>
      </Section>

      <Section title="Tool permissions" icon={<Shield size={15} className="text-accent" />}>
        <p className="mb-3 text-xs text-muted">
          Auto runs immediately, Confirm asks you first, Deny blocks the tool. Destructive tools always ask. Pausing automation (sidebar) makes every write ask.
        </p>
        <div className="divide-y divide-line">
          {(tools.data ?? []).map((t) => (
            <div key={t.name} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-mono">{t.name}</span>
                  <span className={`chip ${t.risk === "read" ? "" : t.risk === "write" ? "border-warn/40 text-warn" : "border-danger/40 text-danger"}`}>{t.risk}</span>
                </div>
                <div className="truncate text-xs text-muted">{t.description}</div>
              </div>
              <select
                className="input w-32"
                value={form.tool_policies[t.name] ?? t.default_policy}
                onChange={(e) => save({ tool_policies: { ...form.tool_policies, [t.name]: e.target.value as "auto" | "confirm" | "deny" } })}
              >
                <option value="auto" disabled={t.risk === "destructive"}>auto</option>
                <option value="confirm">confirm</option>
                <option value="deny">deny</option>
              </select>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Proactive suggestions" icon={<SlidersHorizontal size={15} className="text-accent" />}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label">How often LifeOS suggests things</label>
            <select className="input" value={form.proactive_level} onChange={(e) => save({ proactive_level: e.target.value as S["proactive_level"] })}>
              <option value="off">Off</option>
              <option value="low">Low: deadlines only</option>
              <option value="normal">Normal: deadlines, files, projects</option>
            </select>
          </div>
          <div>
            <label className="label">Loose files before suggesting cleanup</label>
            <input type="number" className="input" value={form.downloads_clutter_threshold}
              onChange={(e) => setForm({ ...form, downloads_clutter_threshold: Number(e.target.value) })}
              onBlur={() => save({ downloads_clutter_threshold: form.downloads_clutter_threshold })} />
          </div>
          <div>
            <label className="label">Large file threshold (MB)</label>
            <input type="number" className="input" value={form.large_file_mb}
              onChange={(e) => setForm({ ...form, large_file_mb: Number(e.target.value) })}
              onBlur={() => save({ large_file_mb: form.large_file_mb })} />
          </div>
          <div>
            <label className="label">Unused after (days)</label>
            <input type="number" className="input" value={form.stale_days}
              onChange={(e) => setForm({ ...form, stale_days: Number(e.target.value) })}
              onBlur={() => save({ stale_days: form.stale_days })} />
          </div>
        </div>
        {form.dismissed_suggestions.length > 0 && (
          <button className="btn-ghost mt-3 text-xs" onClick={() => save({ dismissed_suggestions: [] })}>
            Restore {form.dismissed_suggestions.length} dismissed suggestion(s)
          </button>
        )}
      </Section>

      <Section title="Personalization" icon={<Palette size={15} className="text-accent" />}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label">Your name</label>
            <input className="input" value={form.user_name} onChange={(e) => setForm({ ...form, user_name: e.target.value })}
              onBlur={() => save({ user_name: form.user_name })} />
          </div>
          <div>
            <label className="label">Accent color</label>
            <div className="flex gap-2 pt-1">
              {ACCENTS.map((a) => (
                <button key={a} title={a} onClick={() => save({ theme_accent: a })}
                  className={`h-7 w-7 rounded-full border-2 ${form.theme_accent === a ? "border-text" : "border-transparent"}`}
                  style={{ background: ACCENT_HEX[a] }} />
              ))}
            </div>
          </div>
        </div>
      </Section>
    </div>
  );
}
