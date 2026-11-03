import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, AlertCircle, CalendarClock, CalendarDays, Cpu, FileText, Lightbulb, Network, ShieldCheck, Sparkles, Workflow, X,
} from "lucide-react";
import { AgentAvatar, STATE_LABEL, STATE_TONE } from "../components/crew";
import { api } from "../lib/api";
import { ago, isOverdue, PRIORITY_LABEL, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "../components/ApprovalCard";
import { FileRow } from "../components/FileRow";
import { Empty, ErrorBox, Section, Spinner, notifyChanged, useToast } from "../components/ui";
import { WorkspacesCard } from "../components/SpecExtras";

export default function Dashboard() {
  const navigate = useNavigate();
  const toast = useToast();
  const { data, error, reload } = useAsync(api.dashboard, [], 60000);

  useEffect(() => {
    window.addEventListener("lifeos:changed", reload);
    return () => window.removeEventListener("lifeos:changed", reload);
  }, [reload]);

  if (!data) return <div className="p-8">{error ? <ErrorBox error={error} /> : <Spinner />}</div>;

  const run = (command: string) => navigate(`/assistant?prompt=${encodeURIComponent(command)}`);

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{data.greeting}</h1>
        <p className="mt-1 text-sm text-muted">
          {data.counts.open_tasks} open tasks · {data.counts.files.toLocaleString()} files indexed in {data.counts.folders} folders ·{" "}
          {data.counts.memories} memories
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Daily summary" icon={<Sparkles size={15} className="text-accent" />} className="lg:col-span-2">
          <p className="whitespace-pre-line text-sm leading-relaxed text-text/90">{data.summary.text}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {["Plan my day", "Summarize everything important from today", "Find large files I haven't used recently"].map((c) => (
              <button key={c} className="btn-outline text-xs" onClick={() => run(c)}>{c}</button>
            ))}
          </div>
        </Section>

        <Section title="System" icon={<Cpu size={15} className="text-accent" />}>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted">Local model</span>
              <span className={data.llm.ok ? "text-ok" : "text-warn"}>{data.llm.ok ? "connected" : "offline"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Automation</span>
              <span className={data.automation_paused ? "text-warn" : "text-ok"}>{data.automation_paused ? "paused" : "active"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Waiting for approval</span>
              <span>{data.pending_actions.length}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Completed today</span>
              <span>{data.summary.completed_today}</span>
            </div>
          </div>
        </Section>

        {data.pending_actions.length > 0 && (
          <Section title="Waiting for your approval" icon={<ShieldCheck size={15} className="text-warn" />} className="lg:col-span-3">
            <div className="grid gap-3 md:grid-cols-2">
              {data.pending_actions.map((a) => <ApprovalCard key={a.id} action={a} onChange={() => reload()} />)}
            </div>
          </Section>
        )}

        <Section title="Crew" icon={<Network size={15} className="text-accent" />} className="lg:col-span-2"
          actions={<button className="btn-ghost text-xs" onClick={() => navigate("/crew")}>Open Control Center</button>}>
          <div className="mb-3 flex flex-wrap gap-x-4 text-xs text-muted">
            <span>{data.crew.active_missions} active mission(s)</span>
            {data.crew.open_briefs > 0 && <span className="text-warn">{data.crew.open_briefs} question(s) for you</span>}
            <span>{(data.crew.spend.tokens_in + data.crew.spend.tokens_out).toLocaleString()} tokens today</span>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {data.crew.agents.map((a) => (
              <button key={a.slug} onClick={() => navigate(a.current ? `/crew?mission=${a.current.mission_id}` : "/crew")}
                className="flex items-center gap-2 rounded-lg border border-line p-2 text-left hover:border-accent">
                <AgentAvatar icon={a.icon} color={a.color} state={a.state} size={30} />
                <div className="min-w-0">
                  <div className="truncate text-xs font-semibold">{a.name}</div>
                  <div className={`truncate text-[11px] ${STATE_TONE[a.state]}`}>{a.current?.title ?? STATE_LABEL[a.state]}</div>
                </div>
              </button>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {["Plan my week around my deadlines", "Tidy my Downloads and tell me what's taking space"].map((g) => (
              <button key={g} className="btn-outline text-xs" onClick={() => navigate(`/crew?goal=${encodeURIComponent(g)}`)}>{g}</button>
            ))}
          </div>
        </Section>

        <div className="space-y-4">
          <Section title="Today" icon={<CalendarDays size={15} className="text-accent" />}>
            {data.events_today.length === 0 ? <Empty>No events today.</Empty> : (
              <ul className="space-y-1 text-sm">
                {data.events_today.map((e) => (
                  <li key={e.id} className="flex gap-2">
                    <span className="w-14 shrink-0 text-xs text-muted">{e.all_day ? "all day" : new Date(e.start_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                    <span className="truncate">{e.title}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Automations" icon={<Workflow size={15} className="text-accent" />}
            actions={<button className="btn-ghost text-xs" onClick={() => navigate("/automations")}>Manage</button>}>
            <div className="text-xs text-muted">{data.automations.enabled} of {data.automations.total} workflow(s) enabled{data.automation_paused ? " · paused" : ""}</div>
            <ul className="mt-2 space-y-1 text-sm">
              {data.automations.next.map((w) => (
                <li key={w.id} className="flex justify-between gap-2"><span className="truncate">{w.name}</span><span className="shrink-0 text-xs text-muted">{when(w.next_run_at)}</span></li>
              ))}
            </ul>
            {data.automations.recent_runs.slice(0, 3).map((r) => (
              <div key={r.id} className="mt-1 truncate text-[11px] text-muted">
                <span className={r.status === "failed" ? "text-danger" : r.status === "done" ? "text-ok" : ""}>{r.status}</span> · {r.name} · {ago(r.started_at)}
              </div>
            ))}
          </Section>
        </div>

        <Section title="Priorities" icon={<CalendarClock size={15} className="text-accent" />} className="lg:col-span-2"
          actions={<button className="btn-ghost text-xs" onClick={() => navigate("/calendar")}>All tasks</button>}>
          {data.priorities.length === 0 ? (
            <Empty>No open tasks. Ask the assistant to add one.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {data.priorities.map((t) => (
                <li key={t.id} className="flex items-center gap-3 py-2">
                  <input
                    type="checkbox"
                    className="accent-[var(--color-accent)]"
                    onChange={async () => {
                      await api.updateTask(t.id, { status: "done" });
                      notifyChanged();
                    }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{t.title}</div>
                    <div className="text-xs text-muted">
                      {t.due_at && <span className={isOverdue(t.due_at) ? "text-danger" : ""}>Due {when(t.due_at)}</span>}
                      {t.due_at && t.project && " · "}
                      {t.project}
                    </div>
                  </div>
                  {t.priority >= 3 && <span className="chip border-warn/40 text-warn">{PRIORITY_LABEL[t.priority]}</span>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Suggestions" icon={<Lightbulb size={15} className="text-warn" />}>
          {data.suggestions.length === 0 ? (
            <Empty>Nothing needs attention.</Empty>
          ) : (
            <ul className="space-y-2">
              {data.suggestions.map((s) => (
                <li key={s.id} className="group rounded-lg border border-line p-2.5">
                  <div className="flex items-start gap-2">
                    <AlertCircle size={14} className="mt-0.5 shrink-0 text-accent" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">{s.title}</div>
                      <div className="text-xs text-muted">{s.detail}</div>
                      {s.action && (
                        <button className="btn-outline mt-1.5 text-xs" onClick={async () => {
                          try {
                            await api.suggestionAction(s.action!);
                            toast("Done");
                            notifyChanged();
                          } catch (e) {
                            toast((e as Error).message, "error");
                          }
                        }}>{s.action.label}</button>
                      )}
                      {!s.action && (s.command || s.link) && (
                        <button
                          className="mt-1.5 text-xs text-accent hover:underline"
                          onClick={() => (s.command ? run(s.command) : navigate(s.link!))}
                        >
                          {s.command ? `Ask: “${s.command}”` : "Open"}
                        </button>
                      )}
                    </div>
                    <button
                      className="text-muted opacity-0 hover:text-text group-hover:opacity-100"
                      title="Dismiss"
                      onClick={async () => {
                        await api.dismissSuggestion(s.id);
                        reload();
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Recent files" icon={<FileText size={15} className="text-accent" />} className="lg:col-span-2">
          {data.recent_files.length === 0 ? (
            <Empty>Approve a folder in Settings to see your files here.</Empty>
          ) : (
            data.recent_files.map((f) => <FileRow key={f.id} file={f} />)
          )}
        </Section>

        <Section title="Recent activity" icon={<Activity size={15} className="text-accent" />}
          actions={<button className="btn-ghost text-xs" onClick={() => navigate("/activity")}>View log</button>}>
          {data.recent_activity.length === 0 ? (
            <Empty>No activity yet.</Empty>
          ) : (
            <ul className="space-y-2">
              {data.recent_activity.map((a) => (
                <li key={a.id} className="text-sm">
                  <div className="line-clamp-2">{a.summary}</div>
                  <div className="text-xs text-muted">{ago(a.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <WorkspacesCard className="lg:col-span-3" />
      </div>
    </div>
  );
}
