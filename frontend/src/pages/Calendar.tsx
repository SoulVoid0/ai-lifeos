import { useEffect, useMemo, useState } from "react";
import { Bell, CalendarRange, ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import { api, type Task } from "../lib/api";
import { isOverdue, PRIORITY_LABEL, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, ErrorBox, PageHeader, Section, notifyChanged, useToast } from "../components/ui";

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

export default function Calendar() {
  const toast = useToast();
  const [status, setStatus] = useState<"open" | "done" | "all">("open");
  const tasks = useAsync(() => api.tasks(status), [status]);
  const allOpen = useAsync(() => api.tasks("open"), []);
  const plan = useAsync(() => api.planDay(6), []);
  const [month, setMonth] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState({ title: "", due_at: "", remind_at: "", priority: "2", project: "", estimate_minutes: "" });

  useEffect(() => {
    const h = () => { tasks.reload(); allOpen.reload(); plan.reload(); };
    window.addEventListener("lifeos:changed", h);
    return () => window.removeEventListener("lifeos:changed", h);
  }, [tasks.reload, allOpen.reload, plan.reload]);

  const refresh = () => notifyChanged();

  const byDay = useMemo(() => {
    const m: Record<string, Task[]> = {};
    for (const t of allOpen.data ?? []) {
      const iso = t.due_at ?? t.remind_at;
      if (!iso) continue;
      (m[dayKey(new Date(iso))] ??= []).push(t);
    }
    return m;
  }, [allOpen.data]);

  const cells = useMemo(() => {
    const start = new Date(month);
    start.setDate(1 - ((month.getDay() + 6) % 7)); // weeks start Monday
    return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  }, [month]);

  const add = async () => {
    if (!draft.title.trim()) return;
    try {
      await api.createTask({
        title: draft.title,
        due_at: draft.due_at || null,
        remind_at: draft.remind_at || null,
        priority: Number(draft.priority),
        project: draft.project || null,
        estimate_minutes: draft.estimate_minutes ? Number(draft.estimate_minutes) : null,
      });
      setDraft({ ...draft, title: "", due_at: "", remind_at: "", estimate_minutes: "" });
      refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const today = dayKey(new Date());
  const visible = (tasks.data ?? []).filter((t) => {
    if (!selected) return true;
    const iso = t.due_at ?? t.remind_at;
    return iso && dayKey(new Date(iso)) === selected;
  });

  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Calendar" subtitle="Tasks, deadlines and reminders." />
      <ErrorBox error={tasks.error} />

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="card p-4 lg:col-span-3">
          <div className="mb-3 flex items-center justify-between">
            <button className="btn-ghost px-1.5" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft size={16} /></button>
            <div className="text-sm font-semibold">{month.toLocaleDateString([], { month: "long", year: "numeric" })}</div>
            <button className="btn-ghost px-1.5" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight size={16} /></button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-muted">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d}>{d}</div>)}
          </div>
          <div className="mt-1 grid grid-cols-7 gap-1">
            {cells.map((d) => {
              const k = dayKey(d);
              const items = byDay[k] ?? [];
              const inMonth = d.getMonth() === month.getMonth();
              return (
                <button
                  key={k}
                  onClick={() => setSelected(selected === k ? null : k)}
                  className={`h-20 overflow-hidden rounded-lg border p-1 text-left align-top text-xs transition ${
                    selected === k ? "border-accent" : "border-line hover:border-muted/50"
                  } ${inMonth ? "bg-panel-2" : "opacity-40"}`}
                >
                  <div className={`mb-0.5 ${k === today ? "font-bold text-accent" : "text-muted"}`}>{d.getDate()}</div>
                  {items.slice(0, 2).map((t) => (
                    <div key={t.id} className={`truncate rounded px-1 ${isOverdue(t.due_at) ? "bg-danger/15 text-danger" : "bg-accent-soft"}`}>
                      {t.title}
                    </div>
                  ))}
                  {items.length > 2 && <div className="px-1 text-muted">+{items.length - 2}</div>}
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-4 lg:col-span-2">
          <Section title="New task" icon={<Plus size={15} className="text-accent" />}>
            <div className="space-y-2">
              <input className="input" placeholder="Title" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                onKeyDown={(e) => e.key === "Enter" && add()} />
              <div className="grid grid-cols-2 gap-2">
                <input className="input" placeholder="Due: fri 5pm" value={draft.due_at} onChange={(e) => setDraft({ ...draft, due_at: e.target.value })} />
                <input className="input" placeholder="Remind: tomorrow 9am" value={draft.remind_at} onChange={(e) => setDraft({ ...draft, remind_at: e.target.value })} />
                <select className="input" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })}>
                  {[1, 2, 3, 4].map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
                </select>
                <input className="input" placeholder="Minutes" type="number" value={draft.estimate_minutes}
                  onChange={(e) => setDraft({ ...draft, estimate_minutes: e.target.value })} />
              </div>
              <input className="input" placeholder="Project (optional)" value={draft.project} onChange={(e) => setDraft({ ...draft, project: e.target.value })} />
              <button className="btn-primary w-full justify-center" onClick={add}><Plus size={14} /> Add task</button>
            </div>
          </Section>

          <Section title={`Suggested plan for ${plan.data?.day ?? "today"}`} icon={<CalendarRange size={15} className="text-accent" />}>
            {!plan.data?.blocks.length ? <Empty>No open tasks to schedule.</Empty> : (
              <ul className="space-y-1 text-sm">
                {plan.data.blocks.map((b) => (
                  <li key={b.task_id} className="flex gap-3">
                    <span className="w-20 shrink-0 text-muted">{new Date(b.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                    <span className="flex-1 truncate">{b.title}</span>
                    <span className="text-muted">{b.minutes}m</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>

      <div className="card mt-4 p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">
            {selected ? `Tasks on ${cells.find((d) => dayKey(d) === selected)?.toLocaleDateString()}` : "Tasks"}
            {selected && <button className="ml-2 text-xs text-accent" onClick={() => setSelected(null)}>show all</button>}
          </h2>
          <div className="flex gap-1">
            {(["open", "done", "all"] as const).map((s) => (
              <button key={s} className={s === status ? "btn-outline text-xs" : "btn-ghost text-xs"} onClick={() => setStatus(s)}>{s}</button>
            ))}
          </div>
        </div>
        {visible.length === 0 ? <Empty>No tasks.</Empty> : (
          <ul className="divide-y divide-line">
            {visible.map((t) => (
              <li key={t.id} className="group flex items-start gap-3 py-2">
                <input
                  type="checkbox"
                  className="mt-1 accent-[var(--color-accent)]"
                  checked={t.status === "done"}
                  onChange={async () => { await api.updateTask(t.id, { status: t.status === "done" ? "open" : "done" }); refresh(); }}
                />
                <div className="min-w-0 flex-1">
                  <div className={`text-sm ${t.status === "done" ? "text-muted line-through" : ""}`}>{t.title}</div>
                  <div className="flex flex-wrap gap-x-3 text-xs text-muted">
                    {t.due_at && <span className={isOverdue(t.due_at) && t.status === "open" ? "text-danger" : ""}>Due {when(t.due_at)}</span>}
                    {t.remind_at && <span className="inline-flex items-center gap-1"><Bell size={11} /> {when(t.remind_at)}</span>}
                    {t.project && <span>{t.project}</span>}
                    {t.priority !== 2 && <span>{PRIORITY_LABEL[t.priority]}</span>}
                    {t.estimate_minutes && <span>{t.estimate_minutes} min</span>}
                  </div>
                  {t.notes && <div className="mt-1 whitespace-pre-line text-xs text-muted">{t.notes}</div>}
                </div>
                <button className="btn-ghost px-1.5 opacity-0 hover:text-danger group-hover:opacity-100" title="Delete"
                  onClick={async () => { await api.deleteTask(t.id); refresh(); }}>
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
