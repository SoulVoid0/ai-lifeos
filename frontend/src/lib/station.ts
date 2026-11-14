// The crew's pixel-art station: map, path finding, NPC behavior and canvas rendering.
// Everything is drawn procedurally (no image assets). Positions are in tile units; one tile is 16 logical pixels.

import type { AgentState } from "./api";

export const TILE = 16;
export const W = 63;
export const H = 30;

export type Pt = { x: number; y: number };
type Rect = { x: number; y: number; w: number; h: number };
type Door = { side: "n" | "s" | "e" | "w"; at: number };
type FurnKind = "desk" | "holo" | "board" | "plant" | "table" | "cabinet" | "rack" | "bench" | "printer" | "shelf" | "orb"
  | "couch" | "coffee" | "arcade" | "radio" | "dish" | "mail" | "yourdesk" | "lab" | "scope" | "easel" | "frame" | "camera" | "album" | "reels";
export type Furn = Rect & { kind: FurnKind; owner?: string; use?: string };

export interface Room extends Rect {
  id: string;
  name: string;
  owner?: string;
  door: Door;
}

export const ROOMS: Room[] = [
  { id: "planning", name: "Planning Room", owner: "planner", x: 1, y: 1, w: 13, h: 9, door: { side: "s", at: 6 } },
  { id: "command", name: "Command Deck", owner: "overseer", x: 17, y: 1, w: 14, h: 9, door: { side: "s", at: 6 } },
  { id: "research", name: "Research Lab", owner: "research", x: 34, y: 1, w: 13, h: 9, door: { side: "s", at: 6 } },
  { id: "archive", name: "Archive", owner: "files", x: 1, y: 12, w: 13, h: 7, door: { side: "e", at: 3 } },
  { id: "workshop", name: "Workshop", owner: "coder", x: 34, y: 12, w: 13, h: 7, door: { side: "w", at: 3 } },
  { id: "memory", name: "Memory Vault", owner: "memory", x: 1, y: 21, w: 13, h: 8, door: { side: "n", at: 6 } },
  { id: "lounge", name: "Lounge", x: 17, y: 21, w: 14, h: 8, door: { side: "n", at: 6 } },
  { id: "comms", name: "Comms Room", owner: "comms", x: 34, y: 21, w: 13, h: 8, door: { side: "n", at: 6 } },
  { id: "studio", name: "Studio", owner: "creative", x: 50, y: 1, w: 12, h: 13, door: { side: "w", at: 7 } },
  { id: "gallery", name: "Gallery", x: 50, y: 16, w: 12, h: 13, door: { side: "w", at: 4 } },
];

const f = (kind: FurnKind, x: number, y: number, w = 1, h = 1, owner?: string): Furn => ({ kind, x, y, w, h, owner });
const u = (use: string, it: Furn): Furn => ({ ...it, use });

export const FURNITURE: Furn[] = [
  // planning
  f("desk", 3, 3, 2, 1, "planner"), u("planning", f("board", 7, 2, 4, 1)), f("table", 7, 5, 3, 2), f("plant", 12, 2), f("plant", 2, 8),
  // command
  u("missions", f("holo", 22, 3, 4, 2)), f("desk", 19, 2, 2, 1, "overseer"), u("missions", f("board", 27, 2, 2, 1)), f("plant", 18, 8), f("plant", 29, 8),
  // research
  f("desk", 36, 3, 2, 1, "research"), u("research", f("lab", 40, 2, 4, 1)), f("scope", 44, 6), f("plant", 35, 8), f("table", 39, 5, 2, 2),
  // archive
  f("desk", 3, 13, 2, 1, "files"), ...[7, 8, 9, 10, 11].map((x) => u("files", f("cabinet", x, 13))), ...[7, 8, 9].map((x) => u("files", f("cabinet", x, 16))), f("plant", 2, 17),
  // workshop
  f("desk", 36, 13, 2, 1, "coder"), u("projects", f("bench", 40, 13, 3, 1)), u("models", f("rack", 45, 13)), u("models", f("rack", 45, 14)),
  u("activity", f("printer", 44, 16)),
  // memory
  u("memory", f("shelf", 2, 22, 4, 1)), u("memory", f("shelf", 10, 22, 3, 1)), f("desk", 3, 25, 2, 1, "memory"), u("lessons", f("orb", 10, 25)), f("plant", 12, 27),
  // lounge
  f("couch", 19, 24, 3, 1), f("couch", 19, 27, 3, 1), f("table", 23, 25, 2, 1), u("coffee", f("coffee", 29, 22)), u("arcade", f("arcade", 27, 22)),
  f("plant", 18, 22), f("plant", 29, 27),
  // comms
  f("desk", 36, 24, 2, 1, "comms"), u("outbox", f("radio", 41, 23, 3, 1)), u("automations", f("dish", 45, 27)), u("outbox", f("mail", 35, 22, 3, 1)), f("plant", 45, 22),
  // hub
  // studio
  f("desk", 52, 3, 2, 1, "creative"), u("easel", f("easel", 56, 3, 2, 2)), f("camera", 59, 7), f("table", 54, 8, 3, 2),
  f("plant", 51, 12), f("plant", 60, 12), f("plant", 60, 2), f("desk", 52, 10, 2, 1, "photo"), f("desk", 57, 10, 2, 1, "video"),
  // gallery: the frames show the latest generated images
  ...[51, 54, 57].map((x, i) => u(`frame:${i}`, f("frame", x, 17, 3, 1))),
  ...[52, 56].map((x, i) => u(`frame:${i + 3}`, f("frame", x, 22, 3, 1))),
  f("couch", 54, 25, 3, 1), f("plant", 51, 27), f("plant", 60, 27),
  u("album:image", f("album", 51, 24)), u("album:video", f("reels", 60, 24)),
  // hub
  u("desk", f("yourdesk", 22, 15, 4, 1)), f("desk", 18, 12, 2, 1, "hot-0"), f("desk", 28, 12, 2, 1, "hot-1"),
  f("desk", 18, 18, 2, 1, "hot-2"), f("desk", 28, 18, 2, 1, "hot-3"), f("plant", 15, 10), f("plant", 32, 10),
];

