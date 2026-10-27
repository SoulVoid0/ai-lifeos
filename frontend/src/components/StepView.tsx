import { AlertTriangle, Ban, CheckCircle2, Clock } from "lucide-react";
import type { FileItem, Memory, StepResult, Task } from "../lib/api";
import { bytes, when } from "../lib/format";
import { ApprovalCard } from "./ApprovalCard";
import { FileRow } from "./FileRow";

const ICON = {
  done: <CheckCircle2 size={14} className="text-ok" />,
  executed: <CheckCircle2 size={14} className="text-ok" />,
  pending: <Clock size={14} className="text-warn" />,
  error: <AlertTriangle size={14} className="text-danger" />,
  failed: <AlertTriangle size={14} className="text-danger" />,
  blocked: <Ban size={14} className="text-danger" />,
};

/** Renders one executed tool call inside an assistant message. */
export function StepView({ step }: { step: StepResult }) {
  const r = step.result ?? {};
  const fileList: FileItem[] | undefined = r.files ?? r.results?.files;
  return (
    <div className="mt-2">
      <div className="flex items-center gap-1.5 text-xs text-muted">
        {ICON[step.status] ?? null}
        <span className="font-mono">{step.tool}</span>
      </div>

      {step.action && <div className="mt-1.5"><ApprovalCard action={step.action} /></div>}

      {!step.action && fileList && fileList.length > 0 && (
        <div className="mt-1.5 max-h-64 overflow-y-auto rounded-lg border border-line">
          {fileList.slice(0, 30).map((f) => <FileRow key={f.path} file={f} />)}
        </div>
      )}

      {r.groups && (
        <div className="mt-1.5 space-y-2">
          {(r.groups as { sha256: string; size: number; files: FileItem[] }[]).slice(0, 10).map((g) => (
            <div key={g.sha256} className="rounded-lg border border-line p-2">
              <div className="text-xs text-muted">{g.files.length} copies · {bytes(g.size)} each</div>
              {g.files.map((f) => <FileRow key={f.path} file={f} compact />)}
            </div>
          ))}
        </div>
      )}

      {r.task && <TaskChip task={r.task as Task} />}

      {r.plan?.blocks?.length > 0 && (
        <div className="mt-1.5 rounded-lg border border-line p-2 text-sm">
          {(r.plan.blocks as { task_id: number; title: string; start: string; minutes: number }[]).map((b) => (
            <div key={b.task_id} className="flex gap-3 py-0.5">
              <span className="w-24 shrink-0 text-muted">{new Date(b.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
              <span className="flex-1">{b.title}</span>
              <span className="text-muted">{b.minutes}m</span>
            </div>
          ))}
        </div>
      )}

      {r.results?.memories?.length > 0 && (
        <ul className="mt-1.5 space-y-1 text-sm">
          {(r.results.memories as Memory[]).slice(0, 5).map((m) => (
            <li key={m.id} className="rounded-lg border border-line px-2 py-1"><span className="chip mr-2">{m.kind}</span>{m.content}</li>
          ))}
        </ul>
      )}

      {r.document?.text && (
        <pre className="mt-1.5 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-2 text-xs text-muted">
          {String(r.document.text).slice(0, 3000)}
        </pre>
      )}

      {(step.status === "error" || step.status === "blocked") && <p className="mt-1 text-xs text-danger">{step.summary}</p>}
    </div>
  );
}

function TaskChip({ task }: { task: Task }) {
  return (
    <div className="mt-1.5 inline-flex flex-col rounded-lg border border-line px-3 py-2 text-sm">
      <span className="font-medium">{task.title}</span>
      <span className="text-xs text-muted">
        {task.remind_at && `Reminder ${when(task.remind_at)}`}
        {task.remind_at && task.due_at && " · "}
        {task.due_at && `Due ${when(task.due_at)}`}
      </span>
      {task.notes && <span className="mt-1 whitespace-pre-line text-xs text-muted">{task.notes}</span>}
    </div>
  );
}
