// The desktop pet: a small creature that lives on top of your desktop (its own transparent window in the desktop app,
// a corner of the LifeOS window in a browser or the OS session). It reflects the crew: naps when nothing's going on,
// types when agents work, waves a "!" when something needs you, cheers on level-ups and hides during a panic.
// Drag it anywhere; double-click it to open LifeOS.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type PetConfig } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { focusMain, isDesktop } from "../lib/desktop";
import { play } from "../lib/sfx";

type PetState = "sleep" | "idle" | "work" | "alert" | "happy" | "hide";
const COLORS: Record<PetConfig["species"], [string, string]> = {
  bot: ["#94a3b8", "#22d3ee"], cat: ["#f59e0b", "#fde68a"], slime: ["#34d399", "#a7f3d0"], ghost: ["#e5e7eb", "#c4b5fd"],
  fox: ["#f97316", "#fff7ed"],
};

function usePetMind(cfg: PetConfig | undefined) {
  const crew = useAsync(api.crewStatus, [], 4000);
  const panic = useAsync(api.panic, [], 3000);
  const notes = useAsync(api.notifications, [], 8000);
  const [happyUntil, setHappyUntil] = useState(0);
  const [line, setLine] = useState<{ text: string; until: number } | null>(null);
  const seen = useRef<Set<number> | null>(null);

  useEffect(() => {
    const list = notes.data ?? [];
    if (!seen.current) { seen.current = new Set(list.map((n) => n.id)); return; }
    for (const n of list) {
      if (seen.current.has(n.id)) continue;
      seen.current.add(n.id);
      if (n.kind === "levelup") { setHappyUntil(Date.now() + 8000); setLine({ text: n.title, until: Date.now() + 8000 }); }
      else if (cfg?.speak) setLine({ text: n.title, until: Date.now() + 6000 });
    }
  }, [notes.data, cfg?.speak]);

  const agents = crew.data?.agents ?? [];
  const working = agents.filter((a) => a.state === "working");
  const waiting = (crew.data?.briefs.length ?? 0) + (crew.data?.pending_actions.length ?? 0);
  const hour = new Date().getHours();
  let state: PetState = "idle";
  if (panic.data?.active) state = "hide";
  else if (Date.now() < happyUntil) state = "happy";
  else if (waiting) state = "alert";
  else if (working.length) state = "work";
  else if (hour >= 23 || hour < 7 || !crew.data) state = "sleep";

  useEffect(() => {
    if (!cfg?.speak || line && Date.now() < line.until) return;
    if (state === "alert") setLine({ text: `${waiting} thing${waiting > 1 ? "s" : ""} need${waiting > 1 ? "" : "s"} you`, until: Date.now() + 5000 });
    else if (state === "work" && working[0]) setLine({ text: `${working[0].name}: ${working[0].current?.title ?? "working"}`, until: Date.now() + 5000 });
  }, [state, waiting, working.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return { state, line: line && Date.now() < line.until ? line.text : null, waiting };
}

function drawPet(g: CanvasRenderingContext2D, species: PetConfig["species"], state: PetState, t: number, size: number, facing: number) {
  const [body, accent] = COLORS[species];
  const s = size / 32;
  g.clearRect(0, 0, g.canvas.width, g.canvas.height);
  if (state === "hide") return;
  g.save();
  const cx = g.canvas.width / 2;
  const base = g.canvas.height - 4 * s;
  const bob = state === "sleep" ? Math.sin(t * 1.5) * 0.6 : state === "happy" ? -Math.abs(Math.sin(t * 8)) * 8 : Math.sin(t * 3) * 1.2;
  g.translate(cx, base + bob * s);
  g.scale(facing * s, s);
  // shadow
  g.fillStyle = "rgba(0,0,0,0.25)";
  g.beginPath(); g.ellipse(0, 0, 11 - bob * 0.3, 2.5, 0, 0, Math.PI * 2); g.fill();
  g.fillStyle = body;
  const r = (x: number, y: number, w: number, h: number, rr = 3) => { g.beginPath(); g.roundRect(x, y, w, h, rr); g.fill(); };
  if (species === "slime") {
    const squish = 1 + Math.sin(t * 4) * 0.06;
    g.beginPath(); g.ellipse(0, -8 * squish, 12 / squish, 9 * squish, 0, Math.PI, 0); g.lineTo(12, -1); g.lineTo(-12, -1); g.fill();
    g.fillStyle = accent; g.globalAlpha = 0.5; g.beginPath(); g.ellipse(-5, -12, 3, 2, -0.5, 0, Math.PI * 2); g.fill(); g.globalAlpha = 1;
  } else if (species === "ghost") {
    g.globalAlpha = 0.92;
    g.beginPath(); g.arc(0, -16, 10, Math.PI, 0); g.lineTo(10, -2);
    for (let i = 0; i < 5; i++) g.lineTo(10 - (i + 0.5) * 4, -2 + (i % 2 ? 0 : 3 + Math.sin(t * 6 + i)));
    g.lineTo(-10, -2); g.closePath(); g.fill(); g.globalAlpha = 1;
  } else if (species === "bot") {
    r(-10, -22, 20, 16, 4);
    r(-7, -6, 5, 5, 1); r(2, -6, 5, 5, 1);
    g.fillStyle = accent; r(-1, -27, 2, 5, 1);
    g.beginPath(); g.arc(0, -28, 2 + (state === "alert" ? Math.abs(Math.sin(t * 10)) : 0), 0, Math.PI * 2); g.fill();
    g.fillStyle = "#0b0d12"; r(-8, -20, 16, 9, 2);
  } else {
    // cat / fox: body, head, ears, tail
    r(-9, -10, 16, 9, 4);
    g.beginPath(); g.arc(4, -15, 7, 0, Math.PI * 2); g.fill();
    g.beginPath(); g.moveTo(-1, -19); g.lineTo(0, -26); g.lineTo(4, -21); g.fill();
    g.beginPath(); g.moveTo(5, -21); g.lineTo(9, -26); g.lineTo(10, -18); g.fill();
    g.strokeStyle = body; g.lineWidth = species === "fox" ? 5 : 3; g.lineCap = "round";
    g.beginPath(); g.moveTo(-9, -6); g.quadraticCurveTo(-16, -8 + Math.sin(t * 3) * 3, -14, -16); g.stroke();
    if (species === "fox") { g.fillStyle = accent; g.beginPath(); g.arc(-14, -16, 2.2, 0, Math.PI * 2); g.fill(); r(3, -13, 7, 4, 2); }
  }
  // eyes
  const eyeY = species === "bot" ? -16 : species === "slime" ? -8 : species === "ghost" ? -17 : -16;
  const eyeX = species === "cat" || species === "fox" ? [2, 7] : [-4, 4];
  const blink = Math.sin(t * 0.9) > 0.97;
  g.fillStyle = species === "bot" ? accent : "#111827";
  for (const x of eyeX) {
    if (state === "sleep" || blink) { g.fillRect(x - 1.5, eyeY, 3, 0.8); continue; }
    if (state === "happy") { g.beginPath(); g.arc(x, eyeY + 1, 1.6, Math.PI, 0); g.lineWidth = 0.9; g.strokeStyle = g.fillStyle as string; g.stroke(); continue; }
    g.beginPath(); g.arc(x, eyeY, state === "alert" ? 2 : 1.5, 0, Math.PI * 2); g.fill();
  }
  g.restore();
  // state props, unflipped
  g.save();
  g.translate(cx, base);
  g.scale(s, s);
  g.font = "bold 8px ui-monospace, monospace";
  g.textAlign = "center";
  if (state === "sleep") {
    g.fillStyle = "#c7d2fe";
    for (let i = 0; i < 3; i++) {
      const p = (t * 0.5 + i / 3) % 1;
      g.globalAlpha = 1 - p;
      g.font = `bold ${5 + p * 5}px ui-monospace, monospace`;
      g.fillText("z", 10 + p * 8, -24 - p * 14);
    }
    g.globalAlpha = 1;
  } else if (state === "alert") {
    g.fillStyle = "#fbbf24";
    g.font = "bold 12px ui-sans-serif, sans-serif";
    g.fillText("!", 0, -32 - Math.abs(Math.sin(t * 6)) * 3);
  } else if (state === "work") {
    g.fillStyle = "#22d3ee";
    for (let i = 0; i < 3; i++) if (Math.sin(t * 9 + i * 2) > 0.3) g.fillRect(-8 + i * 6, -30 - (i % 2) * 2, 2, 2);
  } else if (state === "happy") {
    const hues = ["#f472b6", "#facc15", "#34d399", "#60a5fa"];
    for (let i = 0; i < 8; i++) {
      const a = t * 2 + i;
      g.fillStyle = hues[i % 4];
      g.fillRect(Math.cos(a) * 16, -20 + Math.sin(a * 1.3) * 14, 2, 2);
    }
  }
  g.restore();
}

function PetCanvas({ cfg, state, facing }: { cfg: PetConfig; state: PetState; facing: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ state, facing });
  live.current = { state, facing };
  useEffect(() => {
    const cv = ref.current;
    const g = cv?.getContext("2d");
    if (!cv || !g) return;
    let raf = 0, next = 0;
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      if (t < next) return;
      next = t + 1000 / 24;
      g.imageSmoothingEnabled = false;
      drawPet(g, cfg.species, live.current.state, t / 1000, cfg.size, live.current.facing);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [cfg.species, cfg.size]);
  return <canvas ref={ref} width={cfg.size * 1.6} height={cfg.size * 1.4} style={{ width: cfg.size * 1.6, height: cfg.size * 1.4 }} />;
}

function Bubble({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div className="pointer-events-none mx-auto mb-1 max-w-[220px] rounded-xl border border-white/10 bg-[#11141c]/90 px-2.5 py-1.5 text-center text-xs leading-snug text-gray-100 shadow-lg">
      {text}
    </div>
  );
}

/** The pet window's page (desktop app): transparent, walks along the bottom of the screen. */
export default function Pet() {
  const cfgQ = useAsync(api.pet, [], 15000);
  const cfg = cfgQ.data;
  const { state, line } = usePetMind(cfg);
  const [facing, setFacing] = useState(1);
  const last = useRef<PetState>(state);

  useEffect(() => {
    for (const el of [document.documentElement, document.body, document.getElementById("root")]) {
      if (!el) continue;
      el.style.background = "transparent";
      el.style.backgroundImage = "none";
      el.style.overflow = "hidden";
    }
  }, []);
  useEffect(() => {
    if (state === "alert" && last.current !== "alert") play("notify");
    last.current = state;
  }, [state]);

  // Roam: walk left and right along the bottom of the screen while idle.
  useEffect(() => {
    if (!isDesktop || !cfg?.roam) return;
    let stop = false;
    let dir = Math.random() < 0.5 ? -1 : 1;
    (async () => {
      const { getCurrentWindow, PhysicalPosition, currentMonitor } = await import("@tauri-apps/api/window");
      const win = getCurrentWindow();
      while (!stop) {
        await new Promise((r) => setTimeout(r, 120));
        if (stop || document.hidden || last.current !== "idle") continue;
        if (Math.random() < 0.004) { await new Promise((r) => setTimeout(r, 3000 + Math.random() * 6000)); continue; }
        try {
          const [pos, mon, size] = await Promise.all([win.outerPosition(), currentMonitor(), win.outerSize()]);
          if (!mon) continue;
          const minX = mon.position.x, maxX = mon.position.x + mon.size.width - size.width;
          let x = pos.x + dir * 3 * mon.scaleFactor;
          if (x <= minX || x >= maxX || Math.random() < 0.003) { dir = -dir; x = Math.max(minX, Math.min(maxX, x)); }
          setFacing(dir);
          await win.setPosition(new PhysicalPosition(Math.round(x), pos.y));
        } catch { /* window going away */ }
      }
    })();
    return () => { stop = true; };
  }, [cfg?.roam]);

  if (!cfg) return null;
  const drag = async (e: React.MouseEvent) => {
    if (e.button !== 0 || !isDesktop) return;
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    getCurrentWindow().startDragging().catch(() => {});
  };
  return (
    <div className="flex h-screen w-screen select-none flex-col items-center justify-end" title={`${cfg.name} · double-click to open LifeOS`}>
      <Bubble text={line} />
      <div onMouseDown={drag} onDoubleClick={() => focusMain()} className="cursor-grab active:cursor-grabbing">
        <PetCanvas cfg={cfg} state={state} facing={facing} />
      </div>
    </div>
  );
}

/** The pet in a corner of the LifeOS window (browser and OS session, where there's no separate window). */
export function PetOverlay() {
  const cfgQ = useAsync(api.pet, [], 30000);
  const cfg = cfgQ.data;
  const { state, line } = usePetMind(cfg?.enabled ? cfg : undefined);
  const [x, setX] = useState(0.85);
  const [dir, setDir] = useState(-1);
  useEffect(() => {
    if (!cfg?.enabled || !cfg.roam || state !== "idle") return;
    const t = setInterval(() => {
      setX((v) => {
        const nx = v + dir * 0.002;
        if (nx < 0.3 || nx > 0.92 || Math.random() < 0.004) { setDir((d) => -d); return Math.max(0.3, Math.min(0.92, nx)); }
        return nx;
      });
    }, 80);
    return () => clearInterval(t);
  }, [cfg?.enabled, cfg?.roam, state, dir]);
  const style = useMemo(() => ({ left: `${x * 100}%` }), [x]);
  if (!cfg?.enabled || state === "hide") return null;
  return (
    <div className="pointer-events-none fixed bottom-2 z-30 -translate-x-1/2" style={style}>
      <Bubble text={line} />
      <div className="pointer-events-auto" title={cfg.name}><PetCanvas cfg={cfg} state={state} facing={dir} /></div>
    </div>
  );
}