/** Where each agent stands to work (the tile in front of its desk, or the holo table for the Overseer). */
export const SEATS: Record<string, Pt> = {
  planner: { x: 3, y: 4 }, overseer: { x: 23, y: 5 }, research: { x: 36, y: 4 }, files: { x: 3, y: 14 },
  coder: { x: 36, y: 14 }, memory: { x: 3, y: 26 }, comms: { x: 36, y: 25 }, creative: { x: 52, y: 4 },
  photo: { x: 52, y: 11 }, video: { x: 57, y: 11 },
  "hot-0": { x: 18, y: 13 }, "hot-1": { x: 28, y: 13 }, "hot-2": { x: 18, y: 19 }, "hot-3": { x: 28, y: 19 },
};
export const PLAYER_START: Pt = { x: 23.5, y: 14.5 };
export const QUEUE: Pt[] = [{ x: 22, y: 17 }, { x: 25, y: 17 }, { x: 20, y: 17 }, { x: 27, y: 17 }, { x: 23, y: 18 }, { x: 24, y: 18 }];
export const YOUR_DESK = FURNITURE.find((x) => x.kind === "yourdesk")!;
export const FRAMES = FURNITURE.filter((x) => x.kind === "frame");

/** What pressing E (or clicking) on a prop does. */
export const OBJECTS: Record<string, string> = {
  desk: "use your desk: start a crew mission", missions: "open the mission board", planning: "check the planning board",
  research: "open search", files: "browse the archive (Files)", projects: "open the project bench", models: "check the model rack",
  activity: "read the activity printout", memory: "browse the memory library", lessons: "consult the lesson orb",
  arcade: "play the arcade: crew high scores", coffee: "call a coffee break", outbox: "check the outbox",
  automations: "tune the automation dish", easel: "paint something with Muse", frame: "look at this piece",
  "album:image": "browse the photo album", "album:video": "browse the video album",
};
export const objectLabel = (use: string) => OBJECTS[use] ?? OBJECTS[use.split(":")[0]] ?? use;

const distToRect = (p: Pt, r: Rect) => Math.hypot(Math.max(r.x - p.x, 0, p.x - (r.x + r.w)), Math.max(r.y - p.y, 0, p.y - (r.y + r.h)));

/** The interactive prop closest to p, within radius tiles. */
export function nearObject(p: Pt, radius = 1.2): Furn | null {
  let best: Furn | null = null, d = radius;
  for (const it of FURNITURE) {
    if (!it.use) continue;
    const dd = distToRect(p, it);
    if (dd < d) { d = dd; best = it; }
  }
  return best;
}

/** The prop under a clicked point (props are drawn a little taller than their tiles). */
export function objectAt(p: Pt): Furn | null {
  return FURNITURE.find((it) => it.use && p.x >= it.x && p.x < it.x + it.w && p.y >= it.y - 0.6 && p.y < it.y + it.h) ?? null;
}

// ---------------------------------------------------------------- grid and path finding

