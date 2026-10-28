import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { api, type Agent } from "../lib/api";
import { Spinner, notifyChanged, useToast } from "./ui";

export type PersonalityValue = Pick<Agent, "voice" | "quirks" | "catchphrases" | "idle_lines">;

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/** Voice, quirks, catchphrases and Station idle lines. Shapes chat and speech bubbles, not task output. */
export function PersonalityFields({ value, onChange }: { value: PersonalityValue; onChange: (v: PersonalityValue) => void }) {
  const [phrases, setPhrases] = useState((value.catchphrases ?? []).join("\n"));
  const [idle, setIdle] = useState((value.idle_lines ?? []).join("\n"));
  const stop = (e: React.KeyboardEvent) => e.stopPropagation();
  return (
    <div className="grid gap-3 sm:grid-cols-2">
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
      <p className="text-[11px] text-muted sm:col-span-2">Personality shapes chat and speech bubbles. Task results stay plain and professional.</p>
    </div>
  );
}

/** Personality editor with its own Save button (used in the Station's agent panel). */
export function PersonalityEditor({ agent }: { agent: Agent }) {
  const toast = useToast();
  const pick = (a: Agent): PersonalityValue => ({ voice: a.voice, quirks: a.quirks, catchphrases: a.catchphrases, idle_lines: a.idle_lines });
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
      <PersonalityFields key={key} value={v} onChange={setV} />
      <button className="btn-primary" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save size={14} />} Save personality</button>
    </div>
  );
}
