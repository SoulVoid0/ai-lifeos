import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Coins, Download, ExternalLink, FileText, Inbox, ListTree, Plus, RotateCcw, Rocket, Send, ShieldAlert, Trash2, Upload,
  Users, CircleStop, X,
} from "lucide-react";
import { api, type Agent, type AgentStatus, type Mission, type OutboxFile } from "../lib/api";
import { ago, bytes } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "../components/ApprovalCard";
import { AgentEditor } from "../components/AgentEditor";
import { AgentAvatar, BriefCard, EventIcon, MicButton, STATE_LABEL, STATE_TONE, colorOf, useMissionEvents } from "../components/crew";
import { Markdown } from "../components/Markdown";
import { MISSION_TONE, MissionPanel } from "../components/MissionPanel";
import { Empty, ErrorBox, PageHeader, Section, Spinner, notifyChanged, useToast } from "../components/ui";

const EXAMPLES = [
  "Find all PDFs in my Downloads folder, organize them by category, and remind me to review the important ones tomorrow",
  "Look at my open tasks and deadlines and build me a realistic plan for this week",
  "Find what I was last working on in my projects and tell me where I left off",
  "Research the best way to structure a Python project and save a short guide to the outbox",
];

type Tab = "missions" | "outbox" | "crew" | "ledger";