export const blocked: boolean[] = new Array(W * H).fill(false);
const roomAt: (Room | null)[] = new Array(W * H).fill(null);
(function build() {
  for (let x = 0; x < W; x++) { blocked[x] = true; blocked[(H - 1) * W + x] = true; }
  for (let y = 0; y < H; y++) { blocked[y * W] = true; blocked[y * W + W - 1] = true; }
  for (const r of ROOMS) {
    for (let y = r.y; y < r.y + r.h; y++)
      for (let x = r.x; x < r.x + r.w; x++) {
        const edge = x === r.x || y === r.y || x === r.x + r.w - 1 || y === r.y + r.h - 1;
        if (edge) blocked[y * W + x] = true;
        else roomAt[y * W + x] = r;
      }
    for (const p of doorTiles(r)) blocked[p.y * W + p.x] = false;
  }
  for (const it of FURNITURE)
    for (let y = it.y; y < it.y + it.h; y++) for (let x = it.x; x < it.x + it.w; x++) blocked[y * W + x] = true;
})();

export function doorTiles(r: Room): Pt[] {
  const { side, at } = r.door;
  if (side === "s") return [{ x: r.x + at, y: r.y + r.h - 1 }, { x: r.x + at + 1, y: r.y + r.h - 1 }];
  if (side === "n") return [{ x: r.x + at, y: r.y }, { x: r.x + at + 1, y: r.y }];
  if (side === "e") return [{ x: r.x + r.w - 1, y: r.y + at }, { x: r.x + r.w - 1, y: r.y + at + 1 }];
  return [{ x: r.x, y: r.y + at }, { x: r.x, y: r.y + at + 1 }];
}

export const isBlocked = (x: number, y: number) =>
  x < 0 || y < 0 || x >= W || y >= H || blocked[Math.floor(y) * W + Math.floor(x)];

export function roomOf(p: Pt): Room | null {
  return roomAt[Math.floor(p.y) * W + Math.floor(p.x)] ?? null;
}

/** Breadth-first search on tiles; returns tile centers from start (exclusive) to goal (inclusive). */
export function findPath(from: Pt, to: Pt): Pt[] {
  const sx = Math.floor(from.x), sy = Math.floor(from.y);
  let gx = Math.floor(to.x), gy = Math.floor(to.y);
  if (isBlocked(gx, gy)) {
    const near = nearestFree(gx, gy);
    if (!near) return [];
    gx = near.x; gy = near.y;
  }
  const start = sy * W + sx, goal = gy * W + gx;
  if (start === goal) return [{ x: gx + 0.5, y: gy + 0.5 }];
  const prev = new Int32Array(W * H).fill(-1);
  prev[start] = start;
  const queue = [start];
  for (let i = 0; i < queue.length; i++) {
    const cur = queue[i];
    if (cur === goal) break;
    const cx = cur % W, cy = (cur / W) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = cx + dx, ny = cy + dy;
      if (isBlocked(nx, ny)) continue;
      const n = ny * W + nx;
      if (prev[n] !== -1) continue;
      prev[n] = cur;
      queue.push(n);
    }
  }
  if (prev[goal] === -1) return [];
  const out: Pt[] = [];
  for (let c = goal; c !== start; c = prev[c]) out.push({ x: (c % W) + 0.5, y: ((c / W) | 0) + 0.5 });
  return out.reverse();
}

function nearestFree(x: number, y: number): Pt | null {
  for (let r = 1; r < 6; r++)
    for (let dy = -r; dy <= r; dy++)
      for (let dx = -r; dx <= r; dx++) if (!isBlocked(x + dx, y + dy)) return { x: x + dx, y: y + dy };
  return null;
}

export function randomFloor(r: Rect): Pt {
  for (let i = 0; i < 40; i++) {
    const x = r.x + 1 + Math.floor(Math.random() * (r.w - 2));
    const y = r.y + 1 + Math.floor(Math.random() * (r.h - 2));
    if (!isBlocked(x, y)) return { x: x + 0.5, y: y + 0.5 };
  }
  return { x: r.x + r.w / 2, y: r.y + r.h / 2 };
}

// ---------------------------------------------------------------- actors

export type Facing = "up" | "down" | "left" | "right";

export interface Actor {
  slug: string;
  name: string;
  color: string;
  icon: string;
  pos: Pt;
  path: Pt[];
  facing: Facing;
  moving: boolean;
  targetKey: string;
  nextWander: number;
  state: AgentState;
  /** lines and w are filled in the first time the bubble is drawn, so text isn't re-measured every frame. */
  bubble: { text: string; until: number; lines?: string[]; w?: number } | null;
  phase: number;
  idleLines: string[];
  nextChatter: number;
}

export function makeActor(slug: string, name: string, color: string, icon: string, at: Pt): Actor {
  return { slug, name, color, icon, pos: { ...at }, path: [], facing: "down", moving: false, targetKey: "", nextWander: 0,
    state: "idle", bubble: null, phase: Math.random() * 10, idleLines: [], nextChatter: performance.now() + 5000 + Math.random() * 20000 };
}

