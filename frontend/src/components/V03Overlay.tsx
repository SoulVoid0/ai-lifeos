// App-wide bits for the 0.3 features: mood lighting (colors and ambient music), sounds for events, the panic
// button and what happens when a panic is triggered from anywhere, and starting the desktop pet.
import { useEffect, useRef, useState } from "react";
import { OctagonX, ShieldCheck } from "lucide-react";
import { api, type Notification } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { isDesktop, panicHide, setPet } from "../lib/desktop";
import { ambient } from "../lib/music";
import { installClickSounds, loadSfxConfig, play } from "../lib/sfx";
import { useToast } from "./ui";

const THEME: Record<string, string> = { violet: "#8b5cf6", blue: "#3b82f6", emerald: "#10b981", rose: "#f43f5e", amber: "#f59e0b", cyan: "#06b6d4" };

/** Applies the mood's accent and glow (or the theme accent when mood colors are off) and runs the ambient music. */
export function MoodLayer({ themeAccent }: { themeAccent?: string }) {
  const mood = useAsync(api.mood, [], 30000);
  const m = mood.data;
  useEffect(() => {
    const root = document.documentElement.style;
    const useMood = m?.config.enabled && m.config.colors;
    root.setProperty("--accent", useMood ? m!.palette.accent : THEME[themeAccent ?? "violet"] ?? THEME.violet);
    root.setProperty("--mood-glow", useMood ? m!.palette.glow : "transparent");
  }, [m, themeAccent]);
  useEffect(() => {
    if (!m) return;
    ambient.update({ ...m.music, on: m.config.enabled && m.music.on });
  }, [m]);
  useEffect(() => {
    const reload = () => mood.reload();
    window.addEventListener("lifeos:changed", reload);
    return () => { window.removeEventListener("lifeos:changed", reload); ambient.stop(); };
  }, [mood.reload]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** Sounds for new notifications (a level-up gets its own fanfare) and, if turned on, button clicks. */
export function useEventSounds(notes: Notification[] | undefined) {
  const seen = useRef<Set<number> | null>(null);
  useEffect(() => { loadSfxConfig(); return installClickSounds(); }, []);
  useEffect(() => {
    if (!notes) return;
    if (!seen.current) { seen.current = new Set(notes.map((n) => n.id)); return; }
    const fresh = notes.filter((n) => !seen.current!.has(n.id));
    fresh.forEach((n) => seen.current!.add(n.id));
    if (!fresh.length) return;
    const kinds = fresh.map((n) => n.kind ?? "");
    play(kinds.includes("levelup") ? "levelup" : fresh.some((n) => /failed|problem|attention/i.test(n.title)) ? "error"
      : fresh.some((n) => /decided|finished|done|backed up/i.test(n.title)) ? "success" : "notify");
  }, [notes]);
}

/** The red button in the header, and the reaction to a panic started anywhere (hotkey, phone, voice). */
export function PanicButton() {
  const toast = useToast();
  const st = useAsync(api.panic, [], 2000);
  const was = useRef<boolean | null>(null);
  const [cover, setCover] = useState(false);
  const active = !!st.data?.active;

  useEffect(() => {
    if (was.current === null) { was.current = active; return; }
    if (active && !was.current) {
      play("panic");
      if (st.data?.config.hide_windows) {
        if (isDesktop) panicHide();
        else setCover(true);
      }
    }
    if (!active) setCover(false);
    was.current = active;
  }, [active, st.data?.config.hide_windows]);

  const fire = async () => {
    try {
      const r = await api.triggerPanic();
      st.setData(r);
      was.current = true;
      play("panic");
      if (r.hide_windows) { if (isDesktop) panicHide(); else setCover(true); }
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const clear = async () => {
    const r = await api.clearPanic();
    st.setData(r);
    setCover(false);
    toast("All clear. " + (r.automation_paused ? "Automation is still paused (you paused it yourself)." : "Automation is back on."), "ok");
  };
  return (
    <>
      {active ? (
        <button className="btn border border-danger/50 bg-danger/15 text-danger panic-pulse" onClick={clear} title="End panic mode">
          <ShieldCheck size={15} /> All clear
        </button>
      ) : (
        <button className="btn-ghost text-danger hover:bg-danger/10" onClick={fire} aria-label="Panic button"
          title="Panic: stop every mission and job, pause automation, free the GPU, mute and hide LifeOS (Ctrl+Shift+F12)">
          <OctagonX size={17} />
        </button>
      )}
      {cover && (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-[#05060a]">
          <button className="btn-outline" onClick={clear}><ShieldCheck size={15} /> All clear</button>
        </div>
      )}
    </>
  );
}

/** Opens the desktop pet window with the app when it's turned on. */
export function usePetAutostart() {
  useEffect(() => {
    if (!isDesktop) return;
    api.pet().then((p) => { if (p.enabled) setPet(true, p.size); }).catch(() => {});
  }, []);
}
