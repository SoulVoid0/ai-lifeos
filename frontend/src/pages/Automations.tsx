import { useEffect, useState } from "react";
import {
  ArrowDown, ArrowUp, Bell, Bot, ChevronDown, ChevronRight, Clock, Pencil, Play, Plus, Save, Sparkles, Trash2, Wand2,
  Workflow as WorkflowIcon, Wrench, X,
} from "lucide-react";
import {
  api, type Condition, type JsonProp, type ToolInfo, type Trigger, type Workflow, type WorkflowDraft, type WorkflowMeta,
  type WorkflowRun, type WorkflowStep,
} from "../lib/api";
import { ago, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, ErrorBox, PageHeader, Section, Spinner, notifyChanged, useToast } from "../components/ui";

type Editing = { id: number | null; draft: WorkflowDraft & { enabled?: boolean } };

const RUN_TONE: Record<string, string> = {
  done: "border-ok/40 text-ok", failed: "border-danger/40 text-danger", skipped: "text-muted", running: "border-accent/40 text-accent",
};

export default function Automations() {
  const toast = useToast();
  const flows = useAsync(api.workflows, [], 10000);
  const runs = useAsync(() => api.workflowRuns(), [], 5000);
  const templates = useAsync(api.workflowTemplates, []);
  const meta = useAsync(api.workflowMeta, []);
  const tools = useAsync(api.tools, []);
  const agents = useAsync(api.agents, []);
  const [text, setText] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);

  useEffect(() => {
    const h = () => { flows.reload(); runs.reload(); };
    window.addEventListener("lifeos:changed", h);
    return () => window.removeEventListener("lifeos:changed", h);
  }, [flows.reload, runs.reload]);

  const draft = async () => {
    if (!text.trim()) return;
    setDrafting(true);
    try {
      const d = await api.draftWorkflow(text.trim());
      setEditing({ id: null, draft: d });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setDrafting(false);
    }
  };

  const toggle = async (w: Workflow) => {
    await api.updateWorkflow(w.id, { enabled: !w.enabled });
    flows.reload();
  };

  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Automations"
        subtitle="Workflows run on a schedule: trigger → conditions → steps (tools, crew missions, notifications) → summary. Writes still ask for approval."
        actions={<button className="btn-primary" onClick={() => setEditing({ id: null, draft: blank() })}><Plus size={14} /> New workflow</button>} />

      <div className="card mb-4 p-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); draft(); }}>
          <Wand2 size={18} className="mt-2 shrink-0 text-accent" />
          <input className="input" value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Describe it: “Every Friday at 5pm, organize my downloads, summarize my completed tasks and plan next week”" />
          <button className="btn-primary" disabled={drafting || !text.trim()}>{drafting ? <Spinner /> : <Sparkles size={14} />} Draft</button>
        </form>
        <p className="mt-1.5 pl-7 text-[11px] text-muted">You review the draft before anything is saved.</p>
      </div>

      <ErrorBox error={flows.error} />
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          {!(flows.data ?? []).length && <Section title="Your workflows"><Empty>No workflows yet. Draft one above or start from a template.</Empty></Section>}
          {(flows.data ?? []).map((w) => (
            <div key={w.id} className="card p-4">
              <div className="flex flex-wrap items-start gap-3">
                <WorkflowIcon size={18} className={`mt-0.5 ${w.enabled ? "text-accent" : "text-muted"}`} />
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{w.name}</div>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
                    <span className="flex items-center gap-1"><Clock size={12} /> {w.trigger_text}</span>
                    {w.enabled && w.next_run_at && <span>next {when(w.next_run_at)}</span>}
                    {w.conditions.length > 0 && <span>{w.conditions.length} condition(s)</span>}
                    <span>{w.steps.length} step(s)</span>
                    {w.last_run && <span className={RUN_TONE[w.last_run.status]?.split(" ").pop()}>last: {w.last_run.status} {ago(w.last_run.finished_at)}</span>}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    {w.steps.map((s, i) => (
                      <span key={i} className="flex items-center gap-1">
                        {i > 0 && <ChevronRight size={12} className="text-muted" />}
                        <StepChip step={s} />
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <label className="mr-2 flex items-center gap-1.5 text-xs text-muted">
                    <input type="checkbox" className="accent-[var(--color-accent)]" checked={w.enabled} onChange={() => toggle(w)} /> on
                  </label>
                  <button className="btn-outline text-xs" onClick={async () => { await api.runWorkflow(w.id); toast(`Running ${w.name}`, "info"); setTimeout(runs.reload, 800); }}>
                    <Play size={13} /> Run now
                  </button>
                  <button className="btn-ghost" title="Edit" onClick={() => setEditing({ id: w.id, draft: w })}><Pencil size={14} /></button>
                  <button className="btn-ghost" title="Delete" onClick={async () => { await api.deleteWorkflow(w.id); flows.reload(); notifyChanged(); }}><Trash2 size={14} /></button>
                </div>
              </div>
            </div>
          ))}

          <Section title="Run history" icon={<Clock size={15} className="text-accent" />}>
            <RunHistory runs={runs.data ?? []} />
          </Section>
        </div>

        <Section title="Templates" icon={<Sparkles size={15} className="text-accent" />}>
          <div className="space-y-2">
            {(templates.data ?? []).map((t) => (
              <button key={t.name} className="w-full rounded-lg border border-line p-2.5 text-left hover:border-accent"
                onClick={() => setEditing({ id: null, draft: { ...t, enabled: true } })}>
                <div className="text-sm font-medium">{t.name}</div>
                <div className="text-xs text-muted">{t.description}</div>
                <div className="mt-1 text-[11px] text-accent">{t.trigger_text}</div>
              </button>
            ))}
          </div>
        </Section>
      </div>

      {editing && meta.data && (
        <WorkflowEditor editing={editing} meta={meta.data} tools={tools.data ?? []} agents={(agents.data ?? []).map((a) => ({ slug: a.slug, name: a.name }))}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); flows.reload(); notifyChanged(); }} />
      )}
    </div>
  );
}

