// Story mode: an interactive story narrated by a crew member, with a ComfyUI picture for every scene.
import { useEffect, useRef, useState } from "react";
import { BookOpenText, Download, ImageIcon, Loader2, RefreshCw, Sparkles, Trash2, Volume2 } from "lucide-react";
import { api, type Story, type StoryScene } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { play } from "../lib/sfx";
import { mediaUrl } from "../components/media";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

const STYLE_LABELS: Record<string, string> = { "": "Any", storybook: "Storybook", anime: "Anime", noir: "Film noir", scifi: "Sci-fi", fantasy: "Fantasy", pixel: "Pixel art", comic: "Comic" };
const PREMISES = ["A lighthouse keeper finds a message in a bottle addressed to them, dated 50 years from now",
  "The last botanist on a generation ship discovers the garden is growing something new",
  "A detective cat in a city of talking animals investigates a missing moon"];

export default function Stories() {
  const toast = useToast();
  const list = useAsync(api.stories, [], 6000);
  const agents = useAsync(api.agents, []);
  const [sel, setSel] = useState<number | null>(null);
  const [form, setForm] = useState({ premise: "", style: "storybook", narrator: "creative", pictures: true });
  const start = async () => {
    try {
      const s = await api.startStory(form.premise.trim(), form.style, form.narrator, form.pictures);
      play("start");
      setSel(s.id);
      setForm((f) => ({ ...f, premise: "" }));
      list.reload();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  if (sel) return <Reader id={sel} onBack={() => { setSel(null); list.reload(); }} />;
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <PageHeader title="Stories" subtitle="Choose-your-path stories told by the crew, with a picture for every scene made on your GPU. Pick a choice or write your own." />
      <Section title="New story" icon={<Sparkles size={15} className="text-accent" />}>
        <textarea className="input min-h-20" placeholder="The premise: who, where, what's wrong" value={form.premise} onChange={(e) => setForm({ ...form, premise: e.target.value })} />
        <div className="mt-2 flex flex-wrap gap-1.5">{PREMISES.map((p) => <button key={p} className="chip hover:text-text" onClick={() => setForm({ ...form, premise: p })}>{p.slice(0, 48)}…</button>)}</div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className="text-xs text-muted">Art style
            <select className="input mt-1" value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })}>
              {(list.data?.styles ?? Object.keys(STYLE_LABELS)).map((s) => <option key={s} value={s}>{STYLE_LABELS[s] ?? s}</option>)}
            </select></label>
          <label className="text-xs text-muted">Narrator
            <select className="input mt-1" value={form.narrator} onChange={(e) => setForm({ ...form, narrator: e.target.value })}>
              {(agents.data ?? []).filter((a) => a.enabled).map((a) => <option key={a.slug} value={a.slug}>{a.name} ({a.role})</option>)}
            </select></label>
          <label className="mt-5 flex items-center gap-2 text-sm"><input type="checkbox" checked={form.pictures} onChange={(e) => setForm({ ...form, pictures: e.target.checked })} /> Pictures (ComfyUI)</label>
        </div>
        <div className="mt-3 flex justify-end"><button className="btn-primary" disabled={form.premise.trim().length < 5} onClick={start}><BookOpenText size={14} /> Begin</button></div>
      </Section>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(list.data?.stories ?? []).length === 0 && <Empty>No stories yet.</Empty>}
        {(list.data?.stories ?? []).map((s) => (
          <button key={s.id} className="card overflow-hidden text-left hover:border-accent/50" onClick={() => setSel(s.id)}>
            <Cover id={s.cover_media_id ?? null} />
            <div className="p-3">
              <div className="font-medium">{s.title || "Untitled"}</div>
              <div className="line-clamp-2 text-xs text-muted">{s.premise}</div>
              <div className="mt-1 text-[11px] text-muted">{s.scene_count} scene{s.scene_count === 1 ? "" : "s"} · {s.status === "ended" ? "finished" : s.status} · {ago(s.created_at)}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function Cover({ id }: { id: number | null }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => { if (id) mediaUrl(id).then(setUrl).catch(() => setUrl(null)); }, [id]);
  return <div className="aspect-[3/2] bg-panel-2">{url ? <img src={url} alt="" className="h-full w-full object-cover" /> : <div className="grid h-full place-items-center text-muted"><ImageIcon size={22} /></div>}</div>;
}

function ScenePicture({ sc }: { sc: StoryScene }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    setUrl(null);
    if (sc.media_id && sc.image_status === "done") mediaUrl(sc.media_id).then(setUrl).catch(() => {});
  }, [sc.media_id, sc.image_status]);
  if (!sc.media_id) return null;
  if (url) return <img src={url} alt={sc.image_prompt} className="w-full rounded-lg border border-line" />;
  return (
    <div className="grid aspect-[3/2] w-full place-items-center rounded-lg border border-dashed border-line text-sm text-muted">
      {sc.image_status === "failed" ? <span className="px-4 text-center">Picture failed: {sc.image_error}</span> : <span className="flex items-center gap-2"><Loader2 size={15} className="animate-spin" /> Painting the scene…</span>}
    </div>
  );
}