export default function ControlCenter() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const status = useAsync(api.crewStatus, [], 1500);
  const missions = useAsync(() => api.missions(), [], 3000);
  const tools = useAsync(api.tools, []);
  const events = useMissionEvents(undefined, 200, 1000);
  const [goal, setGoal] = useState(params.get("goal") ?? "");
  const [target, setTarget] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("missions");
  const [openMission, setOpenMission] = useState<number | null>(params.get("mission") ? Number(params.get("mission")) : null);
  const [editing, setEditing] = useState<Agent | null | "new">(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    const h = () => { status.reload(); missions.reload(); };
    window.addEventListener("lifeos:changed", h);
    return () => window.removeEventListener("lifeos:changed", h);
  }, [status.reload, missions.reload]);

  const agents = status.data?.agents ?? [];
  const byslug = useMemo(() => Object.fromEntries(agents.map((a) => [a.slug, a])), [agents]);

  const launch = async () => {
    if (!goal.trim()) return;
    setLaunching(true);
    try {
      const m = await api.startMission(goal.trim(), target || null);
      setGoal("");
      toast(`Mission #${m.id} launched`);
      missions.reload();
      status.reload();
      if (params.get("goal")) setParams({});
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLaunching(false);
    }
  };

  const stopAll = async () => {
    const r = await api.stopAll();
    toast(r.stopped ? `Stopped ${r.stopped} mission(s)` : "Nothing was running", "info");
    notifyChanged();
  };

  if (!status.data) return <div className="p-8">{status.error ? <ErrorBox error={status.error} /> : <Spinner />}</div>;

  const spend = status.data.spend;
  const needsYou = status.data.briefs.length + status.data.pending_actions.length;
  const shownEvents = selected ? events.filter((e) => e.agent === selected) : events;
  const active = status.data.active;

  return (
    <div className="mx-auto max-w-7xl p-6">
      <PageHeader
        title="Control Center"
        subtitle="Your AI crew: an Overseer that plans and delegates, and specialists that do the work. Watch them, answer them, steer them."
        actions={<>
          <span className="chip" title="Model usage today (all agents)">
            <Coins size={12} /> {(spend.tokens_in + spend.tokens_out).toLocaleString()} tokens
            {spend.cloud_cost_usd > 0 || spend.cost_usd > 0 ? ` · $${spend.cost_usd.toFixed(3)}` : ""} today
          </span>
          <button className="btn-danger" onClick={stopAll} disabled={!active.length}><CircleStop size={14} /> Stop all</button>
        </>}
      />

      {/* mission composer */}
      <div className="card mb-4 p-3">
        <form className="flex flex-col gap-2 md:flex-row md:items-end" onSubmit={(e) => { e.preventDefault(); launch(); }}>
          <div className="flex-1">
            <textarea
              className="input min-h-[52px] resize-y"
              placeholder="Give the crew a mission… e.g. “Organize my Downloads and plan my week around my deadlines”"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) launch(); }}
            />
          </div>
          <div className="flex items-center gap-2">
            <select className="input w-56" value={target} onChange={(e) => setTarget(e.target.value)} title="Who takes this mission">
              <option value="">Whole crew ({byslug.overseer?.name ?? "Overseer"} plans)</option>
              {agents.filter((a) => a.slug !== "overseer" && a.enabled).map((a) => (
                <option key={a.slug} value={a.slug}>Only {a.name} ({a.role})</option>
              ))}
            </select>
            <MicButton onText={(t) => setGoal((g) => (g ? `${g} ${t}` : t))} />
            <button className="btn-primary" disabled={launching || !goal.trim()}>
              {launching ? <Spinner /> : <Rocket size={14} />} Launch
            </button>
          </div>
        </form>
        {!goal && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLES.map((x) => <button key={x} className="chip hover:text-text" onClick={() => setGoal(x)}>{x.length > 70 ? x.slice(0, 68) + "…" : x}</button>)}
          </div>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        {/* station */}
        <Section
          title="Station"
          icon={<Users size={15} className="text-accent" />}
          actions={<span className="text-xs text-muted">{agents.filter((a) => a.state === "working").length} working · {active.length} active mission(s)</span>}
        >
          <Station agents={agents} selected={selected} onSelect={(s) => setSelected(selected === s ? null : s)}
            onOpenMission={setOpenMission} />
          {selected && byslug[selected] && (
            <AgentDetail agent={byslug[selected]} onEdit={() => setEditing(byslug[selected])} onClose={() => setSelected(null)}
              onOpenMission={setOpenMission} />
          )}
        </Section>

        {/* right column */}
        <div className="space-y-4">
          <Section title={`Needs you${needsYou ? ` (${needsYou})` : ""}`} icon={<ShieldAlert size={15} className="text-warn" />}>
            {needsYou === 0 && <Empty>Nothing is waiting for you.</Empty>}
            <div className="space-y-2">
              {status.data.briefs.map((b) => (
                <BriefCard key={b.id} brief={b} agentName={byslug[b.agent]?.name ?? b.agent} onAnswered={status.reload} />
              ))}
              {status.data.pending_actions.map((a) => (
                <div key={a.id}>
                  {a.mission_id && (
                    <button className="mb-1 text-[11px] text-muted hover:text-text" onClick={() => setOpenMission(a.mission_id!)}>
                      from mission #{a.mission_id}
                    </button>
                  )}
                  <ApprovalCard action={a} onChange={() => status.reload()} />
                </div>
              ))}
            </div>
          </Section>

          <Section
            title={selected ? `Live feed: ${byslug[selected]?.name}` : "Live feed"}
            icon={<ListTree size={15} className="text-accent" />}
            actions={selected ? <button className="text-xs text-muted hover:text-text" onClick={() => setSelected(null)}>Show all</button> : undefined}
          >
            <Feed events={shownEvents} agents={byslug} onOpenMission={setOpenMission} />
          </Section>
        </div>
      </div>

      {/* tabs */}
      <div className="mt-6 flex gap-1 border-b border-line">
        {([["missions", "Missions", Rocket], ["outbox", "Outbox", Inbox], ["crew", "Crew", Users], ["ledger", "Ledger", Coins]] as const).map(([k, label, Icon]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${tab === k ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>
      <div className="pt-4">
        {tab === "missions" && <MissionList missions={missions.data ?? []} agents={byslug} onOpen={setOpenMission} />}
        {tab === "outbox" && <OutboxTab />}
        {tab === "crew" && <CrewTab agents={agents} onEdit={(a) => setEditing(a)} onNew={() => setEditing("new")} reload={status.reload} />}
        {tab === "ledger" && <LedgerTab agents={byslug} />}
      </div>

      {openMission !== null && (
        <MissionPanel id={openMission} agents={agents} onClose={() => { setOpenMission(null); if (params.get("mission")) setParams({}); }} />
      )}
      {editing !== null && (
        <AgentEditor agent={editing === "new" ? null : editing} tools={tools.data ?? []}
          onClose={() => { setEditing(null); status.reload(); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- station view

function Station({ agents, selected, onSelect, onOpenMission }: {
  agents: AgentStatus[]; selected: string | null; onSelect: (slug: string) => void; onOpenMission: (id: number) => void;
}) {
  const overseer = agents.find((a) => a.slug === "overseer");
  const crew = agents.filter((a) => a.slug !== "overseer");
  const n = crew.length;
  // Specialists sit on an ellipse around the Overseer; links light up while work flows along them.
  const pos = crew.map((_, i) => {
    const angle = (-90 + (360 / Math.max(n, 1)) * i) * (Math.PI / 180);
    return { x: 50 + 37 * Math.cos(angle), y: 50 + 37 * Math.sin(angle) };
  });
  return (
    <>
      <div className="relative hidden aspect-[4/3] max-h-[680px] w-full overflow-hidden rounded-xl border border-line md:block"
        style={{ background: "radial-gradient(circle at 50% 50%, color-mix(in oklab, var(--color-accent) 10%, transparent), transparent 60%), repeating-linear-gradient(0deg, transparent 0 23px, color-mix(in oklab, var(--color-line) 45%, transparent) 23px 24px), repeating-linear-gradient(90deg, transparent 0 23px, color-mix(in oklab, var(--color-line) 45%, transparent) 23px 24px)" }}>
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
          {crew.map((a, i) => {
            const live = a.state === "working" || a.state === "waiting" || a.state === "asking";
            return (
              <line key={a.slug} x1={50} y1={50} x2={pos[i].x} y2={pos[i].y}
                stroke={live ? colorOf(a.color) : "var(--color-line)"} strokeWidth={live ? 0.6 : 0.3}
                strokeDasharray={live ? "2 1" : undefined} className={live ? "station-flow" : undefined}
                vectorEffect="non-scaling-stroke" style={{ strokeWidth: live ? 2 : 1 }} />
            );
          })}
        </svg>
        {overseer && (
          <div className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: "50%", top: "50%" }}>
            <AgentNode agent={overseer} big selected={selected === overseer.slug} onClick={() => onSelect(overseer.slug)} onOpenMission={onOpenMission} />
          </div>
        )}
        {crew.map((a, i) => (
          <div key={a.slug} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${pos[i].x}%`, top: `${pos[i].y}%` }}>
            <AgentNode agent={a} selected={selected === a.slug} onClick={() => onSelect(a.slug)} onOpenMission={onOpenMission} />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2 md:hidden">
        {agents.map((a) => (
          <AgentNode key={a.slug} agent={a} selected={selected === a.slug} onClick={() => onSelect(a.slug)} onOpenMission={onOpenMission} />
        ))}
      </div>
    </>
  );
}

function AgentNode({ agent, big, selected, onClick, onOpenMission }: {
  agent: AgentStatus; big?: boolean; selected: boolean; onClick: () => void; onOpenMission: (id: number) => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-44 flex-col items-center rounded-xl border bg-panel/95 p-2.5 text-center shadow-lg backdrop-blur transition hover:border-accent ${
        selected ? "border-accent ring-2 ring-accent/30" : "border-line"} ${big ? "w-52" : ""}`}
    >
      <AgentAvatar icon={agent.icon} color={agent.color} state={agent.state} size={big ? 52 : 40} />
      <div className="mt-1.5 text-sm font-semibold">{agent.name}</div>
      <div className="text-[11px] text-muted">{agent.role}</div>
      <div className={`mt-1 text-[11px] font-medium ${STATE_TONE[agent.state]}`}>{STATE_LABEL[agent.state]}</div>
      {agent.current && (
        <span role="link" tabIndex={0}
          onClick={(e) => { e.stopPropagation(); onOpenMission(agent.current!.mission_id); }}
          className="mt-0.5 line-clamp-2 text-[11px] text-text/80 hover:underline">
          {agent.current.title}
        </span>
      )}
      {!agent.current && agent.tokens_today > 0 && (
        <div className="mt-0.5 text-[10px] text-muted">{agent.tokens_today.toLocaleString()} tokens today</div>
      )}
    </button>
  );
}

function AgentDetail({ agent, onEdit, onClose, onOpenMission }: {
  agent: AgentStatus; onEdit: () => void; onClose: () => void; onOpenMission: (id: number) => void;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-start gap-3 rounded-xl border border-line bg-panel-2 p-3">
      <AgentAvatar icon={agent.icon} color={agent.color} state={agent.state} size={44} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{agent.name}</span>
          <span className="text-xs text-muted">{agent.role}</span>
          <span className="chip font-mono">{agent.effective_model}</span>
          <span className={`text-xs ${STATE_TONE[agent.state]}`}>{STATE_LABEL[agent.state]}</span>
        </div>
        <p className="mt-1 text-xs text-muted">{agent.description}</p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {agent.tools.map((t) => <span key={t} className="chip font-mono text-[10px]">{t}</span>)}
        </div>
        <div className="mt-1.5 text-[11px] text-muted">
          Today: {agent.calls_today} model call(s), {agent.tokens_today.toLocaleString()} tokens{agent.cost_today ? `, $${agent.cost_today.toFixed(4)}` : ""}
          {agent.current && <> · on <button className="underline" onClick={() => onOpenMission(agent.current!.mission_id)}>mission #{agent.current.mission_id}</button></>}
        </div>
      </div>
      <div className="flex gap-1">
        <button className="btn-outline text-xs" onClick={onEdit}>Configure</button>
        <button className="btn-ghost" onClick={onClose}><X size={14} /></button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- feed and lists

function Feed({ events, agents, onOpenMission }: {
  events: ReturnType<typeof useMissionEvents>; agents: Record<string, AgentStatus>; onOpenMission: (id: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events]);
  if (!events.length) return <Empty>No activity yet. Launch a mission to wake the crew.</Empty>;
  return (
    <div ref={ref} className="max-h-[440px] space-y-1.5 overflow-y-auto pr-1"
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
      {events.map((e) => {
        const a = e.agent ? agents[e.agent] : undefined;
        return (
          <div key={e.id} className="flex items-start gap-2 text-xs">
            {a ? <AgentAvatar icon={a.icon} color={a.color} size={20} /> : <span className="grid h-5 w-5 place-items-center"><EventIcon kind={e.kind} /></span>}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <EventIcon kind={e.kind} size={11} />
                {a && <span className="font-semibold" style={{ color: colorOf(a.color) }}>{a.name}</span>}
                <button className="text-muted hover:text-text" onClick={() => onOpenMission(e.mission_id)}>#{e.mission_id}</button>
                <span className="ml-auto text-[10px] text-muted">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
              </div>
              <div className={`break-words ${e.kind === "thought" ? "italic text-muted" : "text-text/90"}`}>{e.summary}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MissionList({ missions, agents, onOpen }: { missions: Mission[]; agents: Record<string, AgentStatus>; onOpen: (id: number) => void }) {
  if (!missions.length) return <Empty>No missions yet.</Empty>;
  return (
    <div className="card divide-y divide-line">
      {missions.map((m) => {
        const counts = m.task_counts ?? {};
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        return (
          <button key={m.id} onClick={() => onOpen(m.id)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-panel-2">
            <span className={`chip w-20 justify-center ${MISSION_TONE[m.status]}`}>{m.status}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm">{m.goal}</div>
              <div className="text-xs text-muted">
                #{m.id} · {m.source}{m.target_agent ? ` · ${agents[m.target_agent]?.name ?? m.target_agent}` : ""} · {ago(m.created_at)}
                {total > 0 && ` · ${counts.done ?? 0}/${total} tasks`}
                {m.tokens_in + m.tokens_out > 0 && ` · ${(m.tokens_in + m.tokens_out).toLocaleString()} tokens`}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function OutboxTab() {
  const toast = useToast();
  const files = useAsync(api.outbox, [], 5000);
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  return (
    <>
      {!(files.data ?? []).length && <Empty>Finished work lands here as real files.</Empty>}
      <div className="card divide-y divide-line">
        {(files.data ?? []).map((f: OutboxFile) => (
          <div key={f.name} className="flex items-center gap-3 px-4 py-2.5">
            <FileText size={16} className="shrink-0 text-accent" />
            <button className="min-w-0 flex-1 text-left" onClick={async () => setPreview(await api.outboxFile(f.name))}>
              <div className="truncate text-sm hover:underline">{f.name}</div>
              <div className="text-xs text-muted">{bytes(f.size)} · {ago(f.modified_at)}</div>
            </button>
            <button className="btn-ghost text-xs" onClick={() => api.openOutbox(f.name).catch((e) => toast(e.message, "error"))}><ExternalLink size={13} /> Open</button>
            <button className="btn-ghost text-xs" onClick={async () => { await api.deleteOutbox(f.name); files.reload(); }}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>
      {preview && (
        <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4" onClick={() => setPreview(null)}>
          <div className="card max-h-[85vh] w-full max-w-3xl overflow-y-auto p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <div className="font-semibold">{preview.name}</div>
              <button className="btn-ghost" onClick={() => setPreview(null)}><X size={16} /></button>
            </div>
            {preview.name.endsWith(".md") ? <Markdown text={preview.text} /> : <pre className="whitespace-pre-wrap font-mono text-xs">{preview.text}</pre>}
          </div>
        </div>
      )}
    </>
  );
}

function CrewTab({ agents, onEdit, onNew, reload }: { agents: AgentStatus[]; onEdit: (a: Agent) => void; onNew: () => void; reload: () => void }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const exportPack = async () => {
    const pack = await api.exportPack();
    const url = URL.createObjectURL(new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "lifeos-pack.json";
    a.click();
    URL.revokeObjectURL(url);
  };
  const importPack = async (file: File) => {
    try {
      const r = await api.importPack(JSON.parse(await file.text()));
      toast(`Imported ${r.agents.length} agent(s) and ${r.workflows.length} workflow(s)` + (r.skipped.length ? `; skipped ${r.skipped.length}` : ""));
      notifyChanged();
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        <button className="btn-primary" onClick={onNew}><Plus size={14} /> Recruit agent</button>
        <button className="btn-outline" onClick={exportPack}><Download size={14} /> Export pack</button>
        <button className="btn-outline" onClick={() => fileRef.current?.click()}><Upload size={14} /> Import pack</button>
        <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) importPack(f); e.target.value = ""; }} />
        <button className="btn-ghost ml-auto" onClick={async () => { await api.resetCrew(); toast("Built-in crew reset"); reload(); }}>
          <RotateCcw size={14} /> Reset built-in agents
        </button>
      </div>
      <p className="mb-3 text-xs text-muted">Packs are plain JSON with agents and workflows. They never contain code or keys; imported workflows start disabled.</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {agents.map((a) => (
          <button key={a.slug} onClick={() => onEdit(a)} className="card flex items-start gap-3 p-3 text-left hover:border-accent">
            <AgentAvatar icon={a.icon} color={a.color} state={a.enabled ? "idle" : "offline"} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{a.name}</span>
                <span className="text-xs text-muted">{a.role}</span>
                {!a.enabled && <span className="chip">disabled</span>}
              </div>
              <div className="mt-0.5 line-clamp-2 text-xs text-muted">{a.description}</div>
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px]">
                <span className="chip font-mono">{a.effective_model}</span>
                <span className="text-muted">{a.tools.length} tools</span>
              </div>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

function LedgerTab({ agents }: { agents: Record<string, AgentStatus> }) {
  const [days, setDays] = useState(7);
  const ledger = useAsync(() => api.ledger(days), [days], 10000);
  const d = ledger.data;
  if (!d) return <Spinner />;
  const maxTokens = Math.max(1, ...d.by_day.map((x) => x.tokens));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Today" icon={<Coins size={15} className="text-accent" />}
        actions={<select className="input w-28 py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[1, 7, 30, 90].map((n) => <option key={n} value={n}>{n} day{n > 1 ? "s" : ""}</option>)}
        </select>}>
        <div className="grid grid-cols-3 gap-3 text-center">
          <div><div className="text-xl font-semibold">{(d.today.tokens_in + d.today.tokens_out).toLocaleString()}</div><div className="text-xs text-muted">tokens</div></div>
          <div><div className="text-xl font-semibold">${d.today.cost_usd.toFixed(3)}</div><div className="text-xs text-muted">total cost</div></div>
          <div><div className="text-xl font-semibold">${d.today.cloud_cost_usd.toFixed(2)} / ${d.today.cloud_budget_usd.toFixed(2)}</div><div className="text-xs text-muted">cloud budget</div></div>
        </div>
        <div className="mt-4 flex h-28 items-end gap-1">
          {d.by_day.map((x) => (
            <div key={x.day} className="flex flex-1 flex-col items-center gap-1" title={`${x.day}: ${x.tokens.toLocaleString()} tokens, $${x.cost_usd.toFixed(4)}`}>
              <div className="w-full rounded-t bg-accent/70" style={{ height: `${(x.tokens / maxTokens) * 100}%`, minHeight: 2 }} />
              <div className="text-[9px] text-muted">{x.day.slice(5)}</div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-muted">Local models cost nothing. Set per-model prices in Settings → Cloud AI to track cloud spend.</p>
      </Section>
      <Section title="By agent" icon={<Users size={15} className="text-accent" />}>
        <table className="w-full text-sm">
          <tbody className="divide-y divide-line">
            {d.by_agent.map((r) => (
              <tr key={r.agent}>
                <td className="py-1.5">{agents[r.agent]?.name ?? r.agent}</td>
                <td className="py-1.5 text-right text-muted">{r.calls} calls</td>
                <td className="py-1.5 text-right">{(r.tokens_in + r.tokens_out).toLocaleString()}</td>
                <td className="py-1.5 text-right text-muted">${r.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!d.by_agent.length && <Empty>No model calls yet.</Empty>}
      </Section>
      <Section title="By model" icon={<Send size={15} className="text-accent" />} className="lg:col-span-2">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted"><tr><th className="py-1">Model</th><th>Provider</th><th className="text-right">Calls</th><th className="text-right">In</th><th className="text-right">Out</th><th className="text-right">Cost</th></tr></thead>
          <tbody className="divide-y divide-line">
            {d.by_model.map((r) => (
              <tr key={r.model}>
                <td className="py-1.5 font-mono text-xs">{r.model}</td><td className="text-muted">{r.provider}</td>
                <td className="text-right">{r.calls}</td><td className="text-right">{r.tokens_in.toLocaleString()}</td>
                <td className="text-right">{r.tokens_out.toLocaleString()}</td><td className="text-right">${r.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