const blank = (): WorkflowDraft & { enabled?: boolean } => ({
  name: "", description: "", trigger: { type: "daily", time: "09:00" }, conditions: [],
  steps: [{ type: "tool", tool: "daily_summary", args: {} }], source_text: null, enabled: true,
});

function StepChip({ step }: { step: WorkflowStep }) {
  const Icon = step.type === "tool" ? Wrench : step.type === "mission" ? Bot : Bell;
  const label = step.label || (step.type === "tool" ? step.tool?.replace(/_/g, " ") : step.type === "mission" ? step.goal : step.title);
  return (
    <span className="chip max-w-64" title={label ?? ""}>
      <Icon size={11} className={step.type === "mission" ? "text-accent" : ""} />
      <span className="truncate">{label}</span>
    </span>
  );
}

function RunHistory({ runs }: { runs: WorkflowRun[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!runs.length) return <Empty>No runs yet.</Empty>;
  return (
    <div className="divide-y divide-line">
      {runs.map((r) => (
        <div key={r.id} className="py-2">
          <button className="flex w-full items-center gap-2 text-left text-sm" onClick={() => setOpen(open === r.id ? null : r.id)}>
            {open === r.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span className={`chip ${RUN_TONE[r.status] ?? ""}`}>{r.status}</span>
            <span className="flex-1 truncate">{r.name}</span>
            <span className="text-xs text-muted">{r.trigger} · {ago(r.started_at)}</span>
          </button>
          {open === r.id && (
            <ul className="ml-6 mt-2 space-y-1 text-xs">
              {r.log.map((e, i) => (
                <li key={i} className="flex gap-2">
                  <span className={`w-16 shrink-0 ${e.status === "error" ? "text-danger" : e.status === "pending" ? "text-warn" : e.status === "skipped" || e.status === "retry" ? "text-muted" : "text-ok"}`}>{e.status}</span>
                  <span className="whitespace-pre-wrap text-text/85">{e.label ? <b>{e.label}: </b> : null}{e.summary}{e.mission_id ? ` (mission #${e.mission_id})` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- editor

function WorkflowEditor({ editing, meta, tools, agents, onClose, onSaved }: {
  editing: Editing; meta: WorkflowMeta; tools: ToolInfo[]; agents: { slug: string; name: string }[];
  onClose: () => void; onSaved: () => void;
}) {
  const toast = useToast();
  const [w, setW] = useState<WorkflowDraft & { enabled?: boolean }>(() => JSON.parse(JSON.stringify(editing.draft)));
  const [busy, setBusy] = useState(false);
  const toolMap = Object.fromEntries(tools.map((t) => [t.name, t]));

  const setStep = (i: number, s: WorkflowStep) => setW({ ...w, steps: w.steps.map((x, j) => (j === i ? s : x)) });
  const move = (i: number, d: number) => {
    const steps = [...w.steps];
    const [s] = steps.splice(i, 1);
    steps.splice(i + d, 0, s);
    setW({ ...w, steps });
  };
  const insert = (i: number, type: WorkflowStep["type"]) => {
    const s: WorkflowStep = type === "tool" ? { type, tool: "list_tasks", args: {} } : type === "mission" ? { type, goal: "" } : { type, title: "LifeOS", message: "" };
    const steps = [...w.steps];
    steps.splice(i, 0, s);
    setW({ ...w, steps });
  };

  const save = async () => {
    setBusy(true);
    try {
      const body = { name: w.name, description: w.description, trigger: w.trigger, conditions: w.conditions, steps: w.steps,
        enabled: w.enabled ?? true, source_text: w.source_text };
      if (editing.id) await api.updateWorkflow(editing.id, body);
      else await api.createWorkflow(body);
      toast(`Saved ${w.name}`);
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <div className="flex h-full w-full max-w-3xl flex-col border-l border-line bg-bg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line p-4">
          <WorkflowIcon size={18} className="text-accent" />
          <div className="flex-1 font-semibold">{editing.id ? "Edit workflow" : "New workflow"}</div>
          {w.planner && <span className="chip">drafted by {w.planner === "llm" ? "the model" : "built-in rules"}</span>}
          <button className="btn-primary" disabled={busy || !w.name.trim()} onClick={save}>{busy ? <Spinner /> : <Save size={14} />} Save</button>
          <button className="btn-ghost" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><label className="label">Name</label><input className="input" value={w.name} onChange={(e) => setW({ ...w, name: e.target.value })} /></div>
            <div><label className="label">Description</label><input className="input" value={w.description} onChange={(e) => setW({ ...w, description: e.target.value })} /></div>
          </div>

          <Node icon={<Clock size={15} />} title="1. Trigger">
            <TriggerEditor value={w.trigger} meta={meta} onChange={(trigger) => setW({ ...w, trigger })} />
          </Node>

          <Node icon={<Sparkles size={15} />} title={`2. Conditions (${w.conditions.length ? "all must hold" : "none: always run"})`}>
            {w.conditions.map((c, i) => (
              <div key={i} className="mb-2 flex items-center gap-2">
                <ConditionEditor value={c} meta={meta} onChange={(nc) => setW({ ...w, conditions: w.conditions.map((x, j) => (j === i ? nc : x)) })} />
                <button className="btn-ghost" onClick={() => setW({ ...w, conditions: w.conditions.filter((_, j) => j !== i) })}><X size={14} /></button>
              </div>
            ))}
            <button className="btn-ghost text-xs" onClick={() => setW({ ...w, conditions: [...w.conditions, { metric: "open_tasks", op: ">", value: 0 }] })}>
              <Plus size={13} /> Add condition
            </button>
          </Node>

          <div>
            <div className="mb-2 text-sm font-semibold">3. Steps</div>
            <AddStep onAdd={(t) => insert(0, t)} />
            {w.steps.map((s, i) => (
              <div key={i}>
                <div className="relative rounded-xl border border-line bg-panel p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="grid h-6 w-6 place-items-center rounded-full bg-accent-soft text-xs font-semibold">{i + 1}</span>
                    <select className="input w-36 py-1" value={s.type}
                      onChange={(e) => {
                        const type = e.target.value as WorkflowStep["type"];
                        setStep(i, type === "tool" ? { ...s, type, tool: s.tool ?? "list_tasks", args: s.args ?? {} } : type === "mission" ? { ...s, type, goal: s.goal ?? "" } : { ...s, type, title: s.title ?? "LifeOS", message: s.message ?? "" });
                      }}>
                      <option value="tool">Tool</option><option value="mission">Crew mission</option><option value="notify">Notification</option>
                    </select>
                    <input className="input flex-1 py-1" placeholder="Label (optional)" value={s.label ?? ""} onChange={(e) => setStep(i, { ...s, label: e.target.value })} />
                    <button className="btn-ghost" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={14} /></button>
                    <button className="btn-ghost" disabled={i === w.steps.length - 1} onClick={() => move(i, 1)}><ArrowDown size={14} /></button>
                    <button className="btn-ghost" onClick={() => setW({ ...w, steps: w.steps.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
                  </div>

                  {s.type === "tool" && (
                    <>
                      <select className="input mb-2" value={s.tool} onChange={(e) => setStep(i, { ...s, tool: e.target.value, args: {} })}>
                        {tools.map((t) => <option key={t.name} value={t.name}>{t.name} {t.policy !== "auto" ? `(${t.policy})` : ""} · {t.description}</option>)}
                      </select>
                      {s.tool && toolMap[s.tool] && <ArgsForm tool={toolMap[s.tool]} args={s.args ?? {}} onChange={(args) => setStep(i, { ...s, args })} />}
                    </>
                  )}
                  {s.type === "mission" && (
                    <div className="grid gap-2 sm:grid-cols-[1fr_200px]">
                      <textarea className="input min-h-16" placeholder="What should the crew do?" value={s.goal ?? ""} onChange={(e) => setStep(i, { ...s, goal: e.target.value })} />
                      <select className="input" value={s.agent ?? ""} onChange={(e) => setStep(i, { ...s, agent: e.target.value || null })}>
                        <option value="">Whole crew</option>
                        {agents.filter((a) => a.slug !== "overseer").map((a) => <option key={a.slug} value={a.slug}>Only {a.name}</option>)}
                      </select>
                    </div>
                  )}
                  {s.type === "notify" && (
                    <div className="grid gap-2">
                      <input className="input" placeholder="Title" value={s.title ?? ""} onChange={(e) => setStep(i, { ...s, title: e.target.value })} />
                      <textarea className="input min-h-16" placeholder="Message. Use {{steps.s1.summary}}, {{date}}, {{weekday}}" value={s.message ?? ""} onChange={(e) => setStep(i, { ...s, message: e.target.value })} />
                    </div>
                  )}

                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted">Retries, errors, condition · id <code>{s.id ?? `s${i + 1}`}</code></summary>
                    <div className="mt-2 grid gap-2 sm:grid-cols-3">
                      <div><label className="label">Retries</label><input type="number" min={0} max={5} className="input" value={s.retries ?? 0} onChange={(e) => setStep(i, { ...s, retries: Number(e.target.value) })} /></div>
                      <div><label className="label">Retry delay (s)</label><input type="number" min={1} max={600} className="input" value={s.retry_delay_s ?? 30} onChange={(e) => setStep(i, { ...s, retry_delay_s: Number(e.target.value) })} /></div>
                      <div><label className="label">If it fails</label>
                        <select className="input" value={s.on_error ?? "stop"} onChange={(e) => setStep(i, { ...s, on_error: e.target.value as "stop" | "continue" })}>
                          <option value="stop">Stop the workflow</option><option value="continue">Continue</option>
                        </select></div>
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      {s.condition ? (
                        <>
                          <span className="text-xs text-muted">Only if</span>
                          <ConditionEditor value={s.condition} meta={meta} onChange={(c) => setStep(i, { ...s, condition: c })} />
                          <button className="btn-ghost" onClick={() => setStep(i, { ...s, condition: undefined })}><X size={14} /></button>
                        </>
                      ) : (
                        <button className="btn-ghost text-xs" onClick={() => setStep(i, { ...s, condition: { metric: "open_tasks", op: ">", value: 0 } })}><Plus size={13} /> Run this step only if…</button>
                      )}
                    </div>
                  </details>
                </div>
                <AddStep onAdd={(t) => insert(i + 1, t)} />
              </div>
            ))}
          </div>

          <Node icon={<Bell size={15} />} title="4. Summary">
            <p className="text-xs text-muted">When the run finishes you get a notification listing what each step did, what is waiting for approval and what failed.</p>
          </Node>
        </div>
      </div>
    </div>
  );
}

function Node({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><span className="text-accent">{icon}</span>{title}</div>
      {children}
    </div>
  );
}

function AddStep({ onAdd }: { onAdd: (t: WorkflowStep["type"]) => void }) {
  return (
    <div className="flex items-center justify-center gap-1 py-1.5">
      <div className="h-px flex-1 bg-line" />
      <button className="chip hover:text-text" onClick={() => onAdd("tool")}><Plus size={11} /> tool</button>
      <button className="chip hover:text-text" onClick={() => onAdd("mission")}><Plus size={11} /> mission</button>
      <button className="chip hover:text-text" onClick={() => onAdd("notify")}><Plus size={11} /> notify</button>
      <div className="h-px flex-1 bg-line" />
    </div>
  );
}

function TriggerEditor({ value, meta, onChange }: { value: Trigger; meta: WorkflowMeta; onChange: (t: Trigger) => void }) {
  const t = value as Trigger & { minutes?: number; time?: string; days?: string[]; day?: number };
  const switchTo = (type: string) => {
    if (type === "interval") onChange({ type, minutes: t.minutes ?? 60 });
    else if (type === "daily") onChange({ type, time: t.time ?? "09:00" });
    else if (type === "weekly") onChange({ type, days: t.days ?? ["mon"], time: t.time ?? "09:00" });
    else if (type === "monthly") onChange({ type, day: t.day ?? 1, time: t.time ?? "09:00" });
    else onChange({ type: type as "manual" | "startup" });
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select className="input w-44" value={t.type} onChange={(e) => switchTo(e.target.value)}>
        {meta.triggers.map((x) => <option key={x} value={x}>{({ manual: "Manually", interval: "Every N minutes", daily: "Every day", weekly: "Weekly", monthly: "Monthly", startup: "When LifeOS starts" } as Record<string, string>)[x] ?? x}</option>)}
      </select>
      {t.type === "interval" && (
        <><input type="number" min={5} className="input w-24" value={t.minutes} onChange={(e) => onChange({ type: "interval", minutes: Number(e.target.value) })} /><span className="text-xs text-muted">minutes</span></>
      )}
      {t.type === "weekly" && (
        <div className="flex gap-1">
          {meta.days.map((d) => (
            <button key={d} className={`rounded-lg border px-2 py-1 text-xs ${t.days?.includes(d) ? "border-accent bg-accent-soft" : "border-line text-muted"}`}
              onClick={() => {
                const days = t.days?.includes(d) ? t.days.filter((x) => x !== d) : [...(t.days ?? []), d];
                onChange({ type: "weekly", days: days.length ? days : [d], time: t.time ?? "09:00" });
              }}>{d}</button>
          ))}
        </div>
      )}
      {t.type === "monthly" && (
        <><span className="text-xs text-muted">day</span><input type="number" min={1} max={31} className="input w-20" value={t.day} onChange={(e) => onChange({ type: "monthly", day: Number(e.target.value), time: t.time ?? "09:00" })} /></>
      )}
      {(t.type === "daily" || t.type === "weekly" || t.type === "monthly") && (
        <><span className="text-xs text-muted">at</span><input type="time" className="input w-32" value={t.time} onChange={(e) => onChange({ ...(t as Trigger & { time: string }), time: e.target.value } as Trigger)} /></>
      )}
    </div>
  );
}

function ConditionEditor({ value, meta, onChange }: { value: Condition; meta: WorkflowMeta; onChange: (c: Condition) => void }) {
  return (
    <div className="flex flex-1 flex-wrap items-center gap-2">
      <select className="input w-56 py-1" value={value.metric} onChange={(e) => onChange({ ...value, metric: e.target.value })}>
        {Object.entries(meta.metrics).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </select>
      {value.metric === "loose_files" && <input className="input w-32 py-1" placeholder="Folder" value={value.folder ?? ""} onChange={(e) => onChange({ ...value, folder: e.target.value })} />}
      {value.metric === "due_within_hours" && <input type="number" className="input w-20 py-1" title="hours" value={value.hours ?? 24} onChange={(e) => onChange({ ...value, hours: Number(e.target.value) })} />}
      <select className="input w-16 py-1" value={value.op} onChange={(e) => onChange({ ...value, op: e.target.value })}>
        {meta.ops.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <input type="number" className="input w-24 py-1" value={value.value} onChange={(e) => onChange({ ...value, value: Number(e.target.value) })} />
    </div>
  );
}

const kindOf = (p: JsonProp): string => p.type ?? p.anyOf?.find((x) => x.type && x.type !== "null")?.type ?? "string";
const itemsOf = (p: JsonProp): JsonProp | undefined => p.items ?? p.anyOf?.find((x) => x.items)?.items;

/** Inputs generated from a tool's JSON schema. Values may contain {{templates}}. */
function ArgsForm({ tool, args, onChange }: { tool: ToolInfo; args: Record<string, unknown>; onChange: (a: Record<string, unknown>) => void }) {
  const props = tool.parameters?.properties ?? {};
  const required = new Set(tool.parameters?.required ?? []);
  const set = (k: string, v: unknown) => {
    const next = { ...args };
    if (v === "" || v === undefined || (Array.isArray(v) && !v.length)) delete next[k];
    else next[k] = v;
    onChange(next);
  };
  if (!Object.keys(props).length) return <p className="text-xs text-muted">No settings.</p>;
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {Object.entries(props).map(([k, p]) => {
        const kind = kindOf(p);
        const label = `${k.replace(/_/g, " ")}${required.has(k) ? " *" : ""}`;
        const enumValues = p.enum ?? p.anyOf?.find((x) => x.enum)?.enum;
        let input;
        if (enumValues) {
          input = (
            <select className="input py-1" value={String(args[k] ?? p.default ?? "")} onChange={(e) => set(k, e.target.value)}>
              {!required.has(k) && <option value="">(default)</option>}
              {enumValues.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          );
        } else if (kind === "boolean") {
          input = <input type="checkbox" className="accent-[var(--color-accent)]" checked={Boolean(args[k] ?? p.default)} onChange={(e) => set(k, e.target.checked)} />;
        } else if (kind === "integer" || kind === "number") {
          input = <input type="number" className="input py-1" placeholder={p.default !== undefined && p.default !== null ? String(p.default) : ""}
            value={args[k] === undefined ? "" : String(args[k])} onChange={(e) => set(k, e.target.value === "" ? "" : Number(e.target.value))} />;
        } else if (kind === "array" && kindOf(itemsOf(p) ?? {}) === "string") {
          input = <input className="input py-1" placeholder="comma, separated" value={Array.isArray(args[k]) ? (args[k] as string[]).join(", ") : ""}
            onChange={(e) => set(k, e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />;
        } else {
          input = <input className="input py-1" placeholder={p.description ?? ""} value={String(args[k] ?? "")} onChange={(e) => set(k, e.target.value)} />;
        }
        return <div key={k}><label className="label" title={p.description}>{label}</label>{input}</div>;
      })}
    </div>
  );
}
