import { useEffect, useMemo } from "react";
import { CircleStop, ExternalLink, RotateCcw, X } from "lucide-react";
import { api, type Agent, type Mission, type MissionTask } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "./ApprovalCard";
import { AgentAvatar, BriefCard, EventIcon, colorOf, useMissionEvents } from "./crew";
import { Markdown } from "./Markdown";
import { Spinner, notifyChanged, useToast } from "./ui";

export const MISSION_TONE: Record<Mission["status"], string> = {
  queued: "text-muted", planning: "border-accent/40 text-accent", running: "border-ok/40 text-ok",
  waiting: "border-warn/40 text-warn", done: "border-ok/40 text-ok", failed: "border-danger/40 text-danger",
  cancelled: "text-muted",
};

const TASK_TONE: Record<MissionTask["status"], string> = {
  pending: "border-line", running: "border-ok", waiting_approval: "border-warn", waiting_input: "border-warn",
  done: "border-ok/50", failed: "border-danger", skipped: "border-line opacity-60", cancelled: "border-line opacity-60",
};

/** Lay tasks out in columns by dependency depth so the plan reads left to right. */
function layers(tasks: MissionTask[]): MissionTask[][] {
  const byKey = Object.fromEntries(tasks.map((t) => [t.key, t]));
  const depth: Record<string, number> = {};
  const d = (t: MissionTask, seen = new Set<string>()): number => {
    if (depth[t.key] !== undefined) return depth[t.key];
    if (seen.has(t.key)) return 0;
    seen.add(t.key);
    const deps = t.depends_on.map((k) => byKey[k]).filter(Boolean);
    return (depth[t.key] = deps.length ? 1 + Math.max(...deps.map((x) => d(x, seen))) : 0);
  };
  const cols: MissionTask[][] = [];
  for (const t of tasks) (cols[d(t)] ??= []).push(t);
  return cols.filter(Boolean);
}

export function MissionPanel({ id, agents, onClose }: { id: number; agents: Agent[]; onClose: () => void }) {
  const toast = useToast();
  const m = useAsync(() => api.mission(id), [id], 1500);
  const events = useMissionEvents(id, 500, 1200);
  const byslug = useMemo(() => Object.fromEntries(agents.map((a) => [a.slug, a])), [agents]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const mission = m.data;
  const live = mission && !["done", "failed", "cancelled"].includes(mission.status);
  const outboxName = mission?.outbox_path?.split(/[\\/]/).pop();

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <div className="flex h-full w-full max-w-3xl flex-col border-l border-line bg-bg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 border-b border-line p-4">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-muted">Mission #{id}{mission ? ` · ${mission.source} · ${ago(mission.created_at)}` : ""}</div>
            <div className="mt-0.5 text-base font-semibold">{mission?.goal ?? "…"}</div>
            {mission && (
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className={`chip ${MISSION_TONE[mission.status]}`}>{mission.status}</span>
                <span>{(mission.tokens_in + mission.tokens_out).toLocaleString()} tokens</span>
                {mission.cost_usd > 0 && <span>${mission.cost_usd.toFixed(4)}</span>}
                {mission.plan?.planner && <span>planner: {mission.plan.planner}</span>}
              </div>
            )}
          </div>
          {live && (
            <button className="btn-danger" onClick={async () => { await api.cancelMission(id); m.reload(); notifyChanged(); }}>
              <CircleStop size={14} /> Stop
            </button>
          )}
          {mission && !live && (
            <button className="btn-outline" onClick={async () => {
              try { await api.retryMission(id); toast("Mission restarted"); notifyChanged(); onClose(); }
              catch (e) { toast((e as Error).message, "error"); }
            }}><RotateCcw size={14} /> Run again</button>
          )}
          <button className="btn-ghost" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        {!mission ? <div className="p-6"><Spinner /></div> : (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
            {(mission.briefs ?? []).filter((b) => b.status === "open").map((b) => (
              <BriefCard key={b.id} brief={b} agentName={byslug[b.agent]?.name ?? b.agent} onAnswered={m.reload} />
            ))}
            {(mission.pending_actions ?? []).map((a) => <ApprovalCard key={a.id} action={a} onChange={() => m.reload()} />)}

            {(mission.tasks ?? []).length > 0 && (
              <div>
                <div className="label">Plan</div>
                <div className="flex gap-3 overflow-x-auto pb-2">
                  {layers(mission.tasks ?? []).map((col, ci) => (
                    <div key={ci} className="flex min-w-56 flex-1 flex-col gap-2">
                      <div className="text-[11px] uppercase tracking-wide text-muted">{ci === 0 ? "Start" : `Then (step ${ci + 1})`}</div>
                      {col.map((t) => {
                        const a = byslug[t.agent];
                        return (
                          <div key={t.id} className={`rounded-xl border-2 bg-panel p-2.5 ${TASK_TONE[t.status]}`}>
                            <div className="flex items-center gap-2">
                              <AgentAvatar icon={a?.icon ?? "bot"} color={a?.color ?? "slate"} size={26}
                                state={t.status === "running" ? "working" : t.status.startsWith("waiting") ? "waiting" : "idle"} />
                              <div className="min-w-0">
                                <div className="truncate text-xs font-semibold">{a?.name ?? t.agent}</div>
                                <div className="text-[11px] text-muted">{t.status.replace("_", " ")}{t.steps ? ` · ${t.steps} step(s)` : ""}</div>
                              </div>
                              <span className="ml-auto font-mono text-[10px] text-muted">{t.key}</span>
                            </div>
                            <div className="mt-1.5 text-xs">{t.title}</div>
                            {t.depends_on.length > 0 && <div className="mt-1 text-[11px] text-muted">after {t.depends_on.join(", ")}</div>}
                            {t.result && (
                              <details className="mt-1.5">
                                <summary className="cursor-pointer text-[11px] text-accent">Result</summary>
                                <div className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap text-[11px] text-text/85">{t.result}</div>
                              </details>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {mission.result && (
              <div className="card p-4">
                <div className="mb-2 flex items-center justify-between">
                  <div className="label mb-0">Report</div>
                  {outboxName && (
                    <button className="btn-ghost text-xs" onClick={() => api.openOutbox(outboxName).catch((e) => toast(e.message, "error"))}>
                      <ExternalLink size={13} /> Open {outboxName}
                    </button>
                  )}
                </div>
                <Markdown text={mission.result} />
              </div>
            )}
            {mission.error && mission.status === "failed" && <div className="text-sm text-danger">{mission.error}</div>}

            <div>
              <div className="label">Timeline</div>
              <ol className="space-y-1.5">
                {events.map((e) => {
                  const a = e.agent ? byslug[e.agent] : undefined;
                  return (
                    <li key={e.id} className="flex items-start gap-2 text-xs">
                      <EventIcon kind={e.kind} />
                      <span className="w-20 shrink-0 whitespace-nowrap text-muted">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                      {a && <span className="shrink-0 font-semibold" style={{ color: colorOf(a.color) }}>{a.name}</span>}
                      <span className={`min-w-0 whitespace-pre-wrap break-words ${e.kind === "thought" ? "italic text-muted" : ""}`}>{e.summary}</span>
                    </li>
                  );
                })}
              </ol>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
