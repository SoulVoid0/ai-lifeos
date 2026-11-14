import { useState } from "react";
import { Cpu, ImageIcon, RefreshCw, Save } from "lucide-react";
import { api, type MediaSettings } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { GenerateForm, MediaAlbum } from "../components/media";
import { PageHeader, Section, useToast } from "../components/ui";

const BACKENDS: [MediaSettings["backend"], string][] = [
  ["comfyui", "ComfyUI (local: images + video)"],
  ["a1111", "Automatic1111 / Forge (local: images)"],
  ["openai", "OpenAI images (cloud, uses your budget)"],
  ["off", "Off"],
];

export default function Gallery() {
  const toast = useToast();
  const status = useAsync(() => api.mediaStatus(), []);
  const crew = useAsync(() => api.agents(), []);
  const [refresh, setRefresh] = useState(0);
  const [showSettings, setShowSettings] = useState(false);
  const [starting, setStarting] = useState(false);
  const startComfy = async () => {
    setStarting(true);
    try {
      const r = await api.startService("comfyui");
      toast(r.ok ? "ComfyUI is running" : r.comfyui.note || "ComfyUI didn't start", r.ok ? "ok" : "error");
      status.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setStarting(false);
    }
  };
  const creators = (crew.data ?? []).filter((a) => a.tools.some((t) => t.startsWith("generate_")) || ["creative", "photo", "video"].includes(a.slug));

  const st = status.data;
  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title="Gallery" subtitle="Images and short clips made by you, Lumen (photos) and Reel (video), generated locally on your GPU by default. Jobs run one at a time."
        actions={<>
          <button className="btn-ghost" onClick={() => status.reload()} title="Check the backend again"><RefreshCw size={14} /></button>
          <button className="btn-outline" onClick={() => setShowSettings((s) => !s)}><Cpu size={14} /> Backend</button>
        </>} />
      {st && (
        <div className={`mb-4 rounded-lg border p-3 text-sm ${st.ok ? "border-ok/30 bg-ok/5" : "border-warn/40 bg-warn/10"}`}>
          <span className={st.ok ? "text-ok" : "text-warn"}>{st.ok ? "Ready" : "Not ready"}</span> · {st.detail}
          {st.ok && st.backend === "comfyui" && !st.video && <span className="text-muted"> · add the LTX-Video model for clips</span>}
          {!st.ok && st.backend === "comfyui" && st.launcher && (
            <button className="btn-ghost ml-2 px-2 py-0.5 text-xs text-accent" onClick={startComfy} disabled={starting}>
              {starting ? "Starting ComfyUI… (first start can take a few minutes)" : "Start it now"}
            </button>
          )}
        </div>
      )}
      {showSettings && st && <MediaSettingsCard s={st.settings} checkpoints={st.checkpoints} onSaved={() => { status.reload(); toast("Media settings saved"); }} />}

      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <Section title="Create" icon={<ImageIcon size={15} className="text-accent" />} className="self-start">
          <GenerateForm videoOk={!st || st.video} onStarted={() => setRefresh((r) => r + 1)} />
          <p className="mt-3 text-xs text-muted">Tip: give a task to Lumen or Reel on the Station (“make a poster for…”), or ask Muse for a whole series. Results land here too.</p>
        </Section>
        <MediaAlbum creators={creators} refreshKey={refresh} />
      </div>
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
          {field("comfy_launcher", "ComfyUI launcher (LifeOS starts it hidden when needed)", "A:\\comfyui-rocm\\comfyui-user.bat (found automatically if empty)")}
          <label className="mt-5 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={v.comfy_autostart} onChange={(e) => set("comfy_autostart", e.target.checked)} />
            Start ComfyUI with LifeOS (instead of on the first generation)
          </label>
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
