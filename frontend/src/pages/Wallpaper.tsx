// Live wallpaper: the Station's crew going about their work, full screen behind the desktop icons.
// Shown in the desktop app's "wallpaper" window (and at #/wallpaper in a browser, e.g. as the OS session's background).
// It is look-only, cheap (a low frame cap, a pre-rendered map) and pauses itself while a game runs.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AgentStatus, type MissionEvent, type WallpaperConfig } from "../lib/api";
import { useAsync } from "../lib/hooks";
import {
  type Actor, type Pt, H, ROOMS, SEATS, TILE, W, drawAmbient, drawCharacter, findPath, makeActor, randomFloor, renderStatic,
  roundRect, step, wrap,
} from "../lib/station";
import { colorOf } from "../components/crew";

const LOUNGE = ROOMS.find((r) => r.id === "lounge")!;
const center = (p: Pt): Pt => ({ x: Math.floor(p.x) + 0.5, y: Math.floor(p.y) + 0.5 });
const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const homeRoom = (slug: string) => ROOMS.find((r) => r.owner === slug) ?? LOUNGE;
const seatFor = (slug: string, extras: string[]) => center(SEATS[slug] ?? SEATS[`hot-${Math.max(0, extras.indexOf(slug)) % 4}`]);

export default function Wallpaper() {
  const cfg = useAsync(api.wallpaper, [], 10000);
  const status = useAsync(api.crewStatus, [], 3000);
  const events = useAsync(() => api.missionEvents(0), [], 4000);
  const c: WallpaperConfig = cfg.data ?? { enabled: true, fps: 15, show_clock: true, show_ticker: true, dim: 0.15 };
  const paused = !!cfg.data?.game_mode;
  const agents: AgentStatus[] = useMemo(() => status.data?.agents ?? [], [status.data]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const actors = useRef<Map<string, Actor>>(new Map());
  const staticLayer = useRef<HTMLCanvasElement | null>(null);
  const seen = useRef(-1);
  const live = useRef({ fps: c.fps, paused });
  live.current = { fps: c.fps, paused };
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    document.body.style.background = "#05060a";
    document.body.style.overflow = "hidden";
    const t = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(t);
  }, []);

  const colorsKey = agents.map((a) => `${a.slug}:${a.color}`).join(",");
  useEffect(() => {
    staticLayer.current = renderStatic(Object.fromEntries(agents.map((a) => [a.slug, colorOf(a.color)])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorsKey]);

  useEffect(() => {
    const map = actors.current;
    const extras = agents.filter((a) => !SEATS[a.slug]).map((a) => a.slug);
    for (const a of agents) {
      let act = map.get(a.slug);
      if (!act) {
        act = makeActor(a.slug, a.name, colorOf(a.color), a.icon, a.state === "idle" ? randomFloor(homeRoom(a.slug)) : seatFor(a.slug, extras));
        map.set(a.slug, act);
      }
      Object.assign(act, { name: a.name, color: colorOf(a.color), icon: a.icon, state: a.state, idleLines: a.idle_lines ?? [] });
      if (a.state !== "idle" && act.targetKey !== "seat") { act.targetKey = "seat"; act.path = findPath(act.pos, seatFor(a.slug, extras)); }
      if (a.state === "idle" && act.targetKey === "seat") { act.targetKey = "wander-0"; act.nextWander = 0; }
      if (a.state === "working" && a.current && (!act.bubble || performance.now() > act.bubble.until))
        act.bubble = { text: a.current.title, until: performance.now() + 5000 };
    }
    for (const slug of [...map.keys()]) if (!agents.some((a) => a.slug === slug)) map.delete(slug);
  }, [agents]);

  useEffect(() => {
    for (const e of events.data ?? []) {
      if (e.id <= seen.current) continue;
      const first = seen.current < 0;
      seen.current = Math.max(seen.current, e.id);
      if (first || !e.agent) continue;
      const act = actors.current.get(e.agent);
      if (act) act.bubble = { text: e.summary.replace(/[*`_#]+/g, "").replace(/\s+/g, " ").trim(), until: performance.now() + 6000 };
    }
  }, [events.data]);

  useEffect(() => {
    const cv = canvasRef.current;
    const g = cv?.getContext("2d", { alpha: false });
    if (!cv || !g) return;
    let raf = 0, nextAt = 0, last = performance.now();
    const fit = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      cv.width = Math.round(window.innerWidth * ratio);
      cv.height = Math.round(window.innerHeight * ratio);
    };
    fit();
    window.addEventListener("resize", fit);
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      const { fps, paused: hold } = live.current;
      if (hold || !staticLayer.current || t < nextAt) return;
      nextAt = t + 1000 / fps;
      const dt = Math.min(0.25, (t - last) / 1000);
      last = t;
      for (const a of actors.current.values()) {
        if (a.targetKey !== "seat" && !a.path.length && t > a.nextWander) {
          a.path = findPath(a.pos, randomFloor(Math.random() < 0.5 ? homeRoom(a.slug) : LOUNGE));
          a.targetKey = `wander-${t}`;
          a.nextWander = t + 5000 + Math.random() * 10000;
        }
        if (a.state === "idle" && a.idleLines.length && t > a.nextChatter && (!a.bubble || t > a.bubble.until)) {
          a.bubble = { text: a.idleLines[Math.floor(Math.random() * a.idleLines.length)], until: t + 4500 };
          a.nextChatter = t + 20000 + Math.random() * 30000;
        }
        step(a, dt, 2.6);
        if (!a.path.length && a.targetKey === "seat") a.facing = "up";
      }
      // Cover the screen with the map (cropping the edges if the aspect differs).
      const s = Math.max(cv.width / (W * TILE), cv.height / (H * TILE));
      const ox = (cv.width - W * TILE * s) / 2, oy = (cv.height - H * TILE * s) / 2;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.fillStyle = "#05060a";
      g.fillRect(0, 0, cv.width, cv.height);
      g.setTransform(s, 0, 0, s, ox, oy);
      g.imageSmoothingEnabled = false;
      g.drawImage(staticLayer.current, 0, 0);
      const colors: Record<string, string> = {};
      const working = new Set<string>();
      for (const a of actors.current.values()) { colors[a.slug] = a.color; if (a.state === "working") working.add(a.slug); }
      drawAmbient(g, t / 1000, colors, working);
      const sorted = [...actors.current.values()].sort((p, q) => p.pos.y - q.pos.y);
      for (const a of sorted) {
        drawCharacter(g, a.pos.x * TILE, a.pos.y * TILE, { color: a.color, icon: a.icon, facing: a.facing, moving: a.moving, t: t / 1000,
          dim: a.state === "offline", typing: a.state === "working" && !a.path.length, seed: hash(a.slug) });
        g.font = "bold 5px ui-monospace, monospace";
        g.textAlign = "center";
        g.fillStyle = "rgba(0,0,0,0.5)";
        const nw = g.measureText(a.name).width + 4;
        roundRect(g, a.pos.x * TILE - nw / 2, a.pos.y * TILE + 1, nw, 7, 2); g.fill();
        g.fillStyle = a.color;
        g.fillText(a.name, a.pos.x * TILE, a.pos.y * TILE + 6.5);
        if (a.bubble && t < a.bubble.until) {
          g.font = "5px ui-sans-serif, system-ui, sans-serif";
          if (!a.bubble.lines) { a.bubble.lines = wrap(g, a.bubble.text, 70, 2); a.bubble.w = Math.max(...a.bubble.lines.map((l) => g.measureText(l).width)) + 8; }
          const bw = a.bubble.w!, bh = a.bubble.lines.length * 6 + 4, bx = a.pos.x * TILE - bw / 2, by = a.pos.y * TILE - 20 - bh;
          g.fillStyle = "rgba(15,18,27,0.88)"; roundRect(g, bx, by, bw, bh, 3); g.fill();
          g.fillStyle = "#e5e7eb";
          a.bubble.lines.forEach((l, i) => g.fillText(l, a.pos.x * TILE, by + 7 + i * 6));
        }
      }
      if (c.dim > 0) { g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = `rgba(0,0,0,${c.dim})`; g.fillRect(0, 0, cv.width, cv.height); }
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("resize", fit); };
  }, [c.dim]);

  const latest: MissionEvent[] = (events.data ?? []).slice(-4).reverse();
  const names = Object.fromEntries(agents.map((a) => [a.slug, a.name]));
  const busy = agents.filter((a) => a.state === "working").length;
  return (
    <div className="fixed inset-0 select-none overflow-hidden bg-[#05060a]">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" style={{ imageRendering: "pixelated" }} />
      {c.show_clock && (
        <div className="pointer-events-none absolute right-10 top-8 text-right text-white drop-shadow-[0_2px_6px_rgba(0,0,0,0.9)]">
          <div className="text-6xl font-light tabular-nums">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
          <div className="text-lg opacity-80">{now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</div>
          <div className="mt-1 text-sm opacity-70">{paused ? "Game mode: the crew is on a break" : busy ? `${busy} agent${busy > 1 ? "s" : ""} working` : "The crew is idle"}</div>
        </div>
      )}
      {c.show_ticker && latest.length > 0 && !paused && (
        <div className="pointer-events-none absolute bottom-16 left-10 max-w-xl space-y-1 text-sm text-white/85 drop-shadow-[0_1px_4px_rgba(0,0,0,0.9)]">
          {latest.map((e) => <div key={e.id} className="truncate"><b>{names[e.agent ?? ""] ?? "Crew"}</b> {e.summary}</div>)}
        </div>
      )}
    </div>
  );
}
