// Generative ambient music for mood lighting: a soft drone plus slow arpeggiated notes from a mode that follows the
// mood (lydian when idle, dorian when the crew is busy, phrygian when something needs you...). Web Audio only.
import type { Mood } from "./api";

const SCALES: Record<string, number[]> = {
  lydian: [0, 2, 4, 6, 7, 9, 11], dorian: [0, 2, 3, 5, 7, 9, 10], phrygian: [0, 1, 3, 5, 7, 8, 10],
  aeolian: [0, 2, 3, 5, 7, 8, 10], ionian: [0, 2, 4, 5, 7, 9, 11],
};
const hz = (midi: number) => 440 * Math.pow(2, (midi - 69) / 12);

class Ambient {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private drone: OscillatorNode[] = [];
  private timer: number | null = null;
  private music: Mood["music"] | null = null;
  private delay: DelayNode | null = null;

  update(m: Mood["music"]) {
    const changed = !this.music || this.music.root !== m.root || this.music.scale !== m.scale;
    this.music = m;
    if (!m.on || m.volume <= 0 || m.tempo <= 0) return this.stop();
    if (!this.ctx) this.start();
    if (this.master && this.ctx) this.master.gain.linearRampToValueAtTime(m.volume * 0.25, this.ctx.currentTime + 2);
    if (changed) this.retune();
  }

  private start() {
    if (!("AudioContext" in window)) return;
    this.ctx = new AudioContext();
    this.master = this.ctx.createGain();
    this.master.gain.value = 0;
    const lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 1800;
    this.delay = this.ctx.createDelay(2);
    this.delay.delayTime.value = 0.45;
    const fb = this.ctx.createGain();
    fb.gain.value = 0.35;
    this.delay.connect(fb).connect(this.delay);
    this.delay.connect(lp);
    lp.connect(this.master).connect(this.ctx.destination);
    this.retune();
    this.tick();
  }

  private retune() {
    if (!this.ctx || !this.master || !this.music) return;
    for (const o of this.drone) { try { o.stop(this.ctx.currentTime + 1.5); } catch { /* already stopped */ } }
    this.drone = [];
    for (const [i, off] of [0, 7, 12].entries()) {
      const o = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      o.type = i === 2 ? "triangle" : "sine";
      o.frequency.value = hz(this.music.root - 12 + off);
      o.detune.value = (i - 1) * 4;
      g.gain.setValueAtTime(0, this.ctx.currentTime);
      g.gain.linearRampToValueAtTime(i === 0 ? 0.32 : 0.14, this.ctx.currentTime + 3);
      o.connect(g).connect(this.master);
      o.start();
      this.drone.push(o);
    }
  }

  private tick = () => {
    if (!this.ctx || !this.master || !this.music) return;
    const m = this.music;
    const beat = 60 / Math.max(30, m.tempo);
    if (Math.random() < 0.35 + m.intensity * 0.5) {
      const scale = SCALES[m.scale] ?? SCALES.ionian;
      const deg = scale[Math.floor(Math.random() * scale.length)];
      const oct = Math.random() < 0.3 ? 24 : 12;
      const t = this.ctx.currentTime + 0.02;
      const o = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      o.type = Math.random() < 0.5 ? "sine" : "triangle";
      o.frequency.value = hz(m.root + oct + deg);
      const len = beat * (1 + Math.random() * 3);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.18 * (0.5 + m.intensity), t + 0.04);
      g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(g);
      g.connect(this.master);
      if (this.delay) g.connect(this.delay);
      o.start(t);
      o.stop(t + len + 0.1);
    }
    this.timer = window.setTimeout(this.tick, beat * 1000);
  };

  stop() {
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = null;
    const c = this.ctx;
    if (!c || !this.master) return;
    this.master.gain.linearRampToValueAtTime(0, c.currentTime + 1);
    setTimeout(() => c.close().catch(() => {}), 1200);
    this.ctx = null;
    this.master = null;
    this.drone = [];
    this.delay = null;
  }
}

export const ambient = new Ambient();
