import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Clapperboard, ExternalLink, ImageIcon, Loader2, Trash2, Wand2, X } from "lucide-react";
import { api, type MediaItem } from "../lib/api";
import { useToast } from "./ui";

// Media files need the X-LifeOS-Client header, so they're fetched once as blobs and shared as object URLs.
const urls = new Map<number, Promise<string>>();

export function mediaUrl(id: number): Promise<string> {
  let p = urls.get(id);
  if (!p) {
    p = api.mediaBlob(id).then((b) => URL.createObjectURL(b));
    p.catch(() => urls.delete(id));
    urls.set(id, p);
  }
  return p;
}

export function forgetMedia(id: number) {
  urls.get(id)?.then((u) => URL.revokeObjectURL(u)).catch(() => {});
  urls.delete(id);
}

export function useMediaUrl(m?: Pick<MediaItem, "id" | "status"> | null) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    setUrl(null);
    if (!m || m.status !== "done") return;
    let live = true;
    mediaUrl(m.id).then((u) => live && setUrl(u)).catch(() => {});
    return () => { live = false; };
  }, [m?.id, m?.status]);
  return url;
}

const isVideoFile = (m: MediaItem) => /\.(mp4|webm)$/i.test(m.name ?? "");

/** One generated image or clip. Animated WebP clips play as images; mp4/webm use <video>. */
export function MediaView({ m, className = "", controls = false }: { m: MediaItem; className?: string; controls?: boolean }) {
  const url = useMediaUrl(m);
  if (m.status === "failed") {
    return <div className={`flex items-center justify-center bg-danger/10 p-2 text-center text-xs text-danger ${className}`}>Failed</div>;
  }
  if (!url) {
    return (
      <div className={`flex flex-col items-center justify-center gap-1 bg-panel-2 text-xs text-muted ${className}`}>
        <Loader2 size={18} className="animate-spin" />{m.status === "done" ? "" : m.status === "queued" ? "Queued" : "Generating…"}
      </div>
    );
  }
  return isVideoFile(m)
    ? <video src={url} className={`bg-black object-contain ${className}`} controls={controls} autoPlay loop muted playsInline />
    : <img src={url} alt={m.prompt} className={`bg-black/40 object-contain ${className}`} />;
}

export const IMAGE_SIZES = [["square", "Square"], ["portrait", "Portrait"], ["landscape", "Landscape"], ["wide", "Wide"]] as const;

/** Prompt box used by the Gallery page and the Station's easel. */
export function GenerateForm({ onStarted, compact = false, videoOk = true }: {
  onStarted?: (m: MediaItem) => void; compact?: boolean; videoOk?: boolean;
}) {
  const toast = useToast();
  const [kind, setKind] = useState<"image" | "video">("image");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [size, setSize] = useState("square");
  const [seconds, setSeconds] = useState(4);
  const [busy, setBusy] = useState(false);

  const go = async () => {
    if (prompt.trim().length < 3) return;
    setBusy(true);
    try {
      const m = await api.generateMedia({ kind, prompt: prompt.trim(), negative: negative.trim(), size, seconds });
      toast(kind === "video" ? "Video queued. Clips take a few minutes on a local GPU." : "Image queued");
      setPrompt("");
      onStarted?.(m);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-1">
        <button className={kind === "image" ? "btn-primary" : "btn-outline"} onClick={() => setKind("image")}><ImageIcon size={14} /> Image</button>
        <button className={kind === "video" ? "btn-primary" : "btn-outline"} onClick={() => setKind("video")} disabled={!videoOk}
          title={videoOk ? "" : "Video needs ComfyUI with the LTX-Video model"}><Clapperboard size={14} /> Video</button>
      </div>
      <textarea className="input min-h-[72px]" placeholder={kind === "video" ? "A slow pan over a misty forest at sunrise, cinematic" : "A cozy reading nook with warm light, watercolor"}
        value={prompt} onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) go(); e.stopPropagation(); }} />
      {!compact && (
        <input className="input" placeholder="Avoid (optional): text, watermark, extra fingers" value={negative}
          onChange={(e) => setNegative(e.target.value)} onKeyDown={(e) => e.stopPropagation()} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select className="input w-32" value={size} onChange={(e) => setSize(e.target.value)}>
          {IMAGE_SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {kind === "video" && (
          <select className="input w-28" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
            {[2, 3, 4, 5, 6, 8].map((s) => <option key={s} value={s}>{s} s</option>)}
          </select>
        )}
        <button className="btn-primary ml-auto" onClick={go} disabled={busy || prompt.trim().length < 3}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />} Generate
        </button>
      </div>
    </div>
  );
}

const RANGES = [["", "All time"], ["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"]] as const;

function sinceFor(days: string) {
  if (!days) return undefined;
  const d = new Date();
  if (days === "1") d.setHours(0, 0, 0, 0);
  else d.setDate(d.getDate() - Number(days));
  return d.toISOString();
}

