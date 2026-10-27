import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, AlertCircle, CalendarClock, Cpu, FileText, Lightbulb, ShieldCheck, Sparkles, X,
} from "lucide-react";
import { api } from "../lib/api";
import { ago, isOverdue, PRIORITY_LABEL, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "../components/ApprovalCard";
import { FileRow } from "../components/FileRow";
import { Empty, ErrorBox, Section, Spinner, notifyChanged } from "../components/ui";

export default function Dashboard() {
  const navigate = useNavigate();
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
                      {(s.command || s.link) && (
                        <button
                          className="mt-1.5 text-xs text-accent hover:underline"
                          onClick={() => (s.command ? run(s.command) : navigate(s.link!))}
                        >
                          {s.command ? `Ask: “${s.command}”` : "Open settings"}
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
      </div>
    </div>
  );
}