export function step(a: { pos: Pt; path: Pt[]; facing: Facing; moving: boolean }, dt: number, speed: number) {
  a.moving = false;
  let budget = speed * dt;
  while (budget > 0 && a.path.length) {
    const t = a.path[0];
    const dx = t.x - a.pos.x, dy = t.y - a.pos.y;
    const d = Math.hypot(dx, dy);
    if (d < 1e-3) { a.path.shift(); continue; }
    a.moving = true;
    a.facing = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
    const move = Math.min(d, budget);
    a.pos.x += (dx / d) * move;
    a.pos.y += (dy / d) * move;
    budget -= move;
    if (move === d) a.path.shift();
  }
}

/** Move the player with the keyboard, sliding along walls. */
export function walk(p: Pt, vx: number, vy: number, dt: number, speed: number) {
  const len = Math.hypot(vx, vy) || 1;
  const nx = p.x + (vx / len) * speed * dt, ny = p.y + (vy / len) * speed * dt;
  const r = 0.28;
  const free = (x: number, y: number) => !isBlocked(x - r, y) && !isBlocked(x + r, y) && !isBlocked(x, y - r * 0.5) && !isBlocked(x, y + r * 0.5)
    && !isBlocked(x - r, y + r * 0.5) && !isBlocked(x + r, y + r * 0.5);
  if (free(nx, p.y)) p.x = nx;
  if (free(p.x, ny)) p.y = ny;
}

// ---------------------------------------------------------------- drawing

const shade = (hex: string, amt: number) => {
  const n = parseInt(hex.slice(1), 16);
  const ch = (v: number) => Math.max(0, Math.min(255, Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt)));
  return `rgb(${ch(n >> 16)},${ch((n >> 8) & 255)},${ch(n & 255)})`;
};
const mix = (a: string, b: string, t: number) => {
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const c = (s: number) => Math.round(((pa >> s) & 255) * (1 - t) + ((pb >> s) & 255) * t);
  return `rgb(${c(16)},${c(8)},${c(0)})`;
};

/** Draw the static map (floors, walls, furniture) once into an offscreen canvas at 1x. */
export function renderStatic(colors: Record<string, string>): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = W * TILE;
  c.height = H * TILE;
  const g = c.getContext("2d")!;
  const px = (x: number, y: number, w: number, h: number, col: string) => { g.fillStyle = col; g.fillRect(x, y, w, h); };
  // corridor floor
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      px(x * TILE, y * TILE, TILE, TILE, (x + y) % 2 ? "#1a1e29" : "#1c2130");
      px(x * TILE, y * TILE, TILE, 1, "#202637");
    }
  // room floors
  for (const r of ROOMS) {
    const tint = r.owner ? colors[r.owner] ?? "#94a3b8" : r.id === "gallery" ? "#c084fc" : "#a78b6d";
    for (let y = r.y + 1; y < r.y + r.h - 1; y++)
      for (let x = r.x + 1; x < r.x + r.w - 1; x++) {
        px(x * TILE, y * TILE, TILE, TILE, mix("#1f2433", tint, (x + y) % 2 ? 0.1 : 0.14));
        px(x * TILE, y * TILE + TILE - 1, TILE, 1, mix("#171b26", tint, 0.08));
      }
    if (r.id === "gallery") // carpet runner
      for (let x = r.x + 2; x < r.x + r.w - 2; x++) px(x * TILE, (r.y + 7) * TILE + 3, TILE, TILE - 6, "#3b2f4f");
    if (r.id === "lounge") // rug
      for (let y = r.y + 3; y < r.y + 6; y++) for (let x = r.x + 5; x < r.x + 9; x++) px(x * TILE + 2, y * TILE + 2, TILE - 4, TILE - 4, "#5b3b4f");
  }
  // walls
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const isWall = blocked[y * W + x] && !FURNITURE.some((it) => x >= it.x && x < it.x + it.w && y >= it.y && y < it.y + it.h);
      if (!isWall) continue;
      px(x * TILE, y * TILE, TILE, TILE, "#2c3449");
      px(x * TILE, y * TILE, TILE, 4, "#3d4865");
      px(x * TILE, y * TILE + TILE - 2, TILE, 2, "#222838");
    }
  // door frames
  for (const r of ROOMS)
    for (const d of doorTiles(r)) {
      const tint = r.owner ? colors[r.owner] ?? "#94a3b8" : r.id === "gallery" ? "#c084fc" : "#a78b6d";
      if (r.door.side === "n" || r.door.side === "s") px(d.x * TILE, d.y * TILE + 6, TILE, 4, shade(tint, -0.35));
      else px(d.x * TILE + 6, d.y * TILE, 4, TILE, shade(tint, -0.35));
    }
  for (const it of FURNITURE) drawFurniture(g, it, colors);
  return c;
}