/** A browsable album: filter by kind, creator and date; open full size; play clips; step through with ← →. */
export function MediaAlbum({ kind: fixedKind, creators, refreshKey = 0, columns = "grid-cols-2 sm:grid-cols-3 xl:grid-cols-4" }: {
  kind?: "image" | "video"; creators: { slug: string; name: string }[]; refreshKey?: number; columns?: string;
}) {
  const [kind, setKind] = useState<"" | "image" | "video">(fixedKind ?? "");
  const [agent, setAgent] = useState("");
  const [days, setDays] = useState("");
  const [items, setItems] = useState<MediaItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const list = await api.media(kind || undefined, 300, agent || undefined, sinceFor(days));
        if (!live) return;
        setItems(list);
        setError(null);
        const busy = list.some((m) => m.status === "queued" || m.status === "running");
        timer = setTimeout(load, busy ? 2000 : 8000);
      } catch (e) {
        if (live) { setError((e as Error).message); timer = setTimeout(load, 8000); }
      }
    };
    load();
    return () => { live = false; clearTimeout(timer); };
  }, [kind, agent, days, refreshKey]);

  const list = items ?? [];
  const cur = open === null ? null : list[open] ?? null;
  const step = (d: number) => setOpen((i) => (i === null || !list.length ? i : (i + d + list.length) % list.length));

  useEffect(() => {
    if (open === null) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") { step(1); e.stopPropagation(); }
      if (e.key === "ArrowLeft") { step(-1); e.stopPropagation(); }
      if (e.key === "Escape") { setOpen(null); e.stopPropagation(); }
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (m: MediaItem) => {
    await api.deleteMedia(m.id);
    forgetMedia(m.id);
    setItems((x) => (x ?? []).filter((y) => y.id !== m.id));
    setOpen(null);
  };
  const who = (slug: string | null) => (slug ? creators.find((c) => c.slug === slug)?.name ?? slug : "You");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {!fixedKind && (["", "image", "video"] as const).map((k) => (
          <button key={k} className={kind === k ? "btn-primary" : "btn-ghost"} onClick={() => setKind(k)}>{k ? `${k[0].toUpperCase()}${k.slice(1)}s` : "All"}</button>
        ))}
        <select className="input w-40" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Made by">
          <option value="">Everyone</option>
          <option value="you">You</option>
          {creators.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
        </select>
        <select className="input w-36" value={days} onChange={(e) => setDays(e.target.value)} aria-label="When">
          {RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span className="ml-auto text-xs text-muted">{items ? `${list.length} item${list.length === 1 ? "" : "s"}` : ""}</span>
      </div>
      {error && <div className="mb-2 text-sm text-danger">{error}</div>}
      {!items ? <Loader2 size={16} className="animate-spin text-muted" /> : !list.length ? (
        <div className="card py-8 text-center text-sm text-muted">Nothing here yet.</div>
      ) : (
        <div className={`grid gap-3 ${columns}`}>
          {list.map((m, i) => (
            <button key={m.id} onClick={() => setOpen(i)} className="card group overflow-hidden text-left hover:border-accent/60">
              <MediaView m={m} className="aspect-square w-full" />
              <div className="p-2">
                <div className="line-clamp-2 text-xs">{m.prompt}</div>
                <div className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                  {m.kind === "video" ? <Clapperboard size={11} /> : <ImageIcon size={11} />}
                  {who(m.agent)} · {new Date(m.created_at).toLocaleDateString()}{m.seconds ? ` · ${Math.round(m.seconds)}s` : ""}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {cur && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setOpen(null)}>
          <button className="btn-ghost absolute left-2 top-1/2 -translate-y-1/2 text-white" onClick={(e) => { e.stopPropagation(); step(-1); }} aria-label="Previous"><ChevronLeft size={28} /></button>
          <button className="btn-ghost absolute right-2 top-1/2 -translate-y-1/2 text-white" onClick={(e) => { e.stopPropagation(); step(1); }} aria-label="Next"><ChevronRight size={28} /></button>
          <div className="card flex max-h-full w-full max-w-5xl flex-col overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <MediaView m={cur} className="max-h-[60vh] min-h-0 w-full shrink" controls />
            <div className="space-y-1.5 p-3 text-sm">
              <div>{cur.prompt}</div>
              {cur.error && <div className="text-danger">{cur.error}</div>}
              <div className="text-xs text-muted">
                {cur.kind} by {who(cur.agent)} · {new Date(cur.created_at).toLocaleString()} · {cur.model ?? cur.backend}
                {cur.params?.width ? ` · ${cur.params.width}×${cur.params.height}` : ""}{cur.params?.seed ? ` · seed ${cur.params.seed}` : ""}
                {cur.seconds ? ` · took ${cur.seconds}s` : ""}
              </div>
              <div className="flex gap-2 pt-1">
                {cur.status === "done" && <button className="btn-outline" onClick={() => api.openMedia(cur.id)}><ExternalLink size={14} /> Open file</button>}
                <button className="btn-ghost text-danger" onClick={() => remove(cur)}><Trash2 size={14} /> Delete</button>
                <span className="ml-auto self-center text-xs text-muted">{(open ?? 0) + 1} / {list.length} · ← → to browse</span>
                <button className="btn-ghost" onClick={() => setOpen(null)}><X size={14} /> Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
