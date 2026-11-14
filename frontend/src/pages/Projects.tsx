import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderGit2, FolderPlus, Pin, PinOff, Radar, Rocket, Trash2 } from "lucide-react";
import { api, type Project } from "../lib/api";
import { ago, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { FileRow } from "../components/FileRow";
import { Markdown } from "../components/Markdown";
import { Empty, ErrorBox, PageHeader, Section, Spinner, notifyChanged, useToast } from "../components/ui";

const STATUS_TONE: Record<Project["status"], string> = { active: "border-ok/40 text-ok", paused: "border-warn/40 text-warn", done: "text-muted" };

export default function Projects() {
  const toast = useToast();
  const navigate = useNavigate();
  const list = useAsync(() => api.projects(), []);
  const [selected, setSelected] = useState<number | null>(null);
  const ctx = useAsync(() => (selected ? api.project(selected) : Promise.resolve(null)), [selected]);
  const [path, setPath] = useState("");
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    if (selected === null && list.data?.length) setSelected(list.data[0].id);
  }, [list.data, selected]);

  const scan = async () => {
    setScanning(true);
    try {
      const r = await api.detectProjects();
      toast(`Found ${r.found} project folder(s), ${r.added} new`);
      list.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setScanning(false);
    }
  };

  const add = async () => {
    if (!path.trim()) return;
    try {
      const p = await api.addProject(path.trim());
      setPath("");
      list.reload();
      setSelected(p.id);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const update = async (id: number, patch: Parameters<typeof api.updateProject>[1]) => {
    await api.updateProject(id, patch);
    list.reload();
    ctx.reload();
  };

  const c = ctx.data;
  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Projects" subtitle="LifeOS finds project folders (git, Node, Python, Rust, …) in your approved folders and keeps their context: recent files, open tasks and notes."
        actions={<button className="btn-outline" onClick={scan} disabled={scanning}>{scanning ? <Spinner /> : <Radar size={14} />} Scan for projects</button>} />
      <ErrorBox error={list.error} />
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-3">
          <div className="flex gap-2">
            <input className="input" placeholder="Add a folder as a project…" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="btn-primary" onClick={add}><FolderPlus size={14} /></button>
          </div>
          <div className="card divide-y divide-line">
            {!(list.data ?? []).length && <Empty>No projects yet. Approve a folder that contains your code or documents, then scan.</Empty>}
            {(list.data ?? []).map((p) => (
              <button key={p.id} onClick={() => setSelected(p.id)} className={`flex w-full items-center gap-2 px-3 py-2.5 text-left ${selected === p.id ? "bg-accent-soft" : "hover:bg-panel-2"}`}>
                <FolderGit2 size={16} className="shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 truncate text-sm font-medium">{p.pinned ? <Pin size={11} className="text-accent" /> : null}{p.name}</div>
                  <div className="text-xs text-muted">{p.kind} · {p.last_activity_at ? `edited ${ago(p.last_activity_at)}` : "no activity"}{p.open_tasks ? ` · ${p.open_tasks} open` : ""}</div>
                </div>
                <span className={`chip ${STATUS_TONE[p.status]}`}>{p.status}</span>
              </button>
            ))}
          </div>
        </div>

        <div>
          {!c ? (selected ? <Spinner /> : null) : (
            <div className="space-y-4">
              <div className="card p-4">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-lg font-semibold">{c.project.name}</div>
                    <div className="truncate font-mono text-xs text-muted">{c.project.path}</div>
                    <div className="mt-1 text-xs text-muted">{c.project.kind} · {c.file_count} indexed files · {c.project.auto ? "auto-detected" : "added by you"}</div>
                  </div>
                  <select className="input w-28" value={c.project.status} onChange={(e) => update(c.project.id, { status: e.target.value as Project["status"] })}>
                    <option value="active">active</option><option value="paused">paused</option><option value="done">done</option>
                  </select>
                  <button className="btn-ghost" title={c.project.pinned ? "Unpin" : "Pin"} onClick={() => update(c.project.id, { pinned: !c.project.pinned })}>
                    {c.project.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                  </button>
                  <button className="btn-ghost" title="Remove from projects (files are untouched)" onClick={async () => {
                    await api.deleteProject(c.project.id); setSelected(null); list.reload(); notifyChanged();
                  }}><Trash2 size={14} /></button>
                </div>
                <textarea className="input mt-3 min-h-14" placeholder="Notes: goals, where you left off…" defaultValue={c.project.description}
                  key={c.project.id} onBlur={(e) => e.target.value !== c.project.description && update(c.project.id, { description: e.target.value })} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn-primary" onClick={() => navigate(`/crew?goal=${encodeURIComponent(`Look at my ${c.project.name} project (${c.project.path}): summarize where I left off from the recently edited files and open tasks, and propose the next 3 steps.`)}`)}>
                    <Rocket size={14} /> Ask the crew where I left off
                  </button>
                  <button className="btn-outline" onClick={() => navigate(`/calendar?project=${encodeURIComponent(c.project.name)}`)}>Add a task</button>
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Section title={`Open tasks (${c.open_tasks.length})`}>
                  {!c.open_tasks.length && <Empty>No open tasks tagged with this project.</Empty>}
                  <ul className="space-y-1 text-sm">
                    {c.open_tasks.map((t) => <li key={t.id} className="flex justify-between gap-2"><span>{t.title}</span><span className="text-xs text-muted">{when(t.due_at)}</span></li>)}
                  </ul>
                </Section>
                <Section title="Recently edited">
                  {!c.recent_files.length && <Empty>No indexed files yet.</Empty>}
                  {c.recent_files.map((f) => <FileRow key={f.id} file={f} />)}
                </Section>
              </div>
              {c.readme && <Section title="README"><Markdown text={c.readme} /></Section>}
              {c.memories.length > 0 && (
                <Section title="Memories about this project">
                  <ul className="list-disc space-y-1 pl-5 text-sm">{c.memories.map((m) => <li key={m.id}>{m.content}</li>)}</ul>
                </Section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
