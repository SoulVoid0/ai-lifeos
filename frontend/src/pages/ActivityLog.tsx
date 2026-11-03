import { useState } from "react";
import { api } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, ErrorBox, PageHeader } from "../components/ui";

const KINDS = ["", "action", "proposal", "decision", "undo", "tool", "reminder", "memory", "permission", "settings", "data", "llm"];

const TONE: Record<string, string> = {
  ok: "text-ok",
  error: "text-danger",
  blocked: "text-danger",
  pending: "text-warn",
  rejected: "text-muted",
};

export default function ActivityLog() {
  const [kind, setKind] = useState("");
  const [view, setView] = useState<"log" | "actions">("log");
  const log = useAsync(() => api.activity(kind || undefined), [kind], 15000);
  const actions = useAsync(() => api.actions(), [view]);

  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader
        title="Activity"
        subtitle="Every action LifeOS proposed or performed, with undo where possible."
        actions={
          <div className="flex gap-1">
            <button className={view === "log" ? "btn-outline" : "btn-ghost"} onClick={() => setView("log")}>Log</button>
            <button className={view === "actions" ? "btn-outline" : "btn-ghost"} onClick={() => setView("actions")}>Actions</button>
          </div>
        }
      />
      <ErrorBox error={log.error ?? actions.error} />

      {view === "log" && (
        <>
          <select className="input mb-3 w-48" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k || "All events"}</option>)}
          </select>
          <div className="card divide-y divide-line">
            {(log.data ?? []).length === 0 && <Empty>No activity yet.</Empty>}
            {(log.data ?? []).map((e) => (
              <details key={e.id} className="group px-4 py-2.5">
                <summary className="flex cursor-pointer list-none items-start gap-3">
                  <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${TONE[e.status]?.replace("text-", "bg-") ?? "bg-muted"}`} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm">{e.summary}</div>
                    <div className="text-xs text-muted">
                      {e.kind}{e.tool ? ` · ${e.tool}` : ""} · <span className={TONE[e.status]}>{e.status}</span> · {ago(e.created_at)}
                    </div>
                  </div>
                  <span className="text-xs text-muted">{new Date(e.created_at).toLocaleString()}</span>
                </summary>
                {e.details != null && (
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-bg p-2 text-xs text-muted">{JSON.stringify(e.details, null, 2)}</pre>
                )}
              </details>
            ))}
          </div>
        </>
      )}

      {view === "actions" && (
        <div className="grid gap-3 md:grid-cols-2">
          {(actions.data ?? []).length === 0 && <Empty>No actions yet.</Empty>}
          {(actions.data ?? []).map((a) => <ApprovalCard key={a.id} action={a} />)}
        </div>
      )}
    </div>
  );
}