function Reader({ id, onBack }: { id: number; onBack: () => void }) {
  const toast = useToast();
  const s = useAsync(() => api.story(id), [id], 2500);
  const [own, setOwn] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const story: Story | undefined = s.data;
  const count = story?.scenes?.length ?? 0;
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [count, story?.status]);
  const choose = async (c: string) => {
    try { s.setData(await api.chooseStory(id, c)); setOwn(""); play("open"); } catch (e) { toast((e as Error).message, "error"); }
  };
  const download = async () => {
    const res = await fetch(api.storyMarkdownUrl(id), { headers: { "X-LifeOS-Client": "1" } });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `${story?.title || "story"}.md`; a.click();
    URL.revokeObjectURL(url);
  };
  if (!story) return <div className="p-8"><Spinner /></div>;
  const last = story.scenes?.[story.scenes.length - 1];
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="flex items-center gap-2">
        <button className="btn-ghost" onClick={onBack}>← Stories</button>
        <h1 className="flex-1 text-xl font-semibold">{story.title || "…"}</h1>
        <button className="btn-ghost" title="Download as Markdown" onClick={download}><Download size={14} /></button>
        <button className="btn-ghost" title="Delete" onClick={async () => { await api.deleteStory(id); onBack(); }}><Trash2 size={14} /></button>
      </div>
      <p className="text-sm italic text-muted">{story.premise}</p>
      {(story.scenes ?? []).map((sc) => (
        <article key={sc.id} className="space-y-3">
          <ScenePicture sc={sc} />
          <div className="flex items-start gap-2">
            <p className="flex-1 whitespace-pre-wrap text-[15px] leading-relaxed">{sc.text}</p>
            <div className="flex flex-col">
              <button className="btn-ghost" title="Read aloud" onClick={() => api.say(sc.text, story.narrator).catch(() => {})}><Volume2 size={14} /></button>
              <button className="btn-ghost" title="New picture" onClick={async () => { try { await api.redrawScene(sc.id); s.reload(); } catch (e) { toast((e as Error).message, "error"); } }}><RefreshCw size={14} /></button>
            </div>
          </div>
          {sc.chosen && <div className="rounded-lg border-l-2 border-accent bg-accent-soft px-3 py-2 text-sm">You chose: {sc.chosen}</div>}
        </article>
      ))}
      {story.status === "writing" && <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> The narrator is writing…</div>}
      {story.status === "failed" && (
        <div className="flex items-center gap-2 text-sm text-danger">{story.error}
          <button className="btn-outline" onClick={async () => s.setData(await api.retryStory(id))}>Try again</button></div>
      )}
      {story.status === "ended" && <div className="py-4 text-center text-lg italic text-muted">The end.</div>}
      {story.status === "ready" && last && (
        <div className="space-y-2">
          {last.choices.map((c) => <button key={c} className="btn-outline w-full justify-start text-left" onClick={() => choose(c)}>{c}</button>)}
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (own.trim()) choose(own.trim()); }}>
            <input className="input flex-1" placeholder="…or write what you do" value={own} onChange={(e) => setOwn(e.target.value)} />
            <button className="btn-primary" disabled={!own.trim()}>Go</button>
          </form>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
