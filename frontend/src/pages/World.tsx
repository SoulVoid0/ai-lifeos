import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Gamepad2, Rocket, Send } from "lucide-react";
import { api, type AgentStatus, type MediaItem } from "../lib/api";
import { useAsync } from "../lib/hooks";
import {
  type Actor, type Pt, FRAMES, H, PLAYER_START, QUEUE, ROOMS, SEATS, TILE, W, YOUR_DESK, drawAmbient, drawCharacter, findPath,
  makeActor, nearObject, objectAt, objectLabel, randomFloor, renderStatic, roundRect, step, walk, wrap,
} from "../lib/station";
import { colorOf, useMissionEvents } from "../components/crew";
import { mediaUrl } from "../components/media";
import { AgentPanel, ObjectPanel } from "../components/StationPanels";
import { notifyChanged, Spinner, useToast } from "../components/ui";

const LOUNGE = ROOMS.find((r) => r.id === "lounge")!;
const SPEED = 3.2;
const PLAYER_SPEED = 5;
const center = (p: Pt): Pt => ({ x: Math.floor(p.x) + 0.5, y: Math.floor(p.y) + 0.5 });
const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

type Packet = { from: Pt; to: Pt; start: number; color: string };

/** Seat for an agent: its own room's desk, or one of the hot desks for agents you added. */
function seatFor(slug: string, extras: string[]): Pt {
  if (SEATS[slug]) return center(SEATS[slug]);
  const i = extras.indexOf(slug);
  return center(SEATS[`hot-${Math.max(0, i) % 4}`]);
}

function homeRoom(slug: string) {
  return ROOMS.find((r) => r.owner === slug) ?? LOUNGE;
}

