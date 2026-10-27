import { useState } from "react";
import { Brain, Pencil, Pin, PinOff, Plus, Save, Search, Trash2, X } from "lucide-react";
import { api, type Memory as M } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, ErrorBox, PageHeader, useToast } from "../components/ui";

const KINDS = ["preference", "project", "fact", "routine", "note", "person"];

export default function Memory() {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const list = useAsync(() => api.memories(query || undefined, kind || undefined), [query, kind]);
  const [draft, setDraft] = useState({ content: "", kind: "fact" });
  const [editing, setEditing] = useState<{ id: number; content: string; kind: string } | null>(null);

  const add = async () => {
    if (!draft.content.trim()) return;
    try {
      await api.addMemory(draft);
      setDraft({ content: "", kind: draft.kind });
      list.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const items = (list.data ?? []).filter((m) => !kind || m.kind === kind);

  return (
    <div className="mx-auto max-w-4xl p-6">
      <PageHeader title="Memory" subtitle="What LifeOS remembers about you. Everything here is visible, editable and deletable." />
      <ErrorBox error={list.error} />

      <div className="card mb-4 p-4">
        <label className="label">Add a memory</label>
        <div className="flex flex-wrap gap-2">
          <input
            className="input min-w-64 flex-1"
            placeholder="e.g. My programming project lives in ~/code/thesis"
            value={draft.content}
            onChange={(e) => setDraft({ ...draft, content: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <select className="input w-36" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            {KINDS.map((k) => <option key={k}>{k}</option>)}
          </select>
          <button className="btn-primary" onClick={add}><Plus size={15} /> Save</button>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <form className="relative flex-1" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input pl-9" placeholder="Search memories (semantic when a model is connected)" value={q}
            onChange={(e) => { setQ(e.target.value); if (!e.target.value) setQuery(""); }} />
        </form>
        <select className="input w-40" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k}>{k}</option>)}
        </select>
      </div>

      <div className="card divide-y divide-line">
        {items.length === 0 && <Empty><Brain size={20} className="mx-auto mb-2" />No memories{query ? " match" : " yet"}.</Empty>}
        {items.map((m: M) => (
          <div key={m.id} className="flex items-start gap-3 p-3">
            {editing?.id === m.id ? (
              <div className="flex flex-1 flex-wrap gap-2">
                <input className="input flex-1" value={editing.content} onChange={(e) => setEditing({ ...editing, content: e.target.value })} />
                <select className="input w-32" value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value })}>
                  {KINDS.map((k) => <option key={k}>{k}</option>)}
                </select>
                <button className="btn-primary" onClick={async () => {
                  await api.updateMemory(m.id, { content: editing.content, kind: editing.kind });
                  setEditing(null);
                  list.reload();
                }}><Save size={14} /></button>
                <button className="btn-ghost" onClick={() => setEditing(null)}><X size={14} /></button>
              </div>
            ) : (
              <>
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{m.content}</div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                    <span className="chip">{m.kind}</span>
                    {m.pinned ? <span className="chip border-accent/50 text-accent">pinned</span> : null}
                    <span>{m.source ?? "user"} · {ago(m.updated_at)}</span>
                    {m.score !== undefined && <span>relevance {m.score.toFixed(2)}</span>}
                  </div>
                </div>
                <button className="btn-ghost px-1.5" title={m.pinned ? "Unpin" : "Pin (always given to the assistant)"}
                  onClick={async () => { await api.updateMemory(m.id, { pinned: !m.pinned }); list.reload(); }}>
                  {m.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                </button>
                <button className="btn-ghost px-1.5" title="Edit" onClick={() => setEditing({ id: m.id, content: m.content, kind: m.kind })}>
                  <Pencil size={14} />
                </button>
                <button className="btn-ghost px-1.5 hover:text-danger" title="Delete"
                  onClick={async () => { await api.deleteMemory(m.id); toast("Memory deleted"); list.reload(); }}>
                  <Trash2 size={14} />
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
