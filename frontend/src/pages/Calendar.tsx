import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, Bell, CalendarPlus, CalendarRange, ChevronLeft, ChevronRight, Download, ListTree, Link2, MapPin, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api, type CalEvent, type Task } from "../lib/api";
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
  const [params] = useSearchParams();
  const [draft, setDraft] = useState({ title: "", due_at: "", remind_at: "", priority: "2", project: params.get("project") ?? "", estimate_minutes: "" });
  const [ev, setEv] = useState({ title: "", start: "", end: "", location: "" });
  const [source, setSource] = useState("");
  const sources = useAsync(api.calendarSources, []);
  const overlaps = useAsync(api.overlaps, []);

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

  const events = useAsync(() => api.events(cells[0].toISOString(), new Date(cells[41].getTime() + 86400000).toISOString()), [cells[0].getTime()]);
  const eventsByDay = useMemo(() => {
    const m: Record<string, CalEvent[]> = {};
    for (const e of events.data ?? []) (m[dayKey(new Date(e.start_at))] ??= []).push(e);
    return m;
  }, [events.data]);

  const reloadCalendars = () => { sources.reload(); events.reload(); overlaps.reload(); };

  const addEvent = async () => {
    if (!ev.title.trim() || !ev.start.trim()) return;
    try {
      await api.addEvent({ title: ev.title, start: ev.start, end: ev.end || undefined, location: ev.location || undefined });
      setEv({ title: "", start: "", end: "", location: "" });
      reloadCalendars();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const addSource = async () => {
    if (!source.trim()) return;
    try {
      const s = await api.addCalendarSource(source.trim());
      setSource("");
      toast(s.last_error ? `Added, but sync failed: ${s.last_error}` : `Imported ${s.event_count} event(s)`, s.last_error ? "error" : "ok");
      reloadCalendars();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const exportIcs = async () => {
    const text = await api.exportIcs();
    const url = URL.createObjectURL(new Blob([text], { type: "text/calendar" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "lifeos.ics";
    a.click();
    URL.revokeObjectURL(url);
  };

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
  const dayEvents = selected ? eventsByDay[selected] ?? [] : (events.data ?? []).filter((e) => new Date(e.end_at ?? e.start_at).getTime() >= Date.now()).slice(0, 8);
  const visible = (tasks.data ?? []).filter((t) => {
    if (!selected) return true;
    const iso = t.due_at ?? t.remind_at;
    return iso && dayKey(new Date(iso)) === selected;
  });

  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Calendar" subtitle="Events from your calendars, plus tasks, deadlines and reminders."
        actions={<button className="btn-outline" onClick={exportIcs}><Download size={14} /> Export .ics</button>} />
      {(overlaps.data ?? []).length > 0 && (
        <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <div className="flex items-center gap-2 font-medium"><AlertTriangle size={15} /> {overlaps.data!.length} overlapping appointment(s) in the next two weeks</div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {overlaps.data!.slice(0, 5).map(({ a, b }) => <li key={`${a.id}-${b.id}`}>“{a.title}” and “{b.title}” · {when(a.start_at)}</li>)}
          </ul>
        </div>
      )}
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
                  {(eventsByDay[k] ?? []).slice(0, 2).map((e) => (
                    <div key={`e${e.id}`} className="truncate rounded border-l-2 border-ok bg-ok/10 px-1">{e.title}</div>
                  ))}
                  {items.slice(0, Math.max(0, 2 - (eventsByDay[k] ?? []).length)).map((t) => (
                    <div key={t.id} className={`truncate rounded px-1 ${isOverdue(t.due_at) ? "bg-danger/15 text-danger" : "bg-accent-soft"}`}>
                      {t.title}
                    </div>
                  ))}
                  {items.length + (eventsByDay[k] ?? []).length > 2 && <div className="px-1 text-muted">+{items.length + (eventsByDay[k] ?? []).length - 2}</div>}
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

          <Section title="New event" icon={<CalendarPlus size={15} className="text-accent" />}>
            <div className="space-y-2">
              <input className="input" placeholder="Title" value={ev.title} onChange={(e) => setEv({ ...ev, title: e.target.value })} />
              <div className="grid grid-cols-2 gap-2">
                <input className="input" placeholder="Start: tomorrow 3pm" value={ev.start} onChange={(e) => setEv({ ...ev, start: e.target.value })} />
                <input className="input" placeholder="End (optional)" value={ev.end} onChange={(e) => setEv({ ...ev, end: e.target.value })} />
              </div>
              <input className="input" placeholder="Location (optional)" value={ev.location} onChange={(e) => setEv({ ...ev, location: e.target.value })} />
              <button className="btn-outline w-full justify-center" onClick={addEvent}><Plus size={14} /> Add event</button>
            </div>
          </Section>

          <Section title="Calendars" icon={<Link2 size={15} className="text-accent" />}>
            <p className="mb-2 text-xs text-muted">Subscribe to an .ics URL (Google, Outlook and iCloud all offer one) or import an .ics file from an approved folder. Read-only, synced every 30 minutes.</p>
            <div className="mb-2 flex gap-2">
              <input className="input" placeholder="https://…/basic.ics or Downloads/calendar.ics" value={source} onChange={(e) => setSource(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addSource()} />
              <button className="btn-primary" onClick={addSource}><Plus size={14} /></button>
            </div>
            <ul className="divide-y divide-line">
              {(sources.data ?? []).map((s) => (
                <li key={s.id} className="flex items-center gap-2 py-1.5 text-xs">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono">{s.label || s.location}</div>
                    <div className={s.last_error ? "text-danger" : "text-muted"}>{s.last_error ?? `${s.event_count} events · synced ${when(s.last_synced_at)}`}</div>
                  </div>
                  <button className="btn-ghost px-1.5" title="Sync now" onClick={async () => { await api.syncCalendarSource(s.id); reloadCalendars(); }}><RefreshCw size={13} /></button>
                  <button className="btn-ghost px-1.5" title="Remove" onClick={async () => { await api.removeCalendarSource(s.id); reloadCalendars(); }}><Trash2 size={13} /></button>
                </li>
              ))}
            </ul>
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

      {dayEvents.length > 0 && (
        <div className="card mt-4 p-4">
          <h2 className="mb-2 text-sm font-semibold">{selected ? "Events that day" : "Upcoming events"}</h2>
          <ul className="divide-y divide-line">
            {dayEvents.map((e) => (
              <li key={e.id} className="group flex items-start gap-3 py-2 text-sm">
                <span className="w-32 shrink-0 text-xs text-muted">{e.all_day ? new Date(e.start_at).toLocaleDateString() : when(e.start_at)}</span>
                <div className="min-w-0 flex-1">
                  <div>{e.title}</div>
                  <div className="flex flex-wrap gap-x-3 text-xs text-muted">
                    {e.location && <span className="inline-flex items-center gap-1"><MapPin size={11} /> {e.location}</span>}
                    <span>{e.source_label ?? (e.source_id ? "subscribed" : "LifeOS")}</span>
                  </div>
                </div>
                {!e.source_id && (
                  <button className="btn-ghost px-1.5 opacity-0 hover:text-danger group-hover:opacity-100" onClick={async () => { await api.deleteEvent(e.id); reloadCalendars(); }}>
                    <Trash2 size={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

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
                {t.status === "open" && !t.parent_id && (
                  <button className="btn-ghost px-1.5 opacity-0 group-hover:opacity-100" title="Break into steps"
                    onClick={async () => {
                      try {
                        const r = await api.breakDownTask(t.id);
                        toast(`Split into ${r.subtasks.length} steps with their own due dates`);
                        refresh();
                      } catch (e) { toast((e as Error).message, "error"); }
                    }}>
                    <ListTree size={14} />
                  </button>
                )}
                {t.status === "open" && !t.parent_id && (allOpen.data ?? []).some((x) => x.parent_id === t.id) && (
                  <button className="btn-ghost px-1.5 opacity-0 group-hover:opacity-100" title="Replan the remaining steps"
                    onClick={async () => {
                      try {
                        const r = await api.replanTask(t.id);
                        toast(`Moved ${r.moved.length} step(s)`);
                        refresh();
                      } catch (e) { toast((e as Error).message, "error"); }
                    }}>
                    <RefreshCw size={14} />
                  </button>
                )}
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
