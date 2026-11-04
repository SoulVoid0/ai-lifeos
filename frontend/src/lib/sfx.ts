// Sound effects, synthesized with the Web Audio API (no audio files: nothing to download, nothing to license).
// Three themes: "scifi" (bleeps, sweeps and soft lasers), "soft" (gentle bells) and "retro" (8-bit square waves).
import { api, type SfxConfig } from "./api";

export type Sfx = "click" | "notify" | "success" | "error" | "start" | "approve" | "levelup" | "panic" | "wake" | "message" | "open" | "close";

let ctx: AudioContext | null = null;
let cfg: SfxConfig = { enabled: true, volume: 0.5, theme: "scifi", clicks: false };
let loaded = false;

export function setSfxConfig(c: SfxConfig) {
  cfg = c;
  loaded = true;
}

export async function loadSfxConfig() {
  try { setSfxConfig(await api.sfx()); } catch { /* defaults */ }
}

function audio(): AudioContext | null {
  if (typeof window === "undefined" || !("AudioContext" in window)) return null;
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

type Tone = { f: number; to?: number; t: number; d: number; type?: OscillatorType; g?: number };

function tones(list: Tone[], master = 1) {
  const a = audio();
  if (!a) return;
  const out = a.createGain();
  out.gain.value = cfg.volume * master * 0.35;
  out.connect(a.destination);
  const now = a.currentTime + 0.01;
  for (const n of list) {
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = n.type ?? (cfg.theme === "retro" ? "square" : cfg.theme === "soft" ? "sine" : "triangle");
    o.frequency.setValueAtTime(n.f, now + n.t);
    if (n.to) o.frequency.exponentialRampToValueAtTime(n.to, now + n.t + n.d);
    const peak = (n.g ?? 1) * (cfg.theme === "retro" ? 0.35 : 1);
    g.gain.setValueAtTime(0.0001, now + n.t);
    g.gain.exponentialRampToValueAtTime(peak, now + n.t + Math.min(0.015, n.d / 4));
    g.gain.exponentialRampToValueAtTime(0.0001, now + n.t + n.d);
    o.connect(g).connect(out);
    o.start(now + n.t);
    o.stop(now + n.t + n.d + 0.05);
  }
}

function noise(t: number, d: number, from: number, to: number, g = 0.5) {
  const a = audio();
  if (!a) return;
  const buf = a.createBuffer(1, Math.ceil(a.sampleRate * d), a.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  const src = a.createBufferSource();
  src.buffer = buf;
  const f = a.createBiquadFilter();
  f.type = "bandpass";
  f.Q.value = 4;
  const now = a.currentTime + 0.01 + t;
  f.frequency.setValueAtTime(from, now);
  f.frequency.exponentialRampToValueAtTime(to, now + d);
  const gain = a.createGain();
  gain.gain.setValueAtTime(g * cfg.volume * 0.35, now);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + d);
  src.connect(f).connect(gain).connect(a.destination);
  src.start(now);
}

const SCIFI: Record<Sfx, () => void> = {
  click: () => tones([{ f: 1800, to: 1200, t: 0, d: 0.03, g: 0.3 }]),
  open: () => tones([{ f: 400, to: 900, t: 0, d: 0.12, g: 0.4 }]),
  close: () => tones([{ f: 900, to: 400, t: 0, d: 0.12, g: 0.4 }]),
  message: () => tones([{ f: 880, t: 0, d: 0.08 }, { f: 1320, t: 0.07, d: 0.1 }]),
  notify: () => tones([{ f: 660, t: 0, d: 0.12 }, { f: 990, t: 0.1, d: 0.18 }, { f: 1320, t: 0.2, d: 0.25, g: 0.6 }]),
  success: () => tones([{ f: 523, t: 0, d: 0.12 }, { f: 659, t: 0.09, d: 0.12 }, { f: 784, t: 0.18, d: 0.12 }, { f: 1047, t: 0.27, d: 0.35 }]),
  error: () => tones([{ f: 220, to: 160, t: 0, d: 0.18, type: "sawtooth", g: 0.5 }, { f: 180, to: 120, t: 0.16, d: 0.25, type: "sawtooth", g: 0.5 }]),
  start: () => { tones([{ f: 200, to: 1600, t: 0, d: 0.35, g: 0.5 }]); noise(0, 0.35, 400, 4000, 0.25); },
  approve: () => tones([{ f: 740, t: 0, d: 0.08 }, { f: 1110, t: 0.08, d: 0.16 }]),
  levelup: () => tones([523, 659, 784, 1047, 1319, 1568].map((f, i) => ({ f, t: i * 0.08, d: 0.22, g: 0.7 }))
    .concat([{ f: 2093, t: 0.5, d: 0.6, g: 0.5 }])),
  panic: () => { for (let i = 0; i < 3; i++) tones([{ f: 880, to: 440, t: i * 0.28, d: 0.25, type: "square", g: 0.4 }]); },
  wake: () => tones([{ f: 1047, t: 0, d: 0.09, g: 0.5 }, { f: 1568, t: 0.08, d: 0.14, g: 0.5 }]),
};

const SOFT: Partial<Record<Sfx, () => void>> = {
  notify: () => tones([{ f: 784, t: 0, d: 0.6, type: "sine" }, { f: 1175, t: 0.12, d: 0.7, type: "sine", g: 0.5 }]),
  success: () => tones([{ f: 659, t: 0, d: 0.5, type: "sine" }, { f: 988, t: 0.15, d: 0.7, type: "sine" }]),
  start: () => tones([{ f: 523, t: 0, d: 0.4, type: "sine" }, { f: 784, t: 0.12, d: 0.5, type: "sine" }]),
  error: () => tones([{ f: 392, t: 0, d: 0.4, type: "sine" }, { f: 311, t: 0.18, d: 0.5, type: "sine" }]),
};

export function play(name: Sfx) {
  if (!loaded) loadSfxConfig();
  if (!cfg.enabled || cfg.volume <= 0) return;
  if (name === "click" && !cfg.clicks) return;
  try {
    (cfg.theme === "soft" && SOFT[name] ? SOFT[name]! : SCIFI[name])();
  } catch { /* audio blocked until the first click: fine */ }
}

/** Play a short click on any button press (if turned on in Settings → Sound). */
export function installClickSounds() {
  const h = (e: MouseEvent) => {
    const el = (e.target as HTMLElement | null)?.closest("button, a");
    if (el) play("click");
  };
  document.addEventListener("click", h, true);
  return () => document.removeEventListener("click", h, true);
}
