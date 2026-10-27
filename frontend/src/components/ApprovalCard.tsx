import { useMemo, useState } from "react";
import { Check, FolderInput, RotateCcw, ShieldAlert, Star, X } from "lucide-react";
import { api, type Action } from "../lib/api";
import { basename, bytes } from "../lib/format";
import { notifyChanged, useToast } from "./ui";

const STATUS_STYLE: Record<string, string> = {
  pending: "border-warn/40 text-warn",
  executed: "border-ok/40 text-ok",
  rejected: "border-line text-muted",
  superseded: "border-line text-muted",
  failed: "border-danger/40 text-danger",
  undone: "border-line text-muted",
};

/** Preview of a proposed action with approve / reject / undo. File moves can be deselected. */
export function ApprovalCard({ action: initial, onChange }: { action: Action; onChange?: (a: Action) => void }) {
  const [action, setAction] = useState(initial);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const moves = action.preview?.moves ?? [];

  const groups = useMemo(() => {
    const g: Record<string, typeof moves> = {};
    for (const m of moves) (g[m.category] ??= []).push(m);
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b));
  }, [moves]);

  const run = async (fn: () => Promise<Action>, ok: (a: Action) => string) => {
    setBusy(true);
    try {
      const a = await fn();
      setAction(a);
      onChange?.(a);
      toast(a.status === "failed" ? `Failed: ${a.error}` : ok(a), a.status === "failed" ? "error" : "ok");
      notifyChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const toggle = (src: string) =>
    setExcluded((s) => {
      const n = new Set(s);
      n.has(src) ? n.delete(src) : n.add(src);
      return n;
    });

  const selected = moves.length - excluded.size;

  return (
    <div className="rounded-xl border border-line bg-panel-2 p-3">
      <div className="flex items-start gap-2">
        <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{action.summary}</div>
          <div className="mt-0.5 text-xs text-muted">
            {action.tool.replace(/_/g, " ")} · #{action.id}
          </div>
        </div>
        <span className={`chip ${STATUS_STYLE[action.status] ?? ""}`}>{action.status}</span>
      </div>

      {moves.length > 0 && (
        <div className="mt-3 max-h-72 space-y-3 overflow-y-auto pr-1">
          {groups.map(([cat, items]) => (
            <div key={cat}>
              <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted">
                <FolderInput size={13} /> {cat}/ <span className="font-normal">({items.length})</span>
              </div>
              <ul className="space-y-0.5">
                {items.map((m) => (
                  <li key={m.src} className="flex items-center gap-2 text-sm">
                    {action.status === "pending" ? (
                      <input
                        type="checkbox"
                        className="accent-[var(--color-accent)]"
                        checked={!excluded.has(m.src)}
                        onChange={() => toggle(m.src)}
                      />
                    ) : null}
                    <span className={`truncate ${excluded.has(m.src) ? "text-muted line-through" : ""}`} title={m.src}>
                      {basename(m.src)}
                    </span>
                    {m.important && <Star size={12} className="shrink-0 text-warn" aria-label="important" />}
                    {m.size !== undefined && <span className="ml-auto shrink-0 text-xs text-muted">{bytes(m.size)}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {action.result?.summary && action.status !== "pending" && (
        <p className="mt-2 text-xs text-muted">{action.result.summary}</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {action.status === "pending" && (
          <>
            <button
              className="btn-primary"
              disabled={busy || (moves.length > 0 && selected === 0)}
              onClick={() => run(() => api.approve(action.id, [...excluded]), (a) => a.result?.summary ?? "Done")}
            >
              <Check size={14} /> Approve{moves.length > 0 ? ` (${selected})` : ""}
            </button>
            <button className="btn-outline" disabled={busy} onClick={() => run(() => api.reject(action.id), () => "Rejected")}>
              <X size={14} /> Reject
            </button>
          </>
        )}
        {action.status === "executed" && (
          <button
            className="btn-ghost"
            disabled={busy}
            onClick={() => run(() => api.undo(action.id), () => "Undone")}
          >
            <RotateCcw size={14} /> Undo
          </button>
        )}
      </div>
    </div>
  );
}