function drawFurniture(g: CanvasRenderingContext2D, it: Furn, colors: Record<string, string>) {
  const X = it.x * TILE, Y = it.y * TILE, Wd = it.w * TILE, Ht = it.h * TILE;
  const px = (x: number, y: number, w: number, h: number, col: string) => { g.fillStyle = col; g.fillRect(X + x, Y + y, w, h); };
  const own = it.owner ? colors[it.owner] ?? "#94a3b8" : "#94a3b8";
  switch (it.kind) {
    case "desk":
    case "yourdesk": {
      const wood = it.kind === "yourdesk" ? "#6b4f3a" : "#4a5068";
      px(0, 5, Wd, Ht - 5, wood);
      px(0, 5, Wd, 2, shade(wood.startsWith("#") ? wood : "#4a5068", 0.25));
      px(1, Ht - 2, 2, 2, "#1d2130"); px(Wd - 3, Ht - 2, 2, 2, "#1d2130");
      const screens = it.kind === "yourdesk" ? 2 : 1;
      for (let s = 0; s < screens; s++) {
        const sx = Math.round((Wd / (screens + 1)) * (s + 1)) - 6;
        px(sx, -3, 12, 9, "#11141c");
        px(sx + 1, -2, 10, 7, it.kind === "yourdesk" ? "#2a6f97" : shade(own, -0.55));
        px(sx + 5, 6, 2, 1, "#11141c");
      }
      if (it.kind === "yourdesk") { px(Wd / 2 - 3, 8, 6, 2, "#c9b28f"); px(4, 8, 4, 3, "#d9d9d9"); }
      break;
    }
    case "holo":
      px(1, 3, Wd - 2, Ht - 4, "#262c3f"); px(2, 4, Wd - 4, Ht - 6, "#31384f");
      px(4, 6, Wd - 8, Ht - 10, shade(own, -0.5));
      break;
    case "board":
      px(0, 1, Wd, 11, "#e5e7eb"); px(0, 1, Wd, 1, "#9ca3af");
      for (let i = 0; i < it.w * 3; i++) px(3 + i * 5, 4 + (i % 3) * 2, 3, 2, ["#f59e0b", "#10b981", "#3b82f6", "#f43f5e"][i % 4]);
      break;
    case "plant":
      px(4, 10, 8, 6, "#7c4a2d"); px(5, 10, 6, 1, "#9a5c38");
      px(3, 3, 10, 7, "#2f7d4a"); px(5, 0, 6, 5, "#3c9a5c"); px(1, 6, 4, 3, "#2f7d4a"); px(11, 5, 4, 3, "#3c9a5c");
      break;
    case "table":
      px(1, 3, Wd - 2, Ht - 5, "#5a4636"); px(1, 3, Wd - 2, 2, "#6f5745");
      px(4, 6, 5, 4, "#e5e7eb"); px(Wd - 9, 8, 4, 3, "#fbbf24");
      break;
    case "cabinet":
      px(1, -4, 14, 20, "#3b4256"); px(1, -4, 14, 2, "#4b5470");
      for (let d = 0; d < 3; d++) { px(3, -1 + d * 6, 10, 4, "#2a2f40"); px(7, d * 6, 2, 1, "#9ca3af"); }
      break;
    case "rack":
      px(1, -6, 14, 22, "#1b1f2b"); px(1, -6, 14, 2, "#2b3142");
      for (let d = 0; d < 5; d++) px(3, -3 + d * 4, 10, 2, "#2c3346");
      break;
    case "bench":
      px(0, 4, Wd, Ht - 4, "#6b4f3a"); px(0, 4, Wd, 2, "#82614a");
      px(4, 0, 6, 5, "#9ca3af"); px(18, 1, 3, 4, "#f43f5e"); px(26, 2, 8, 2, "#d1d5db"); px(Wd - 8, 0, 5, 5, "#64748b");
      break;
    case "printer":
      px(1, 2, 14, 14, "#4b5563"); px(3, 4, 10, 7, "#1f2937"); px(5, 8, 6, 3, shade(own, 0.1));
      break;
    case "shelf":
      px(0, -4, Wd, 20, "#5a3f2e"); px(0, -4, Wd, 2, "#6f4f3a");
      for (let row = 0; row < 3; row++)
        for (let b = 0; b < it.w * 4; b++)
          px(1 + b * 4, -1 + row * 6, 3, 5, ["#3b82f6", "#f43f5e", "#f59e0b", "#10b981", "#8b5cf6"][(b * 7 + row * 3) % 5]);
      break;
    case "orb":
      px(3, 11, 10, 5, "#374151"); px(4, 2, 8, 9, shade(colors.memory ?? "#3b82f6", -0.2));
      break;
    case "couch":
      px(0, 3, Wd, Ht - 3, "#7c3a5a"); px(0, 3, Wd, 4, "#97476e"); px(0, 3, 3, Ht - 3, "#6a304c"); px(Wd - 3, 3, 3, Ht - 3, "#6a304c");
      break;
    case "coffee":
      px(2, 0, 12, 16, "#374151"); px(4, 3, 8, 4, "#111827"); px(6, 10, 4, 4, "#e5e7eb"); px(7, 11, 2, 2, "#6b3f24");
      break;
    case "arcade":
      px(2, -6, 12, 22, "#4c1d95"); px(4, -4, 8, 7, "#0f172a"); px(4, 6, 8, 3, "#1f2937"); px(5, 7, 2, 1, "#f43f5e"); px(9, 7, 2, 1, "#22d3ee");
      break;
    case "radio":
      px(0, 2, Wd, Ht - 2, "#374151"); px(0, 2, Wd, 2, "#4b5563");
      for (let k = 0; k < 6; k++) px(4 + k * 7, 7, 4, 4, "#111827");
      break;
    case "dish":
      px(6, 8, 4, 8, "#6b7280"); px(1, 0, 14, 9, "#d1d5db"); px(3, 2, 10, 5, "#9ca3af"); px(7, 3, 2, 2, "#f97316");
      break;
    case "mail":
      px(0, -4, Wd, 18, "#4b5563");
      for (let k = 0; k < it.w * 2; k++) { px(2 + k * 8, -2, 6, 5, "#1f2937"); px(2 + k * 8, 5, 6, 5, "#1f2937"); }
      break;
    case "lab":
      px(0, 4, Wd, Ht - 4, "#e5e7eb"); px(0, 4, Wd, 2, "#f3f4f6");
      for (let k = 0; k < it.w * 2; k++) px(3 + k * 8, -1, 3, 6, ["#10b981", "#22d3ee", "#a78bfa"][k % 3]);
      break;
    case "scope":
      px(6, 8, 4, 8, "#6b7280"); px(2, 2, 12, 5, "#9ca3af"); px(12, 1, 3, 7, "#4b5563");
      break;
    case "easel":
      px(6, -4, 2, Ht + 4, "#7c4a2d"); px(Wd - 8, -4, 2, Ht + 4, "#7c4a2d"); px(Wd / 2 - 1, -6, 2, Ht + 6, "#6b3f24");
      px(3, -3, Wd - 6, 18, "#f5f0e6"); px(3, 15, Wd - 6, 2, "#6b3f24");
      px(6, 0, 8, 6, colors.creative ?? "#ec4899"); px(14, 4, 9, 7, "#38bdf8"); px(8, 8, 6, 4, "#facc15");
      px(Wd - 5, Ht - 6, 4, 3, "#e5e7eb"); px(Wd - 4, Ht - 5, 1, 1, "#f43f5e"); px(Wd - 3, Ht - 5, 1, 1, "#22c55e");
      break;
    case "frame":
      px(0, -6, Wd, 20, "#b08d57"); px(1, -5, Wd - 2, 18, "#8a6a3d"); px(2, -4, Wd - 4, 16, "#1b1f2b");
      px(Wd / 2 - 3, 14, 6, 2, "#4b5563");
      break;
    case "album":
      px(6, 6, 4, 10, "#5a3f2e"); px(2, 2, 12, 6, "#6f4f3a");
      px(3, 0, 5, 7, "#f5f0e6"); px(8, 0, 5, 7, "#ece4d4"); px(4, 1, 3, 3, "#38bdf8"); px(9, 2, 3, 2, "#f59e0b");
      break;
    case "reels":
      px(1, -2, 14, 18, "#1f2937"); px(1, -2, 14, 2, "#374151");
      for (let k = 0; k < 2; k++) { px(3 + k * 6, 1, 5, 5, "#9ca3af"); px(5 + k * 6, 3, 1, 1, "#1f2937"); }
      px(3, 9, 10, 4, "#0f172a"); px(4, 10, 8, 2, "#14b8a6");
      break;
    case "camera":
      px(7, 6, 2, 10, "#4b5563"); px(4, 14, 8, 2, "#374151"); px(2, 0, 11, 7, "#1f2937"); px(12, 2, 3, 3, "#64748b");
      px(4, 2, 3, 2, "#ef4444");
      break;
  }
}

