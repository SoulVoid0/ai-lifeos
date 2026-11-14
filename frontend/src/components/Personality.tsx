import { useEffect, useState } from "react";
import { Play, Save, Sparkles } from "lucide-react";
import { api, type Agent } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Spinner, notifyChanged, useToast } from "./ui";

export type PersonalityValue = Pick<Agent, "voice" | "quirks" | "catchphrases" | "idle_lines" | "tts_voice" | "tts_rate">;

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/** Voice, quirks, catchphrases and Station idle lines. Shapes chat and speech bubbles, not task output. */
export function PersonalityFields({ value, onChange, slug }: { value: PersonalityValue; onChange: (v: PersonalityValue) => void; slug?: string }) {
  const [phrases, setPhrases] = useState((value.catchphrases ?? []).join("\n"));
  const [idle, setIdle] = useState((value.idle_lines ?? []).join("\n"));
  const presets = useAsync(api.presets, []);
  const voice = useAsync(api.voice, []);
  const sys = useAsync(api.systemVoices, []);
  const stop = (e: React.KeyboardEvent) => e.stopPropagation();
  const applyPreset = (id: string) => {
    const p = presets.data?.find((x) => x.id === id);
    if (!p) return;
    setPhrases(p.catchphrases.join("\n"));
    setIdle(p.idle_lines.join("\n"));
    onChange({ ...value, voice: p.voice, quirks: p.quirks, catchphrases: p.catchphrases, idle_lines: p.idle_lines, tts_voice: p.tts_voice, tts_rate: p.tts_rate });
  };
  const fallback = slug ? voice.data?.agent_voices?.[slug] : undefined;
  const preview = () => {
    const line = value.catchphrases?.[0] || "Hello. This is how I sound.";
    api.say(line, slug ?? null, value.tts_voice || fallback?.voice || undefined, value.tts_rate || fallback?.rate || undefined).catch(() => {});
  };
  const catalog = voice.data?.catalog ?? [];
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2"><label className="label">Start from a personality</label>
        <select className="input" value="" onChange={(e) => applyPreset(e.target.value)}>
          <option value="">Pick one to fill everything below…</option>
          {(presets.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select></div>
      <div><label className="label">Spoken voice</label>
        <select className="input" value={value.tts_voice ?? ""} onChange={(e) => onChange({ ...value, tts_voice: e.target.value })}>
          <option value="">Default{fallback ? ` (${fallback.voice})` : ""}</option>
          <optgroup label="Piper (local neural voices)">
            {catalog.map((v) => <option key={v.id} value={`piper:${v.id}`}>{v.label}{v.installed ? "" : " (downloads on first use)"}</option>)}
          </optgroup>
          {(sys.data ?? []).length > 0 && <optgroup label="Built into this computer">
            {(sys.data ?? []).map((v) => <option key={v} value={`system:${v}`}>{v}</option>)}
          </optgroup>}
        </select></div>
      <div><label className="label">Speaking speed ({(value.tts_rate || fallback?.rate || 1).toFixed(2)}x)</label>
        <div className="flex items-center gap-2">
          <input type="range" min={0.5} max={2} step={0.05} className="flex-1" value={value.tts_rate || fallback?.rate || 1}
            onChange={(e) => onChange({ ...value, tts_rate: Number(e.target.value) })} />
          <button type="button" className="btn-ghost" title="Hear it" onClick={preview}><Play size={14} /></button>
        </div></div>
      <div className="sm:col-span-2"><label className="label">Voice and tone</label>
        <input className="input" value={value.voice ?? ""} placeholder="Calm ship's captain: measured, warm, decisive" onKeyDown={stop}
          onChange={(e) => onChange({ ...value, voice: e.target.value })} /></div>
      <div className="sm:col-span-2"><label className="label">Quirks</label>
        <textarea className="input min-h-14" value={value.quirks ?? ""} placeholder="Makes time puns. Hates vague deadlines." onKeyDown={stop}
          onChange={(e) => onChange({ ...value, quirks: e.target.value })} /></div>
      <div><label className="label">Catchphrases (one per line)</label>
        <textarea className="input min-h-20" value={phrases} onKeyDown={stop}
          onChange={(e) => { setPhrases(e.target.value); onChange({ ...value, catchphrases: lines(e.target.value) }); }} /></div>
      <div><label className="label">Station idle lines (one per line)</label>
        <textarea className="input min-h-20" value={idle} onKeyDown={stop}
          onChange={(e) => { setIdle(e.target.value); onChange({ ...value, idle_lines: lines(e.target.value) }); }} /></div>
      <p className="text-[11px] text-muted sm:col-span-2"><Sparkles size={11} className="mr-1 inline" />Personality shapes chat and speech bubbles. Task results stay plain and professional.</p>
    </div>
  );
}

/** Personality editor with its own Save button (used in the Station's agent panel). */
export function PersonalityEditor({ agent }: { agent: Agent }) {
  const toast = useToast();
  const pick = (a: Agent): PersonalityValue => ({ voice: a.voice, quirks: a.quirks, catchphrases: a.catchphrases, idle_lines: a.idle_lines, tts_voice: a.tts_voice ?? "", tts_rate: a.tts_rate ?? 0 });
  const [v, setV] = useState<PersonalityValue>(pick(agent));
  const [key, setKey] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setV(pick(agent)); setKey((k) => k + 1); }, [agent.slug]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    setBusy(true);
    try {
      await api.updateAgent(agent.slug, v);
      toast(`Saved ${agent.name}'s personality`);
      notifyChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      <PersonalityFields key={key} value={v} onChange={setV} slug={agent.slug} />
      <button className="btn-primary" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save size={14} />} Save personality</button>
    </div>
  );
}
