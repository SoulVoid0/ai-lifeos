// Header bits for the 0.2 features: the voice orb, the game-mode chip, the screen-question dialog (Ctrl+Shift+Space)
// and starting the live wallpaper with the app.
import { useEffect, useRef, useState } from "react";
import { Eye, Gamepad2, Mic, Send, X } from "lucide-react";
import { api, type ScreenAnswer } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { isDesktop, onDesktopEvent, setWallpaper } from "../lib/desktop";
import { Spinner, useToast } from "./ui";

export function VoiceOrb() {
  const toast = useToast();
  const st = useAsync(api.voice, [], 1500);
  const shown = useRef("");
  const l = st.data?.listener;
  useEffect(() => {
    const h = st.data?.history?.[0];
    if (!h) return;
    const key = `${h.at}|${h.heard}`;
    if (shown.current && shown.current !== key) toast(`“${h.heard}”\n${h.agent ? `${h.agent}: ` : ""}${h.reply.slice(0, 240)}`, "info");
    shown.current = key;
  }, [st.data?.history, toast]);
  if (!st.data) return null;
  const state = l?.state ?? "off";
  const tone = state === "heard" ? "bg-accent text-white animate-pulse"
    : state === "thinking" || state === "speaking" ? "bg-warn/20 text-warn" : state === "listening" ? "text-ok" : state === "error" ? "text-danger" : "text-muted";
  const title = state === "listening" ? `Listening for “${st.data.config.wake_phrase}”. Click to talk now.`
    : state === "off" ? "Click to give a voice command (turn on the wake word in Settings → Voice)" : `Voice: ${state}${l?.heard ? ` · heard “${l.heard}”` : ""}`;
  return (
    <button className={`btn-ghost relative rounded-full ${tone}`} title={title} aria-label="Voice command"
      onClick={() => api.listen().catch((e) => toast((e as Error).message, "error"))}>
      <Mic size={16} />
      {state === "listening" && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-ok" />}
    </button>
  );
}

export function GameChip() {
  const g = useAsync(api.gameMode, [], 4000);
  if (!g.data?.active) return null;
  return (
    <button className="chip border-accent/40 text-accent" title="Game mode: AI models are off the GPU. Click to end it."
      onClick={async () => g.setData(await api.setGameMode(false))}>
      <Gamepad2 size={12} className="mr-1 inline" /> Game mode{g.data.game ? `: ${g.data.game}` : ""}
    </button>
  );
}

export function ScreenAsk() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [shot, setShot] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [answers, setAnswers] = useState<ScreenAnswer[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const show = async (capture: boolean) => {
    try {
      if (capture) await api.captureScreen();
      const url = await api.screenshotBlob();
      setShot((old) => { if (old) URL.revokeObjectURL(old); return url; });
    } catch (e) {
      setShot(null);
      if (capture) toast((e as Error).message, "error");
    }
    setAnswers([]);
    setOpen(true);
    setTimeout(() => inputRef.current?.focus(), 50);
  };
  useEffect(() => onDesktopEvent("screen-ask", () => { show(false); }), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const h = () => show(true);
    window.addEventListener("lifeos:screen-ask", h);
    return () => window.removeEventListener("lifeos:screen-ask", h);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try {
      const a = await api.askScreen(q.trim(), false);
      setAnswers((x) => [...x, a]);
      setQ("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  if (!open) return (
    <button className="btn-ghost" title="Ask about your screen (Ctrl+Shift+Space)" aria-label="Ask about your screen" onClick={() => show(true)}><Eye size={16} /></button>
  );
  return (
    <>
      <button className="btn-ghost text-accent" aria-label="Ask about your screen"><Eye size={16} /></button>
      <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-6" onClick={() => setOpen(false)}>
        <div className="card flex max-h-full w-full max-w-3xl flex-col gap-3 p-4" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2">
            <Eye size={16} className="text-accent" /><span className="flex-1 text-sm font-semibold">Ask about your screen</span>
            <button className="btn-ghost text-xs" onClick={() => show(true)} title="Take a new screenshot">Retake</button>
            <button className="btn-ghost" onClick={() => setOpen(false)} aria-label="Close"><X size={14} /></button>
          </div>
          {shot && <img src={shot} alt="Your screen" className="max-h-72 rounded-lg border border-line object-contain" />}
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
            {answers.map((a, i) => (
              <div key={i} className="text-sm">
                <div className="text-xs text-muted">{a.question}</div>
                <div className="whitespace-pre-wrap">{a.answer}</div>
                <div className="text-[11px] text-muted">{a.model} · {a.seconds.toFixed(1)} s</div>
              </div>
            ))}
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(); }}>
            <input ref={inputRef} className="input flex-1" placeholder="What does this error mean? Summarize this page. Which button do I press?" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn-primary" disabled={busy || !q.trim()}>{busy ? <Spinner /> : <Send size={14} />}</button>
          </form>
        </div>
      </div>
    </>
  );
}

/** Put the live wallpaper back on the desktop when the app starts, if it's turned on. */
export function useWallpaperAutostart() {
  useEffect(() => {
    if (!isDesktop) return;
    api.wallpaper().then((w) => { if (w.enabled) setWallpaper(true); }).catch(() => {});
  }, []);
}
