// Code-to-app: describe a small tool and Forge builds it. Apps run sandboxed (no network, no access to LifeOS or files).
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Calculator, CalendarClock, ChartLine, Clock, Dice5, ExternalLink, Gamepad2, Globe, Hammer, ListChecks, Music, NotebookPen,
  Palette, Timer, Trash2, Undo2, Wand2, Wrench,
} from "lucide-react";
import { api, type MiniApp } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { isDesktop, openMiniApp } from "../lib/desktop";
import { play } from "../lib/sfx";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

const ICONS: Record<string, typeof Wrench> = {
  wrench: Wrench, timer: Timer, calculator: Calculator, list: ListChecks, note: NotebookPen, chart: ChartLine, dice: Dice5,
  palette: Palette, music: Music, clock: Clock, globe: Globe, game: Gamepad2, calendar: CalendarClock,
};
const IDEAS = ["A pomodoro timer with a task list and a daily total", "A unit converter for cooking (cups, grams, °F/°C)",
  "A habit tracker grid for the month", "A random team picker for game night", "A loan / savings calculator with a chart",
  "A markdown notepad with live preview"];

export default function Tools() {
  const toast = useToast();
  const apps = useAsync(api.miniApps, [], 3000);
  const [desc, setDesc] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const build = async () => {
    try {
      const a = await api.buildMiniApp(desc.trim());
      play("start");
      setDesc("");
      setSel(a.id);
      apps.reload();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const list = apps.data ?? [];
  const current = list.find((a) => a.id === sel) ?? list[0];
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <PageHeader title="Tools" subtitle="Describe a small tool and Forge builds it as a little app of its own. Apps run sandboxed: no internet, no access to your files or to LifeOS." />
      <Section title="Build a tool" icon={<Hammer size={15} className="text-accent" />}>
        <textarea className="input min-h-20" placeholder="What should it do? Who is it for? Any must-have features?" value={desc} onChange={(e) => setDesc(e.target.value)} />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {IDEAS.map((i) => <button key={i} className="chip hover:text-text" onClick={() => setDesc(i)}>{i}</button>)}
        </div>
        <div className="mt-3 flex justify-end"><button className="btn-primary" disabled={desc.trim().length < 8} onClick={build}><Wand2 size={14} /> Build it</button></div>
      </Section>
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-1">
          {list.length === 0 && <Empty>No tools yet.</Empty>}
          {list.map((a) => {
            const Icon = ICONS[a.icon] ?? Wrench;
            return (
              <button key={a.id} onClick={() => setSel(a.id)}
                className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${current?.id === a.id ? "bg-accent-soft" : "hover:bg-panel-2"}`}>
                <Icon size={16} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1"><span className="block truncate">{a.name}</span>
                  <span className="text-xs text-muted">{a.busy ? "Forge is working…" : a.status === "failed" ? "failed" : `v${a.version} · ${ago(a.updated_at)}`}</span></span>
              </button>
            );
          })}
        </div>
        {current && <AppPanel key={current.id} a={current} onChange={apps.reload} />}
      </div>
    </div>
  );
}

function AppPanel({ a, onChange }: { a: MiniApp; onChange: () => void }) {
  const toast = useToast();
  const [change, setChange] = useState("");
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); onChange(); } catch (e) { toast((e as Error).message, "error"); } };
  return (
    <section className="card flex min-h-[560px] flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex-1 text-base font-semibold">{a.name}</h2>
        {a.version > 0 && <button className="btn-outline" onClick={() => openMiniApp(a.id, a.name)}><ExternalLink size={14} /> {isDesktop ? "Open in a window" : "Open in a tab"}</button>}
        {a.version > 1 && <button className="btn-ghost" title="Back to the previous version" onClick={() => act(() => api.undoMiniApp(a.id))}><Undo2 size={14} /></button>}
        <button className="btn-ghost" title="Delete" onClick={() => act(() => api.deleteMiniApp(a.id))}><Trash2 size={14} /></button>
      </div>
      <p className="text-xs text-muted">{a.description}</p>
      {a.error && <p className="text-sm text-danger">{a.error}</p>}
      {a.busy && <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> Forge is {a.version ? "changing" : "building"} it… (big models can take a few minutes)</div>}
      {a.version > 0 ? <MiniAppFrame id={a.id} version={a.version} className="min-h-[420px] flex-1 rounded-lg border border-line" /> : !a.busy && <Empty>Not built.</Empty>}
      {a.version > 0 && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (change.trim()) act(() => api.reviseMiniApp(a.id, change.trim()).then(() => setChange(""))); }}>
          <input className="input flex-1" placeholder="Ask for a change: 'add a dark/light toggle', 'make the buttons bigger'…" value={change} onChange={(e) => setChange(e.target.value)} />
          <button className="btn-primary" disabled={a.busy || !change.trim()}><Wand2 size={14} /> Change</button>
        </form>
      )}
      {a.notes && <details className="text-xs text-muted"><summary>History</summary><pre className="mt-1 whitespace-pre-wrap">{a.notes}</pre></details>}
    </section>
  );
}

/** Frames the sandboxed app and serves its window.lifeosStore requests (the sandbox has no storage of its own). */
export function MiniAppFrame({ id, version, className = "" }: { id: number; version: number; className?: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const onMessage = useCallback(async (e: MessageEvent) => {
    if (e.source !== ref.current?.contentWindow) return;
    const d = e.data as { lifeosStore?: boolean; id: number; op: string; key?: string; value?: unknown };
    if (!d?.lifeosStore) return;
    let value: unknown = null, all: Record<string, unknown> | undefined;
    try {
      if (d.op === "all") value = all = await api.miniAppStore(id);
      else if (d.op === "set" && d.key) all = await api.setMiniAppStore(id, d.key, d.value);
      else if (d.op === "remove" && d.key) all = await api.setMiniAppStore(id, d.key, null, true);
    } catch { /* the app keeps its in-memory copy */ }
    ref.current?.contentWindow?.postMessage({ lifeosStoreReply: true, id: d.id, value, all }, "*");
  }, [id]);
  useEffect(() => {
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onMessage]);
  return (
    <iframe ref={ref} title="Mini app" src={api.miniAppUrl(id, version)} className={`w-full bg-[#0b0d12] ${className}`}
      sandbox="allow-scripts allow-forms allow-modals allow-downloads" referrerPolicy="no-referrer" />
  );
}

/** #/miniapp/:id: the app alone, filling its own window. */
export function MiniAppWindow() {
  const id = Number(useParams().id);
  const a = useAsync(() => api.miniApp(id), [id], 5000);
  useEffect(() => { if (a.data) document.title = a.data.name; }, [a.data?.name]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!a.data) return <div className="grid h-screen place-items-center">{a.error ?? <Spinner />}</div>;
  return <MiniAppFrame id={id} version={a.data.version} className="h-screen" />;
}
