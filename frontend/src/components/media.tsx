import { useEffect, useState } from "react";
import { Clapperboard, ImageIcon, Loader2, Wand2 } from "lucide-react";
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