const ANIMATED = new Set<FurnKind>(["desk", "yourdesk", "holo", "rack", "cabinet", "orb", "radio", "camera", "easel", "arcade"]);
const ANIMATED_FURNITURE = FURNITURE.filter((it) => ANIMATED.has(it.kind));

/** Animated bits drawn every frame on top of the static map (screens, LEDs, holo table, orb). */
export function drawAmbient(g: CanvasRenderingContext2D, t: number, colors: Record<string, string>, working: Set<string>) {
  const px = (x: number, y: number, w: number, h: number, col: string) => { g.fillStyle = col; g.fillRect(x, y, w, h); };
  for (const it of ANIMATED_FURNITURE) {
    const X = it.x * TILE, Y = it.y * TILE;
    const own = it.owner ? colors[it.owner] ?? "#94a3b8" : "#94a3b8";
    if (it.kind === "desk" && it.owner && working.has(it.owner)) {
      const sx = X + Math.round((it.w * TILE) / 2) - 6;
      px(sx + 1, Y - 2, 10, 7, own);
      for (let l = 0; l < 3; l++) px(sx + 2, Y - 1 + l * 2, 2 + ((Math.floor(t * 6) + l * 3) % 7), 1, "#0b0e14");
    }
    if (it.kind === "yourdesk") {
      const on = Math.floor(t * 2) % 2 === 0;
      px(X + 19, Y + 1, 2, 1, on ? "#7dd3fc" : "#2a6f97");
    }
    if (it.kind === "holo") {
      const pulse = 0.5 + 0.5 * Math.sin(t * 2.4);
      g.globalAlpha = 0.25 + pulse * 0.35;
      px(X + 6, Y - 6, it.w * TILE - 12, 14, colors.overseer ?? "#8b5cf6");
      g.globalAlpha = 1;
      for (let k = 0; k < 5; k++) {
        const a = t * 1.5 + k * 1.26;
        px(X + it.w * 8 + Math.cos(a) * 18 - 1, Y + 1 + Math.sin(a) * 5, 2, 2, "#e9d5ff");
      }
    }
    if (it.kind === "rack" || it.kind === "cabinet")
      for (let d = 0; d < 4; d++) {
        const lit = Math.sin(t * (3 + d) + it.x * 7 + d * 13) > 0.2;
        px(X + 11, Y - 3 + d * 4 + (it.kind === "cabinet" ? 2 : 0), 2, 1, lit ? (d % 2 ? "#22c55e" : "#38bdf8") : "#1f2937");
      }
    if (it.kind === "orb") {
      g.globalAlpha = 0.35 + 0.25 * Math.sin(t * 3);
      px(X + 2, Y, 12, 12, colors.memory ?? "#3b82f6");
      g.globalAlpha = 1;
    }
    if (it.kind === "radio")
      for (let k = 0; k < 6; k++) px(X + 4 + k * 7, Y + 7 + 4 - Math.round(2 + 2 * Math.sin(t * 5 + k)), 4, 1, "#f97316");
    if (it.kind === "camera" && working.has("video")) px(X + 4, Y + 2, 3, 2, Math.floor(t * 2) % 2 ? "#ef4444" : "#7f1d1d");
    if (it.kind === "easel" && (working.has("creative") || working.has("photo")))
      for (let k = 0; k < 3; k++) px(X + 5 + ((Math.floor(t * 5) + k * 7) % 20), Y + 1 + k * 4, 3, 2, ["#f43f5e", "#a3e635", "#c084fc"][k]);
    if (it.kind === "arcade") px(X + 5, Y - 3 + (Math.floor(t * 3) % 4), 6, 2, ["#f43f5e", "#22d3ee", "#facc15", "#a3e635"][Math.floor(t) % 4]);
  }
}

