import { useMemo, useState } from "react";
import { Save, Trash2, X, Zap } from "lucide-react";
import { api, type Agent, type ToolInfo } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { AGENT_COLORS, AGENT_ICONS, AgentAvatar } from "./crew";
import { Spinner, notifyChanged, useToast } from "./ui";

const BLANK: Partial<Agent> = {
  name: "", role: "Specialist", description: "", persona: "", model: "", tools: [], color: "slate", icon: "bot", max_steps: 6, enabled: true, candidates: [],
};

const MODEL_HINTS = [
  "openai:gpt-4o-mini", "anthropic:claude-sonnet-4-5", "gemini:gemini-2.5-flash", "openrouter:qwen/qwen3-32b",
  "groq:llama-3.3-70b-versatile", "mistral:mistral-small-latest", "deepseek:deepseek-chat", "xai:grok-3-mini", "custom:local-model",
];

/** Create or edit an agent: identity, persona, model and its tool grant. */
export function AgentEditor({ agent, tools, onClose }: { agent: Agent | null; tools: ToolInfo[]; onClose: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState<Partial<Agent>>(agent ?? BLANK);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<string | null>(null);
  const models = useAsync(api.crewModels, []);
  const creating = !agent;

  const grouped = useMemo(() => {
    const g: Record<string, ToolInfo[]> = { read: [], write: [], destructive: [] };
    for (const t of tools) g[t.risk].push(t);
    return g;
  }, [tools]);

  const set = <K extends keyof Agent>(k: K, v: Agent[K]) => setForm((f) => ({ ...f, [k]: v }));
  const toggleTool = (name: string) =>
    set("tools", (form.tools ?? []).includes(name) ? (form.tools ?? []).filter((t) => t !== name) : [...(form.tools ?? []), name]);

  const save = async () => {
    setBusy(true);
    try {
      const body = { name: form.name, role: form.role, description: form.description, persona: form.persona, model: form.model,
        tools: form.tools, color: form.color, icon: form.icon, max_steps: form.max_steps, enabled: form.enabled,
        candidates: form.candidates ?? [] };
      if (creating) await api.createAgent({ ...body, name: form.name ?? "" });
      else await api.updateAgent(agent.slug, body);
      toast(creating ? `Recruited ${form.name}` : `Saved ${form.name}`);
      notifyChanged();
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setTest("…");
    const r = await api.testModel(form.model || models.data?.default || "");
    setTest(r.ok ? `OK in ${r.seconds}s: "${r.reply}"` : `Failed: ${r.error}`);
  };

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4" onClick={onClose}>
      <div className="card max-h-full w-full max-w-2xl overflow-y-auto p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center gap-3">
          <AgentAvatar icon={form.icon ?? "bot"} color={form.color ?? "slate"} size={44} state={form.enabled ? "idle" : "offline"} />
          <div className="flex-1">
            <div className="text-lg font-semibold">{creating ? "Recruit an agent" : `Edit ${agent.name}`}</div>
            {!creating && <div className="text-xs text-muted">{agent.builtin ? "Built-in agent" : "Custom agent"} · {agent.slug}</div>}
          </div>
          <button className="btn-ghost" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className="label">Name</label><input className="input" value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} /></div>
          <div><label className="label">Role</label><input className="input" value={form.role ?? ""} onChange={(e) => set("role", e.target.value)} /></div>
          <div className="sm:col-span-2"><label className="label">What it does (the Overseer reads this to delegate)</label>
            <input className="input" value={form.description ?? ""} onChange={(e) => set("description", e.target.value)} /></div>
          <div className="sm:col-span-2"><label className="label">Persona and instructions</label>
            <textarea className="input min-h-20" value={form.persona ?? ""} onChange={(e) => set("persona", e.target.value)} /></div>
          <div className="sm:col-span-2">
            <label className="label">Model (empty = default {models.data?.default ?? ""})</label>
            <div className="flex gap-2">
              <input className="input flex-1 font-mono" list="agent-models" placeholder="llama3.1:8b · openai:gpt-4o-mini · anthropic:…"
                value={form.model ?? ""} onChange={(e) => set("model", e.target.value)} />
              <button className="btn-outline" onClick={runTest}><Zap size={14} /> Test</button>
            </div>
            <datalist id="agent-models">
              {(models.data?.local ?? []).map((m) => <option key={m} value={m} />)}
              {MODEL_HINTS.map((m) => <option key={m} value={m} />)}
            </datalist>
            {test && <div className={`mt-1 text-xs ${test.startsWith("OK") ? "text-ok" : test === "…" ? "text-muted" : "text-danger"}`}>{test}</div>}
            <div className="mt-1 text-[11px] text-muted">Cloud models need Settings → Cloud AI turned on and an API key. Local Ollama models are free.</div>
            {agent?.model_missing && (
              <div className="mt-1 text-[11px] text-warn">
                {agent.wanted_model} isn't installed, so {agent.name} uses {agent.effective_model} for now. Install it with
                <code className="ml-1 select-all rounded bg-panel-2 px-1">ollama pull {agent.wanted_model}</code>
              </div>
            )}
            {(agent?.recommended?.length ?? 0) > 0 && (
              <div className="mt-2 rounded-lg border border-line p-2">
                <div className="mb-1 text-[11px] font-medium text-muted">Recommended local models for this role</div>
                {agent!.recommended.map((r) => {
                  const have = (models.data?.local ?? []).some((m) => m === r.model || m === `${r.model}:latest`);
                  return (
                    <div key={r.model} className="flex flex-wrap items-center gap-2 py-0.5 text-xs">
                      <button className="chip font-mono hover:border-accent" onClick={() => set("model", r.model)} title="Use this model">{r.model}</button>
                      <span className="text-muted">{r.size} · {r.why}</span>
                      {have ? <span className="text-ok">installed</span>
                        : <code className="select-all rounded bg-panel-2 px-1 text-[10px]">ollama pull {r.model}</code>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="sm:col-span-2">
            <label className="label">Other models to try (auto-routing)</label>
            <input className="input font-mono" list="agent-models" placeholder="qwen2.5:7b, mistral:7b"
              value={(form.candidates ?? []).join(", ")}
              onChange={(e) => set("candidates", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
            <div className="mt-1 text-[11px] text-muted">With auto-routing on (Control Center → Learning), each model is tried a few times and the one with the best review scores and your ratings gets this agent's work.</div>
          </div>
          <div>
            <label className="label">Color</label>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(AGENT_COLORS).map(([k, hex]) => (
                <button key={k} title={k} onClick={() => set("color", k)} style={{ background: hex }}
                  className={`h-6 w-6 rounded-full border-2 ${form.color === k ? "border-text" : "border-transparent"}`} />
              ))}
            </div>
          </div>
          <div>
            <label className="label">Icon</label>
            <div className="flex flex-wrap gap-1">
              {Object.entries(AGENT_ICONS).map(([k, Icon]) => (
                <button key={k} title={k} onClick={() => set("icon", k)}
                  className={`grid h-7 w-7 place-items-center rounded-lg border ${form.icon === k ? "border-accent text-accent" : "border-line text-muted"}`}>
                  <Icon size={14} />
                </button>
              ))}
            </div>
          </div>
          <div><label className="label">Max steps per task</label>
            <input type="number" min={1} max={20} className="input" value={form.max_steps ?? 6} onChange={(e) => set("max_steps", Number(e.target.value))} /></div>
          <label className="mt-5 flex items-center gap-2 text-sm">
            <input type="checkbox" className="accent-[var(--color-accent)]" checked={form.enabled ?? true}
              disabled={agent?.slug === "overseer"} onChange={(e) => set("enabled", e.target.checked)} /> Enabled
          </label>
        </div>

        <div className="mt-4">
          <label className="label">Tools this agent may use ({(form.tools ?? []).length})</label>
          <p className="mb-2 text-[11px] text-muted">A tool grant is a capability. Every call still follows your permission policy, so writes that need approval still ask you.</p>
          {(["read", "write", "destructive"] as const).map((risk) => (
            <div key={risk} className="mb-2">
              <div className={`mb-1 text-[11px] font-semibold uppercase ${risk === "read" ? "text-muted" : risk === "write" ? "text-warn" : "text-danger"}`}>{risk}</div>
              <div className="grid gap-1 sm:grid-cols-2">
                {grouped[risk].map((t) => (
                  <label key={t.name} className="flex items-start gap-2 rounded-lg px-2 py-1 text-xs hover:bg-panel-2" title={t.description}>
                    <input type="checkbox" className="mt-0.5 accent-[var(--color-accent)]" checked={(form.tools ?? []).includes(t.name)}
                      onChange={() => toggleTool(t.name)} />
                    <span><span className="font-mono">{t.name}</span>{t.policy !== "auto" && <span className="ml-1 text-muted">({t.policy})</span>}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <button className="btn-primary" disabled={busy || !form.name?.trim()} onClick={save}>{busy ? <Spinner /> : <Save size={14} />} Save</button>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          {agent && !agent.builtin && (
            <button className="btn-danger ml-auto" onClick={async () => {
              await api.deleteAgent(agent.slug); toast(`Dismissed ${agent.name}`); notifyChanged(); onClose();
            }}><Trash2 size={13} /> Dismiss</button>
          )}
        </div>
      </div>
    </div>
  );
}
