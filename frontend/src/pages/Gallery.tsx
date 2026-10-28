import { useEffect, useState } from "react";
import { Clapperboard, Cpu, ExternalLink, ImageIcon, RefreshCw, Save, Trash2, X } from "lucide-react";
import { api, type MediaItem, type MediaSettings } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { GenerateForm, MediaView, forgetMedia } from "../components/media";
import { Empty, ErrorBox, PageHeader, Section, Spinner, useToast } from "../components/ui";

const BACKENDS: [MediaSettings["backend"], string][] = [
  ["comfyui", "ComfyUI (local: images + video)"],
  ["a1111", "Automatic1111 / Forge (local: images)"],
  ["openai", "OpenAI images (cloud, uses your budget)"],
  ["off", "Off"],
];

export default function Gallery() {
  const toast = useToast();
  const [kind, setKind] = useState<"" | "image" | "video">("");
  const list = useAsync(() => api.media(kind || undefined, 120), [kind]);
  const status = useAsync(() => api.mediaStatus(), []);
  const [open, setOpen] = useState<MediaItem | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const busy = (list.data ?? []).some((m) => m.status === "queued" || m.status === "running");

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(list.reload, 2000);
    return () => clearInterval(t);
  }, [busy, list.reload]);

  const remove = async (m: MediaItem) => {
    await api.deleteMedia(m.id);
    forgetMedia(m.id);
    setOpen(null);
    list.reload();
  };

  const st = status.data;
  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Gallery" subtitle="Images and short clips made by you and Muse, generated locally on your GPU by default."
        actions={<>
          <button className="btn-ghost" onClick={() => status.reload()} title="Check the backend again"><RefreshCw size={14} /></button>
          <button className="btn-outline" onClick={() => setShowSettings((s) => !s)}><Cpu size={14} /> Backend</button>
        </>} />
      <ErrorBox error={list.error} />
      {st && (
        <div className={`mb-4 rounded-lg border p-3 text-sm ${st.ok ? "border-ok/30 bg-ok/5" : "border-warn/40 bg-warn/10"}`}>
          <span className={st.ok ? "text-ok" : "text-warn"}>{st.ok ? "Ready" : "Not ready"}</span> · {st.detail}
          {st.ok && st.backend === "comfyui" && !st.video && <span className="text-muted"> · add the LTX-Video model for clips</span>}
          {!st.ok && st.backend === "comfyui" && <span className="text-muted"> · start ComfyUI (comfyui-user.bat) and press refresh</span>}
        </div>
      )}
      {showSettings && st && <MediaSettingsCard s={st.settings} checkpoints={st.checkpoints} onSaved={() => { status.reload(); toast("Media settings saved"); }} />}

      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <Section title="Create" icon={<ImageIcon size={15} className="text-accent" />}>
          <GenerateForm videoOk={!st || st.video} onStarted={() => list.reload()} />
          <p className="mt-3 text-xs text-muted">Tip: ask Muse in the Control Center or the Station (“make a poster for…”), and the result lands here too.</p>
        </Section>
        <div>
          <div className="mb-3 flex gap-1">
            {(["", "image", "video"] as const).map((k) => (
              <button key={k} className={kind === k ? "btn-primary" : "btn-ghost"} onClick={() => setKind(k)}>{k ? `${k[0].toUpperCase()}${k.slice(1)}s` : "All"}</button>
            ))}
          </div>
          {list.loading && !list.data ? <Spinner /> : !(list.data ?? []).length ? (
            <div className="card"><Empty>Nothing generated yet.</Empty></div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
              {(list.data ?? []).map((m) => (
                <button key={m.id} onClick={() => setOpen(m)} className="card group overflow-hidden text-left hover:border-accent/60">
                  <MediaView m={m} className="aspect-square w-full" />
                  <div className="p-2">
                    <div className="line-clamp-2 text-xs">{m.prompt}</div>
                    <div className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                      {m.kind === "video" ? <Clapperboard size={11} /> : <ImageIcon size={11} />}
                      {m.agent ? `${m.agent} · ` : ""}{ago(m.created_at)}{m.seconds ? ` · ${Math.round(m.seconds)}s` : ""}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {open && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6" onClick={() => setOpen(null)}>
          <div className="card flex max-h-full w-full max-w-4xl flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <MediaView m={open} className="max-h-[70vh] w-full" controls />
            <div className="space-y-2 p-4 text-sm">
              <div>{open.prompt}</div>
              {open.error && <div className="text-danger">{open.error}</div>}
              <div className="text-xs text-muted">
                {open.kind} · {open.model ?? open.backend} · {open.params?.width}×{open.params?.height} · seed {open.params?.seed}
                {open.seconds ? ` · ${open.seconds}s` : ""}{open.agent ? ` · by ${open.agent}` : ""} · {ago(open.created_at)}
              </div>
              {open.path && <div className="truncate font-mono text-xs text-muted">{open.path}</div>}
              <div className="flex gap-2 pt-1">
                {open.status === "done" && <button className="btn-outline" onClick={() => api.openMedia(open.id)}><ExternalLink size={14} /> Open file</button>}
                <button className="btn-ghost text-danger" onClick={() => remove(open)}><Trash2 size={14} /> Delete</button>
                <button className="btn-ghost ml-auto" onClick={() => setOpen(null)}><X size={14} /> Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MediaSettingsCard({ s, checkpoints, onSaved }: { s: MediaSettings; checkpoints: string[]; onSaved: () => void }) {
  const toast = useToast();
  const [v, setV] = useState<MediaSettings>(s);
  const set = <K extends keyof MediaSettings>(k: K, val: MediaSettings[K]) => setV((x) => ({ ...x, [k]: val }));
  const save = async () => {
    try {
      await api.setMediaSettings(v);
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const field = (k: keyof MediaSettings, label: string, hint?: string) => (
    <label className="block text-xs text-muted">{label}
      <input className="input mt-1" value={String(v[k])} placeholder={hint} onChange={(e) => set(k, e.target.value as never)} />
    </label>
  );
  return (
    <Section title="Generation backend" icon={<Cpu size={15} className="text-accent" />} className="mb-4"
      actions={<button className="btn-primary" onClick={save}><Save size={14} /> Save</button>}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block text-xs text-muted">Backend
          <select className="input mt-1" value={v.backend} onChange={(e) => set("backend", e.target.value as MediaSettings["backend"])}>
            {BACKENDS.map(([id, l]) => <option key={id} value={id}>{l}</option>)}
          </select>
        </label>
        {v.backend === "a1111" ? field("a1111_url", "WebUI URL") : field("comfy_url", "ComfyUI URL")}
        <label className="block text-xs text-muted">Image model
          <select className="input mt-1" value={v.image_checkpoint} onChange={(e) => set("image_checkpoint", e.target.value)}>
            <option value="">Auto (first image checkpoint)</option>
            {[...new Set([...checkpoints, v.image_checkpoint].filter(Boolean))].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        {field("output_folder", "Save to folder (must be an approved folder)", "Default: LifeOS data folder\\media")}
        {v.backend === "comfyui" && <>
          {field("video_checkpoint", "Video model (LTX-Video checkpoint)")}
          {field("video_text_encoder", "Video text encoder (in models/text_encoders)")}
          {field("image_workflow", "Custom image workflow (API JSON, optional)")}
          {field("video_workflow", "Custom video workflow (API JSON, optional)")}
        </>}
        {v.backend === "openai" && <>
          {field("cloud_image_model", "OpenAI image model")}
          <label className="block text-xs text-muted">Price per image (USD, counted against the daily cap)
            <input className="input mt-1" type="number" step="0.01" min={0} value={v.cloud_price_usd} onChange={(e) => set("cloud_price_usd", Number(e.target.value))} />
          </label>
        </>}
        <label className="flex items-center gap-2 text-sm md:col-span-2">
          <input type="checkbox" checked={v.free_vram} onChange={(e) => set("free_vram", e.target.checked)} />
          Unload Ollama models before generating (recommended when both share one GPU)
        </label>
      </div>
    </Section>
  );
}
