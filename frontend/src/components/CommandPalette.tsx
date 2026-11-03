import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppWindow, Bot, CornerDownLeft, Search } from "lucide-react";
import { api, type AppEntry } from "../lib/api";
import { useOpenApp } from "../pages/Computer";

export type PaletteNav = { to: string; label: string };
type Item = { key: string; kind: "app" | "page" | "ask" | "search"; label: string; hint: string; run: () => void };

/** Ctrl+K (or Ctrl+Space): open an app, jump to a page, or hand the text to the assistant. */
export function CommandPalette({ nav }: { nav: PaletteNav[] }) {
  const navigate = useNavigate();
  const openApp = useOpenApp();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [apps, setApps] = useState<AppEntry[]>([]);
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "k" || e.code === "Space")) {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("lifeos:palette", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("lifeos:palette", onOpen); };
  }, []);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    const t = setTimeout(() => { api.apps(q.trim()).then((a) => live && setApps(a.slice(0, 8))).catch(() => {}); }, 80);
    return () => { live = false; clearTimeout(t); };
  }, [q, open]);

  const items = useMemo<Item[]>(() => {
    const s = q.trim();
    const low = s.toLowerCase();
    const close = (f: () => void) => () => { setOpen(false); f(); };
    const out: Item[] = apps.map((a) => ({ key: a.id, kind: "app", label: a.name, hint: "Open app", run: close(() => openApp(a)) }));
    for (const n of nav) {
      if (!low || n.label.toLowerCase().includes(low)) out.push({ key: n.to, kind: "page", label: n.label, hint: "Go to", run: close(() => navigate(n.to)) });
    }
    if (s) {
      out.unshift({ key: "ask", kind: "ask", label: s, hint: "Ask the assistant", run: close(() => navigate(`/assistant?prompt=${encodeURIComponent(s)}`)) });
      out.push({ key: "search", kind: "search", label: s, hint: "Search files, memories and tasks", run: close(() => navigate(`/search?q=${encodeURIComponent(s)}`)) });
      // An app name typed exactly goes first, ahead of "ask".
      const best = out.findIndex((it) => (it.kind === "app" || it.kind === "page") && it.label.toLowerCase().startsWith(low));
      if (best > 0) out.unshift(out.splice(best, 1)[0]);
    }
    return out.slice(0, 14);
  }, [apps, nav, q, navigate, openApp]);

  if (!open) return null;
  const icon = (k: Item["kind"]) => k === "app" ? <AppWindow size={15} /> : k === "ask" ? <Bot size={15} /> : <Search size={15} />;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[12vh]" onMouseDown={() => setOpen(false)}>
      <div className="card w-full max-w-xl overflow-hidden shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search size={16} className="text-muted" />
          <input ref={input} className="h-12 flex-1 bg-transparent text-sm outline-none" placeholder="Open an app, go to a page, or ask anything…"
            value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((i) => Math.min(items.length - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setSel((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter") { e.preventDefault(); items[sel]?.run(); }
            }} />
          <kbd className="chip text-[10px]">Esc</kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto p-1">
          {items.map((it, i) => (
            <li key={`${it.kind}-${it.key}`}>
              <button className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${i === sel ? "bg-accent-soft" : "hover:bg-panel-2"}`}
                onMouseEnter={() => setSel(i)} onClick={it.run}>
                <span className="text-muted">{icon(it.kind)}</span>
                <span className="min-w-0 flex-1 truncate">{it.label}</span>
                <span className="text-xs text-muted">{it.hint}</span>
                {i === sel && <CornerDownLeft size={12} className="text-muted" />}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