const SKIN = ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"];

/** One character, feet at (x, y) in logical pixels. */
export function drawCharacter(g: CanvasRenderingContext2D, x: number, y: number, o: {
  color: string; icon: string; facing: Facing; moving: boolean; t: number; dim?: boolean; player?: boolean; typing?: boolean; seed: number;
}) {
  const px = (dx: number, dy: number, w: number, h: number, col: string) => { g.fillStyle = col; g.fillRect(Math.round(x + dx), Math.round(y + dy), w, h); };
  const body = o.player ? "#e5e7eb" : o.color;
  const skin = SKIN[o.seed % SKIN.length];
  const walkFrame = o.moving ? Math.floor(o.t * 8) % 2 : 0;
  const bob = o.moving ? (walkFrame ? -1 : 0) : o.typing ? (Math.floor(o.t * 6) % 2 ? -0.5 : 0) : 0;
  g.save();
  if (o.dim) g.globalAlpha = 0.45;
  g.globalAlpha *= 0.35;
  g.fillStyle = "#000";
  g.beginPath(); g.ellipse(x, y, 5, 2, 0, 0, Math.PI * 2); g.fill();
  g.globalAlpha = o.dim ? 0.45 : 1;
  // legs
  const legCol = o.player ? "#334155" : shade(o.color, -0.6);
  px(-3, -4 + (walkFrame ? -1 : 0), 2, 4, legCol);
  px(1, -4 + (walkFrame ? 0 : -1), 2, 4, legCol);
  // body
  px(-4, -10 + bob, 8, 7, body);
  px(-4, -4 + bob, 8, 1, shade(o.player ? "#e5e7eb" : o.color, -0.35));
  if (o.player) px(-1, -9 + bob, 2, 5, "#8b5cf6");
  // arms
  const armSwing = o.moving ? (walkFrame ? 1 : -1) : 0;
  const armType = o.typing ? (Math.floor(o.t * 8) % 2) : 0;
  px(-5, -9 + bob + armSwing, 1, 5 - armType, o.facing === "up" ? body : skin);
  px(4, -9 + bob - armSwing, 1, 5 - (1 - armType), o.facing === "up" ? body : skin);
  // head
  px(-3, -16 + bob, 6, 6, skin);
  if (o.facing !== "up") {
    const ex = o.facing === "left" ? -2 : o.facing === "right" ? 1 : -2;
    px(ex, -13 + bob, 1, 1, "#111827");
    if (o.facing === "down") px(1, -13 + bob, 1, 1, "#111827");
  }
  // headgear by role
  const hat = o.player ? "#8b5cf6" : shade(o.color, -0.2);
  switch (o.player ? "player" : o.icon) {
    case "crown":
      px(-3, -18 + bob, 6, 2, "#facc15"); px(-3, -19 + bob, 1, 1, "#facc15"); px(0, -20 + bob, 1, 2, "#facc15"); px(2, -19 + bob, 1, 1, "#facc15");
      px(0, -18 + bob, 1, 1, "#f43f5e");
      break;
    case "calendar":
      px(-3, -17 + bob, 6, 2, hat); px(o.facing === "left" ? -5 : 2, -15 + bob, 3, 1, hat);
      break;
    case "folder":
      px(-3, -17 + bob, 6, 2, hat); if (o.facing !== "up") px(-3, -14 + bob, 6, 1, "#67e8f9");
      break;
    case "search":
      px(-3, -17 + bob, 6, 2, hat); px(0, -20 + bob, 1, 3, "#9ca3af"); px(-1, -21 + bob, 3, 1, "#34d399");
      break;
    case "code":
      px(-3, -17 + bob, 6, 3, hat); px(-1, -18 + bob, 2, 1, hat); if (o.facing !== "up") px(-3, -13 + bob, 6, 1, "#111827");
      break;
    case "brain":
      g.globalAlpha *= 0.8; px(-4, -19 + bob, 8, 1, "#93c5fd"); g.globalAlpha = o.dim ? 0.45 : 1;
      px(-3, -17 + bob, 6, 1, hat);
      break;
    case "mail":
      px(-3, -17 + bob, 6, 2, hat); px(2, -19 + bob, 1, 2, "#fde68a");
      break;
    case "camera": // cap, and a camera on a strap
      px(-3, -17 + bob, 6, 2, hat); px(o.facing === "left" ? -5 : 2, -16 + bob, 3, 1, hat);
      if (o.facing !== "up") { px(-2, -8 + bob, 4, 3, "#1f2937"); px(-1, -7 + bob, 2, 1, "#93c5fd"); }
      break;
    case "film": // backwards cap and headset
      px(-3, -17 + bob, 6, 2, hat); px(o.facing === "right" ? -5 : 2, -16 + bob, 3, 1, hat);
      px(-4, -14 + bob, 1, 3, "#111827"); px(3, -14 + bob, 1, 3, "#111827");
      break;
    case "image": // beret
      px(-4, -18 + bob, 8, 2, hat); px(-2, -19 + bob, 5, 1, hat); px(0, -20 + bob, 1, 1, shade(o.color, -0.5));
      break;
    case "player":
      px(-3, -17 + bob, 6, 2, "#1f2937"); px(-4, -16 + bob, 1, 3, "#1f2937"); px(3, -16 + bob, 1, 3, "#1f2937");
      break;
    default:
      px(-3, -17 + bob, 6, 2, hat);
  }
  g.restore();
}

export function wrap(g: CanvasRenderingContext2D, text: string, maxW: number, maxLines = 3): string[] {
  const words = text.split(/\s+/).map((w) => (w.length > 24 ? `${w.slice(0, 22)}…` : w));
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (g.measureText(next).width > maxW && cur) {
      lines.push(cur);
      cur = w;
      if (lines.length === maxLines) break;
    } else cur = next;
  }
  if (lines.length < maxLines && cur) lines.push(cur);
  if (lines.length === maxLines && words.join(" ").length > lines.join(" ").length) lines[maxLines - 1] = lines[maxLines - 1].replace(/.{0,2}$/, "…");
  return lines;
}

export function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}
