// Settings → Local AI: keeping the computer usable while a model bigger than the GPU runs.
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, type Settings as S } from "../lib/api";
import { useAsync } from "../lib/hooks";

type Props = { form: S; setForm: (s: S) => void; save: (patch: Partial<S>) => Promise<void> };
type Opts = { num_ctx?: number; num_gpu?: number; num_thread?: number };
const gb = (n: number) => `${(n / 1024 ** 3).toFixed(1)} GB`;

export function GpuSettings({ form, setForm, save }: Props) {
  const models = useAsync(api.llmStatus, []);
  const gpu = useAsync(api.gpu, [], 5000);
  const per = (form.ollama_model_options ?? {}) as Record<string, Opts>;
  const [adding, setAdding] = useState("");
  const setOpt = (model: string, key: keyof Opts, raw: string) => {
    const next: Record<string, Opts> = { ...per, [model]: { ...per[model] } };
    if (raw === "") delete next[model][key];
    else next[model][key] = Math.max(0, Math.round(Number(raw) || 0));
    setForm({ ...form, ollama_model_options: next });
  };
  const commit = () => save({ ollama_model_options: per });
  const remove = (model: string) => {
    const next = { ...per };
    delete next[model];
    save({ ollama_model_options: next });
  };
  const add = () => {
    if (!adding || per[adding]) return;
    save({ ollama_model_options: { ...per, [adding]: {} } });
    setAdding("");
  };
  const num = (k: "ollama_gpu_gb" | "ollama_cpu_threads", step: number) => (
    <input className="input" type="number" min={0} step={step} value={form[k] ?? 0}
      onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })}
      onBlur={() => save({ [k]: Math.max(0, Number(form[k]) || 0) } as Partial<S>)} />
  );
  const check = (k: "ollama_low_priority" | "ollama_unload_on_switch" | "ollama_hard_stop", label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" className="accent-[var(--color-accent)]" checked={form[k] ?? true} onChange={(e) => save({ [k]: e.target.checked } as Partial<S>)} />
      {label}
    </label>
  );

  return (
    <div className="mt-4 rounded-lg border border-line p-3">
      <div className="text-sm font-medium">Keep the computer responsive</div>
      <p className="mt-0.5 text-xs text-muted">
        A model bigger than your GPU (like a 30B on 16 GB) spills onto the CPU and RAM and can make the whole PC lag.
        Leave room for Windows: give models a bit less than your total VRAM (about 13.5 on a 16 GB card) and keep a
        couple of CPU cores free.
        {gpu.data && <> Right now: {gpu.data.models.length ? gpu.data.models.map((m) => `${m.name} ${gb(m.vram)} on GPU of ${gb(m.size)}`).join(", ") : "no model loaded"}.</>}
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div><label className="label">GPU memory for models (GB, 0 = let Ollama fill it)</label>{num("ollama_gpu_gb", 0.5)}</div>
        <div><label className="label">CPU threads for models (0 = all but 2 cores)</label>{num("ollama_cpu_threads", 1)}</div>
      </div>
      <div className="mt-2 space-y-1.5">
        {check("ollama_low_priority", "Run Ollama at low CPU priority so Windows comes first")}
        {check("ollama_unload_on_switch", "Unload the previous model when agents switch to another one")}
        {check("ollama_hard_stop", "If Ollama keeps working after Stop, restart it")}
      </div>
      <div className="mt-3">
        <div className="label">Per model (empty = automatic; 30B and larger default to an 8192 context)</div>
        {Object.keys(per).length > 0 && (
          <div className="mt-1 space-y-1.5">
            <div className="grid grid-cols-[1fr_6rem_6rem_6rem_2rem] gap-2 text-[11px] text-muted">
              <span>Model</span><span>Context</span><span>GPU layers</span><span>Threads</span><span />
            </div>
            {Object.entries(per).map(([m, o]) => (
              <div key={m} className="grid grid-cols-[1fr_6rem_6rem_6rem_2rem] items-center gap-2">
                <span className="truncate font-mono text-xs">{m}</span>
                {(["num_ctx", "num_gpu", "num_thread"] as const).map((k) => (
                  <input key={k} className="input py-1 text-xs" type="number" min={0} placeholder="auto" value={o[k] ?? ""}
                    onChange={(e) => setOpt(m, k, e.target.value)} onBlur={commit} />
                ))}
                <button className="btn-ghost px-1" onClick={() => remove(m)} aria-label={`Remove ${m}`}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        )}
        <div className="mt-2 flex gap-2">
          <select className="input flex-1 text-xs" value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Add a model…</option>
            {(models.data?.models ?? []).filter((m) => !per[m]).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <button className="btn-ghost" onClick={add} disabled={!adding}><Plus size={14} /> Add</button>
        </div>
      </div>
    </div>
  );
}
