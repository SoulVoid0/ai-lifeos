// Settings → Local AI: Low-resource mode (one click) plus the savers it turns on, and in-app model downloads.
import { useState } from "react";
import { Download, Feather, RotateCw, Trash2 } from "lucide-react";
import { api, type ModelPull, type Settings as S } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { useToast } from "./ui";

type Props = { form: S; save: (patch: Partial<S>) => Promise<void>; reload: () => void };
const gb = (n: number) => `${(n / 1e9).toFixed(1)} GB`;
const pct = (p: ModelPull) => (p.total ? Math.round((100 * p.completed) / p.total) : 0);

export function LowResource({ form, save, reload }: Props) {
  const toast = useToast();
  const lean = useAsync(api.lean, [], 3000);
  const models = useAsync(api.installedModels, [], 5000);
  const [busy, setBusy] = useState(false);
  const [pullName, setPullName] = useState("");
  const st = lean.data;
  const pulling = (models.data?.pulls ?? []).filter((p) => p.state === "pulling");

  const turnOn = async () => {
    setBusy(true);
    try {
      const r = await api.applyLean(true);
      reload();
      lean.reload();
      toast(["Low-resource mode is on.", r.downloading?.length ? `Downloading ${r.downloading.join(", ")} in the background.` : "",
        r.gpu_hint ?? ""].filter(Boolean).join("\n"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const restart = async () => {
    try {
      const r = await api.applyTuning();
      lean.reload();
      toast(r.restarted ? "Ollama restarted with the new settings" : r.note ?? "Saved", r.restarted ? "ok" : "info");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const pull = async (m: string) => {
    if (!m.trim()) return;
    try {
      await api.pullModel(m.trim());
      setPullName("");
      models.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const remove = async (m: string) => {
    try {
      await api.removeModel(m);
      models.reload();
      lean.reload();
      toast(`Removed ${m}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const check = (k: "ollama_think" | "ollama_tuning" | "ollama_flash_attention" | "stay_on_loaded_model", label: string, invert = false) => (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" className="accent-[var(--color-accent)]" checked={invert ? !form[k] : !!form[k]}
        onChange={(e) => save({ [k]: invert ? !e.target.checked : e.target.checked } as Partial<S>)} />
      {label}
    </label>
  );

  return (
    <div className="mt-4 rounded-lg border border-accent/40 bg-accent/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium"><Feather size={15} className="text-accent" /> Low-resource mode
          {st?.on && <span className="rounded bg-ok/15 px-1.5 py-0.5 text-[11px] text-ok">on</span>}
        </div>
        <button className="btn-primary" onClick={turnOn} disabled={busy}>{st?.on ? "Re-apply" : "Turn on"}</button>
      </div>
      <p className="mt-1 text-xs text-muted">
        Agents skip the hidden thinking pass, share one main model instead of swapping several in and out, Forge uses a
        30B coder that fits fully in a 16 GB GPU (no CPU spill), and chats and the Spark micro agent use a small fast model
        that stays loaded beside the main one. Ollama gets a compressed context cache (half the VRAM) and one request slot
        so each step reuses the cached prompt. Missing models download here, no terminal needed.
      </p>

      {st && (
        <div className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
          {st.plan.map((p) => (
            <div key={p.slug} className="flex items-center justify-between gap-2 rounded border border-line px-2 py-1">
              <span>{p.agent}</span>
              <span className={`truncate font-mono ${p.installed ? "text-muted" : "text-warn"}`} title={p.model}>
                {p.model.replace("hf.co/unsloth/", "")}{p.installed ? "" : " (not downloaded)"}
              </span>
            </div>
          ))}
        </div>
      )}

      {pulling.map((p) => (
        <div key={p.model} className="mt-2 text-xs">
          <div className="flex justify-between"><span className="truncate font-mono">{p.model}</span><span>{p.status} {pct(p)}%</span></div>
          <div className="mt-1 h-1.5 rounded bg-line"><div className="h-1.5 rounded bg-accent" style={{ width: `${pct(p)}%` }} /></div>
        </div>
      ))}
      {(models.data?.pulls ?? []).filter((p) => p.state === "failed").map((p) => (
        <div key={p.model} className="mt-2 flex items-center gap-2 text-xs text-danger">
          <span className="flex-1">Download of {p.model} failed: {p.error}</span>
          <button className="btn-ghost py-0.5 text-xs" onClick={() => pull(p.model)}><RotateCw size={12} /> Retry</button>
        </div>
      ))}

      <div className="mt-3 space-y-1.5">
        {check("ollama_think", "Skip the thinking pass of thinking models (qwen3): far fewer tokens per step", true)}
        {check("stay_on_loaded_model", "Overseer reviews and reports on the model that's already loaded (no swap)")}
        {check("ollama_tuning", "Tune Ollama: one request slot, at most two models in memory")}
        {check("ollama_flash_attention", "Flash attention (needed for the compressed context cache)")}
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label">Context cache</label>
          <select className="input" value={form.ollama_kv_cache ?? "q8_0"} onChange={(e) => save({ ollama_kv_cache: e.target.value as S["ollama_kv_cache"] })}>
            <option value="q8_0">q8_0: half the VRAM, same quality (recommended)</option>
            <option value="q4_0">q4_0: a quarter, slightly worse answers</option>
            <option value="f16">f16: Ollama's default</option>
          </select>
        </div>
        <div>
          <label className="label">Fast model (chats, Spark; empty = off)</label>
          <input className="input" list="models" key={form.fast_model} defaultValue={form.fast_model ?? ""} placeholder="qwen3:4b-instruct-2507-q4_K_M"
            onBlur={(e) => e.target.value.trim() !== (form.fast_model ?? "") && save({ fast_model: e.target.value.trim() })} />
        </div>
      </div>
      {st?.tuning && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>Context cache in use: {st.tuning.kv_cache}{st.tuning.flash_attention_refused ? " (this GPU/driver refused flash attention)" : ""}.</span>
          {st.tuning.pending_restart && <>
            <span className="text-warn">Ollama needs a restart to use the new settings.</span>
            <button className="btn-ghost py-0.5 text-xs" onClick={restart}><RotateCw size={12} /> Restart Ollama</button>
          </>}
        </div>
      )}

      <div className="mt-3">
        <div className="label">Downloaded models</div>
        <div className="mt-1 space-y-1">
          {(models.data?.models ?? []).map((m) => (
            <div key={m.name} className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate font-mono" title={m.name}>{m.name}</span>
              <span className="text-muted">{gb(m.size)}</span>
              {m.in_use ? <span className="w-16 text-right text-muted">in use</span> : (
                <button className="btn-ghost w-16 justify-end px-1 text-xs" onClick={() => remove(m.name)}
                  title={st?.removable.includes(m.name) ? "Low-resource mode doesn't need this one" : "Free the disk space"}>
                  <Trash2 size={12} /> Remove
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <input className="input flex-1 text-xs" value={pullName} placeholder="Download a model, e.g. qwen3:14b or hf.co/user/repo:Q4_K_M"
            onChange={(e) => setPullName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pull(pullName)} />
          <button className="btn-ghost" onClick={() => pull(pullName)} disabled={!pullName.trim()}><Download size={14} /> Download</button>
        </div>
      </div>
    </div>
  );
}