export default function World() {
  const status = useAsync(api.crewStatus, [], 1200);
  const events = useMissionEvents(undefined, 80, 1000);
  const agents = useMemo(() => status.data?.agents ?? [], [status.data]);
  const [selected, setSelected] = useState<string | null>(null);
  const [composer, setComposer] = useState(false);
  const [obj, setObj] = useState<string | null>(null);
  const toast = useToast();
  const gallery = useAsync(() => api.media(undefined, 24), [], 5000);
  const frameItems = useMemo(() => (gallery.data ?? []).filter((m) => m.status === "done" && !/\.(mp4|webm)$/i.test(m.name ?? ""))
    .slice(0, FRAMES.length), [gallery.data]);
  const frameImgs = useRef<{ id: number; img: HTMLImageElement }[]>([]);
  const [hint, setHint] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const actors = useRef<Map<string, Actor>>(new Map());
  const player = useRef({ pos: { ...PLAYER_START }, path: [] as Pt[], facing: "down" as Actor["facing"], moving: false });
  const keys = useRef<Set<string>>(new Set());
  const packets = useRef<Packet[]>([]);
  const seen = useRef<number>(-1);
  const colorsKey = agents.map((a) => `${a.slug}:${a.color}`).join(",");
  const staticLayer = useRef<HTMLCanvasElement | null>(null);
  const view = useRef({ scale: 2, dpr: 1 });

  // Inspectable from devtools (and used by the UI smoke test).
  useEffect(() => {
    (window as unknown as { __station?: unknown }).__station = { actors: actors.current, player: player.current };
  }, []);

  // Keep one actor per agent, and give each the right destination for its state.
  useEffect(() => {
    const map = actors.current;
    const extras = agents.filter((a) => !SEATS[a.slug]).map((a) => a.slug);
    for (const a of agents) {
      let act = map.get(a.slug);
      if (!act) {
        act = makeActor(a.slug, a.name, colorOf(a.color), a.icon, a.state === "idle" ? randomFloor(LOUNGE) : seatFor(a.slug, extras));
        map.set(a.slug, act);
      }
      Object.assign(act, { name: a.name, color: colorOf(a.color), icon: a.icon, state: a.state, idleLines: a.idle_lines ?? [] });
    }
    for (const slug of [...map.keys()]) if (!agents.some((a) => a.slug === slug)) map.delete(slug);
    const needYou = agents.filter((a) => a.state === "waiting" || a.state === "asking").map((a) => a.slug);
    for (const a of agents) {
      const act = map.get(a.slug)!;
      let key = "", dest: Pt | null = null;
      if (a.state === "working" || a.state === "offline") { key = `seat`; dest = seatFor(a.slug, extras); }
      else if (needYou.includes(a.slug)) { const q = QUEUE[needYou.indexOf(a.slug) % QUEUE.length]; key = `queue-${q.x}-${q.y}`; dest = center(q); }
      else if (!act.targetKey.startsWith("wander")) { key = "wander-0"; act.nextWander = 0; }
      if (dest && key !== act.targetKey) { act.targetKey = key; act.path = findPath(act.pos, dest); }
    }
  }, [agents]);

  // New events become speech bubbles; delegations send a glowing packet from the Overseer.
  useEffect(() => {
    if (!events.length) return;
    const fresh = Date.now() - 20000;
    for (const e of events) {
      if (e.id <= seen.current) continue;
      seen.current = Math.max(seen.current, e.id);
      if (new Date(e.created_at).getTime() < fresh || !e.agent) continue;
      const act = actors.current.get(e.agent);
      if (!act) continue;
      const text = e.kind === "delegate" ? e.summary.split(": ").slice(1).join(": ") || e.summary : e.summary;
      act.bubble = { text: text.replace(/[*`_#]+/g, "").replace(/\s+/g, " ").trim(), until: performance.now() + 6500 };
      if (e.kind === "delegate") {
        const boss = actors.current.get("overseer");
        if (boss && boss !== act) packets.current.push({ from: { ...boss.pos }, to: { ...act.pos }, start: performance.now(), color: act.color });
      }
    }
  }, [events]);

  // The gallery wall shows the latest generated images.
  useEffect(() => {
    let live = true;
    frameItems.forEach((m: MediaItem, i) => {
      if (frameImgs.current[i]?.id === m.id) return;
      mediaUrl(m.id).then((url) => {
        const img = new Image();
        img.onload = () => { if (live) frameImgs.current[i] = { id: m.id, img }; };
        img.src = url;
      }).catch(() => {});
    });
    frameImgs.current.length = Math.min(frameImgs.current.length, frameItems.length);
    return () => { live = false; };
  }, [frameItems]);

  // Send idle crew to the lounge for a break.
  const coffeeBreak = useCallback(() => {
    const lounge = ROOMS.find((r) => r.id === "lounge")!;
    let n = 0;
    for (const a of actors.current.values()) {
      if (a.state !== "idle") continue;
      a.path = findPath(a.pos, randomFloor(lounge));
      a.targetKey = `wander-coffee-${performance.now()}`;
      a.nextWander = performance.now() + 20000;
      a.bubble = { text: ["Coffee time!", "Don't mind if I do", "Finally, a break", "Extra shot, please"][n % 4], until: performance.now() + 5000 };
      n++;
    }
    toast(n ? `Coffee break: ${n} idle crew member${n === 1 ? "" : "s"} heading to the lounge` : "Everyone's busy. No break right now.", "info");
  }, [toast]);

  const activate = useCallback((use: string) => {
    setSelected(null);
    if (use === "desk") { setComposer(true); return; }
    if (use === "coffee") { coffeeBreak(); return; }
    setObj(use);
  }, [coffeeBreak]);

  useEffect(() => {
    staticLayer.current = renderStatic(Object.fromEntries(agents.map((a) => [a.slug, colorOf(a.color)])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorsKey]);

  // Size the canvas to the container, crisp on high-DPI screens.
  useEffect(() => {
    const el = wrapRef.current, cv = canvasRef.current;
    if (!el || !cv) return;
    const fit = () => {
      const scale = Math.max(0.5, Math.min(el.clientWidth / (W * TILE), el.clientHeight / (H * TILE)));
      const dpr = window.devicePixelRatio || 1;
      view.current = { scale, dpr };
      cv.style.width = `${W * TILE * scale}px`;
      cv.style.height = `${H * TILE * scale}px`;
      cv.width = Math.round(W * TILE * scale * dpr);
      cv.height = Math.round(H * TILE * scale * dpr);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const nearest = useCallback((p: Pt, radius: number) => {
    let best: Actor | null = null, d = radius;
    for (const a of actors.current.values()) {
      const dd = Math.hypot(a.pos.x - p.x, a.pos.y - p.y);
      if (dd < d) { d = dd; best = a; }
    }
    return best;
  }, []);

  // Keyboard: WASD / arrows to walk, E to talk to the nearest agent or use your desk.
  useEffect(() => {
    const typing = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    };
    const down = (e: KeyboardEvent) => {
      if (typing(e)) return;
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) {
        keys.current.add(k);
        player.current.path = [];
        e.preventDefault();
      }
      if (k === "e") {
        const a = nearest(player.current.pos, 2);
        const it = a ? null : nearObject(player.current.pos);
        if (a) { setObj(null); setSelected(a.slug); }
        else if (it?.use) activate(it.use);
      }
      if (k === "escape") { setSelected(null); setComposer(false); setObj(null); }
    };
    const up = (e: KeyboardEvent) => keys.current.delete(e.key.toLowerCase());
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [nearest, activate]);

  // The game loop.
  useEffect(() => {
    let raf = 0, last = performance.now();
    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = now / 1000;
      const cv = canvasRef.current;
      const g = cv?.getContext("2d");
      if (!cv || !g || !staticLayer.current) { raf = requestAnimationFrame(frame); return; }

      // simulate
      const p = player.current;
      const k = keys.current;
      const vx = (k.has("d") || k.has("arrowright") ? 1 : 0) - (k.has("a") || k.has("arrowleft") ? 1 : 0);
      const vy = (k.has("s") || k.has("arrowdown") ? 1 : 0) - (k.has("w") || k.has("arrowup") ? 1 : 0);
      if (vx || vy) {
        walk(p.pos, vx, vy, dt, PLAYER_SPEED);
        p.moving = true;
        p.facing = Math.abs(vx) >= Math.abs(vy) && vx ? (vx > 0 ? "right" : "left") : vy > 0 ? "down" : "up";
      } else step(p, dt, PLAYER_SPEED);
      for (const a of actors.current.values()) {
        if (a.targetKey.startsWith("wander") && !a.path.length && now > a.nextWander) {
          const room = a.slug === "overseer" || Math.random() < 0.35 ? homeRoom(a.slug) : LOUNGE;
          a.path = findPath(a.pos, randomFloor(room));
          a.targetKey = `wander-${now}`;
          a.nextWander = now + 4000 + Math.random() * 8000;
        }
        // Idle crew mutter their personality lines now and then.
        if (a.state === "idle" && a.idleLines.length && now > a.nextChatter) {
          if (!a.bubble || now > a.bubble.until)
            a.bubble = { text: a.idleLines[Math.floor(Math.random() * a.idleLines.length)], until: now + 4000 };
          a.nextChatter = now + 18000 + Math.random() * 30000;
        }
        step(a, dt, SPEED);
        if (!a.path.length && a.targetKey === "seat") a.facing = a.state === "offline" ? "down" : "up";
        if (!a.path.length && a.targetKey.startsWith("queue")) a.facing = "up";
      }
      const nearA = nearest(p.pos, 2);
      const nearO = nearA ? null : nearObject(p.pos);
      const h = nearA ? `Press E to talk to ${nearA.name}` : nearO?.use ? `Press E to ${objectLabel(nearO.use)}` : null;
      setHint((prev) => (prev === h ? prev : h));

      // draw
      const { scale, dpr } = view.current;
      g.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0);
      g.imageSmoothingEnabled = false;
      g.drawImage(staticLayer.current, 0, 0);
      const colors: Record<string, string> = {};
      const working = new Set<string>();
      const extras = [...actors.current.values()].filter((a) => !SEATS[a.slug]).map((a) => a.slug);
      for (const a of actors.current.values()) {
        colors[a.slug] = a.color;
        if (a.state === "working") working.add(SEATS[a.slug] ? a.slug : `hot-${extras.indexOf(a.slug) % 4}`);
      }
      drawAmbient(g, t, colors, working);

      // gallery frames: cover-fit the latest images
      g.imageSmoothingEnabled = true;
      FRAMES.forEach((fr, i) => {
        const im = frameImgs.current[i]?.img;
        if (!im || !im.naturalWidth) return;
        const dx = fr.x * TILE + 2, dy = fr.y * TILE - 4, dw = fr.w * TILE - 4, dh = 16;
        const s = Math.max(dw / im.naturalWidth, dh / im.naturalHeight);
        const sw = dw / s, sh = dh / s;
        g.drawImage(im, (im.naturalWidth - sw) / 2, (im.naturalHeight - sh) / 2, sw, sh, dx, dy, dw, dh);
      });
      g.imageSmoothingEnabled = false;

      // room labels
      g.font = "bold 6px ui-monospace, SFMono-Regular, Menlo, monospace";
      g.textAlign = "center";
      for (const r of ROOMS) {
        const label = r.name.toUpperCase();
        const cx = (r.x + r.w / 2) * TILE, cy = r.y * TILE + 9;
        g.fillStyle = "rgba(0,0,0,0.45)";
        const w = g.measureText(label).width + 8;
        roundRect(g, cx - w / 2, cy - 6, w, 9, 3); g.fill();
        g.fillStyle = r.owner ? colors[r.owner] ?? "#cbd5e1" : "#e9d5ff";
        g.fillText(label, cx, cy + 0.5);
      }
      g.fillStyle = "#cbd5e1";
      g.fillText("YOUR DESK", (YOUR_DESK.x + YOUR_DESK.w / 2) * TILE, (YOUR_DESK.y + 2) * TILE + 2);

      // delegation packets
      packets.current = packets.current.filter((pk) => now - pk.start < 1200);
      for (const pk of packets.current) {
        const u = (now - pk.start) / 1200;
        const x = (pk.from.x + (pk.to.x - pk.from.x) * u) * TILE, y = (pk.from.y + (pk.to.y - pk.from.y) * u) * TILE - 10 - Math.sin(u * Math.PI) * 30;
        g.fillStyle = pk.color; g.globalAlpha = 0.35; g.beginPath(); g.arc(x, y, 5, 0, Math.PI * 2); g.fill();
        g.globalAlpha = 1; g.fillRect(x - 1.5, y - 1.5, 3, 3);
      }

      // characters, back to front
      const drawList: { y: number; draw: () => void }[] = [];
      for (const a of actors.current.values()) {
        const x = a.pos.x * TILE, y = a.pos.y * TILE + 5;
        drawList.push({ y, draw: () => {
          drawCharacter(g, x, y, { color: a.color, icon: a.icon, facing: a.facing, moving: a.moving, t: t + a.phase,
            dim: a.state === "offline", typing: a.state === "working" && !a.moving, seed: hash(a.slug) });
          const sel = selected === a.slug;
          g.font = `bold 5px ui-sans-serif, system-ui, sans-serif`;
          const label = a.name;
          const lw = g.measureText(label).width + 6;
          g.fillStyle = sel ? a.color : "rgba(10,12,18,0.7)";
          roundRect(g, x - lw / 2, y + 2, lw, 7, 2); g.fill();
          g.fillStyle = sel ? "#0b0e14" : a.color;
          g.fillText(label, x, y + 7.2);
          // state badge
          if (a.state === "waiting" || a.state === "asking" || a.state === "offline") {
            const bounce = Math.round(Math.sin(t * 5 + a.phase) * 1.5);
            const ch = a.state === "waiting" ? "!" : a.state === "asking" ? "?" : "z";
            g.fillStyle = a.state === "offline" ? "#64748b" : "#f59e0b";
            g.beginPath(); g.arc(x + 6, y - 22 + bounce, 4, 0, Math.PI * 2); g.fill();
            g.fillStyle = "#0b0e14"; g.font = "bold 6px ui-sans-serif, system-ui"; g.fillText(ch, x + 6, y - 20 + bounce);
          } else if (a.state === "working" && !a.moving) {
            g.fillStyle = "rgba(10,12,18,0.7)";
            roundRect(g, x + 2, y - 27, 12, 6, 2); g.fill();
            for (let d = 0; d < 3; d++) {
              g.fillStyle = Math.floor(t * 4) % 3 === d ? "#e5e7eb" : "#64748b";
              g.fillRect(x + 4 + d * 3.3, y - 25, 2, 2);
            }
          }
          // speech bubble
          if (a.bubble && performance.now() < a.bubble.until) {
            g.font = "4.5px ui-sans-serif, system-ui, sans-serif";
            const lines = wrap(g, a.bubble.text, 78, 3);
            const bw = Math.max(...lines.map((l) => g.measureText(l).width)) + 8, bh = lines.length * 6 + 4;
            const bx = Math.min(Math.max(x - bw / 2, 2), W * TILE - bw - 2), by = y - 30 - bh;
            g.fillStyle = "rgba(248,250,252,0.95)";
            roundRect(g, bx, by, bw, bh, 3); g.fill();
            g.beginPath(); g.moveTo(x - 2, by + bh); g.lineTo(x + 2, by + bh); g.lineTo(x, by + bh + 3); g.fill();
            g.fillStyle = "#0f172a"; g.textAlign = "left";
            lines.forEach((l, i) => g.fillText(l, bx + 4, by + 6.5 + i * 6));
            g.textAlign = "center";
          }
        } });
      }
      drawList.push({ y: p.pos.y * TILE + 5, draw: () => {
        const x = p.pos.x * TILE, y = p.pos.y * TILE + 5;
        drawCharacter(g, x, y, { color: "#e5e7eb", icon: "player", facing: p.facing, moving: p.moving, t, player: true, seed: 3 });
        g.font = "bold 5px ui-sans-serif, system-ui, sans-serif";
        const lw = g.measureText("You").width + 6;
        g.fillStyle = "#8b5cf6"; roundRect(g, x - lw / 2, y + 2, lw, 7, 2); g.fill();
        g.fillStyle = "#fff"; g.fillText("You", x, y + 7.2);
      } });
      drawList.sort((a, b) => a.y - b.y).forEach((d) => d.draw());

      // gentle vignette
      const grad = g.createRadialGradient(W * TILE / 2, H * TILE / 2, H * TILE * 0.35, W * TILE / 2, H * TILE / 2, W * TILE * 0.7);
      grad.addColorStop(0, "rgba(0,0,0,0)"); grad.addColorStop(1, "rgba(0,0,0,0.35)");
      g.fillStyle = grad; g.fillRect(0, 0, W * TILE, H * TILE);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [nearest, selected]);

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const { scale } = view.current;
    const pt = { x: (e.clientX - rect.left) / scale / TILE, y: (e.clientY - rect.top) / scale / TILE };
    // Characters are drawn above their feet, so test a box around the body.
    let hit: Actor | null = null;
    for (const a of actors.current.values())
      if (Math.abs(pt.x - a.pos.x) < 0.55 && pt.y > a.pos.y - 1.25 && pt.y < a.pos.y + 0.8) hit = a;
    if (hit) {
      setObj(null);
      setSelected(hit.slug);
      player.current.path = findPath(player.current.pos, { x: hit.pos.x, y: hit.pos.y + 1 });
      return;
    }
    const it = objectAt(pt);
    if (it?.use) {
      activate(it.use);
      player.current.path = findPath(player.current.pos, { x: it.x + it.w / 2, y: it.y + it.h + 0.5 });
      return;
    }
    player.current.path = findPath(player.current.pos, pt);
  };

  const sel = agents.find((a) => a.slug === selected) ?? null;
  const counts = { working: agents.filter((a) => a.state === "working").length, needs: agents.filter((a) => a.state === "waiting" || a.state === "asking").length };

  return (
    <div className="flex h-full flex-col p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3 px-1">
        <Gamepad2 size={18} className="text-accent" />
        <div className="text-base font-semibold">Station</div>
        <div className="text-xs text-muted">{counts.working} working · {counts.needs} need you · {agents.length} crew</div>
        <div className="ml-auto text-xs text-muted">WASD / arrows or click to walk · E or click agents and props · your desk starts a mission</div>
      </div>
      <div ref={wrapRef} className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-line bg-[#0b0e14]">
        <div className="grid h-full w-full place-items-center">
          <canvas ref={canvasRef} onClick={onClick} className="cursor-pointer" style={{ imageRendering: "pixelated" }} />
        </div>
        {!status.data && <div className="absolute inset-0 grid place-items-center"><Spinner /></div>}
        {hint && !sel && !composer && !obj && (
          <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-lg bg-black/70 px-3 py-1.5 text-xs text-white">{hint}</div>
        )}
        {sel && <AgentPanel agent={sel} onClose={() => setSelected(null)} />}
        {obj && !sel && <ObjectPanel use={obj} agents={agents} frames={frameItems} onClose={() => setObj(null)} onCompose={() => { setObj(null); setComposer(true); }} />}
        {composer && <DeskComposer agents={agents} onClose={() => setComposer(false)} />}
      </div>
    </div>
  );
}

function DeskComposer({ agents, onClose }: { agents: AgentStatus[]; onClose: () => void }) {
  const toast = useToast();
  const [goal, setGoal] = useState("");
  const [target, setTarget] = useState("");
  const launch = async () => {
    if (!goal.trim()) return;
    try {
      const m = await api.startMission(goal.trim(), target || null);
      toast(`Mission #${m.id} launched`);
      notifyChanged();
      onClose();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="absolute inset-0 grid place-items-center bg-black/40" onClick={onClose}>
      <div className="card w-full max-w-md p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center gap-2 font-semibold"><Rocket size={16} className="text-accent" /> Your desk: new mission</div>
        <textarea autoFocus className="input min-h-24" placeholder="What should the crew do? Atlas will split it into tasks."
          value={goal} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) launch(); }} />
        <div className="mt-2 flex gap-2">
          <select className="input flex-1" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Atlas plans and delegates</option>
            {agents.filter((a) => a.slug !== "overseer" && a.enabled).map((a) => <option key={a.slug} value={a.slug}>Only {a.name} ({a.role})</option>)}
          </select>
          <button className="btn-primary" onClick={launch} disabled={!goal.trim()}><Send size={14} /> Launch</button>
        </div>
      </div>
    </div>
  );
}
