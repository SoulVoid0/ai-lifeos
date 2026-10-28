import { useEffect, useRef, useState } from "react";
import {
  ArrowRight, Bot, Brain, CalendarDays, ChartColumn, Check, CircleCheck, CircleHelp, CircleStop, Code, Crown, Eye,
  BookOpen, Flag, Folder, Gauge, GraduationCap, Route, Info, ListTree, Mail, MessageSquare, Mic, MicOff, PenLine, Reply, Rocket, Search, Shield,
  ShieldAlert, SkipForward, Sparkles, TriangleAlert, Wrench, type LucideIcon,
} from "lucide-react";
import type { AgentState, Brief, MissionEvent } from "../lib/api";
import { api } from "../lib/api";
import { useToast } from "./ui";

export const AGENT_COLORS: Record<string, string> = {
  violet: "#8b5cf6", amber: "#f59e0b", cyan: "#06b6d4", emerald: "#10b981", rose: "#f43f5e", blue: "#3b82f6",
  orange: "#f97316", pink: "#ec4899", lime: "#84cc16", slate: "#94a3b8",
};

export const AGENT_ICONS: Record<string, LucideIcon> = {
  crown: Crown, calendar: CalendarDays, folder: Folder, search: Search, code: Code, brain: Brain, mail: Mail, bot: Bot,
  pen: PenLine, shield: Shield, chart: ChartColumn, sparkles: Sparkles,
};

export const STATE_LABEL: Record<AgentState, string> = {
  idle: "Idle", working: "Working", waiting: "Needs approval", asking: "Has a question", offline: "Disabled",
};

export const STATE_TONE: Record<AgentState, string> = {
  idle: "text-muted", working: "text-ok", waiting: "text-warn", asking: "text-warn", offline: "text-muted/60",
};

export const colorOf = (c?: string | null) => AGENT_COLORS[c ?? ""] ?? AGENT_COLORS.slate;

export function AgentAvatar({ icon, color, state, size = 40 }: { icon: string; color: string; state?: AgentState; size?: number }) {
  const Icon = AGENT_ICONS[icon] ?? Bot;
  const hex = colorOf(color);
  const active = state === "working";
  const attention = state === "waiting" || state === "asking";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {(active || attention) && (
        <span
          className="absolute inset-0 animate-ping rounded-full opacity-40"
          style={{ background: attention ? "var(--color-warn)" : hex, animationDuration: active ? "1.6s" : "1.1s" }}
        />
      )}
      <div
        className="relative grid h-full w-full place-items-center rounded-full border-2"
        style={{
          borderColor: state === "offline" ? "var(--color-line)" : hex,
          background: `color-mix(in oklab, ${hex} ${state === "offline" ? 6 : 18}%, var(--color-panel))`,
          color: state === "offline" ? "var(--color-muted)" : hex,
        }}
      >
        <Icon size={Math.round(size * 0.45)} />
      </div>
    </div>
  );
}

const EVENT_ICONS: Record<string, LucideIcon> = {
  mission: Rocket, plan: ListTree, delegate: ArrowRight, thought: MessageSquare, tool: Wrench, approval: ShieldAlert,
  decision: Check, brief: CircleHelp, answer: Reply, result: CircleCheck, error: TriangleAlert, skip: SkipForward,
  fallback: Info, done: Flag, cancel: CircleStop, cancelled: CircleStop, review: Eye, limit: Gauge,
  lessons: BookOpen, learn: GraduationCap, revise: PenLine, route: Route,
};

export const EVENT_TONE: Record<string, string> = {
  error: "text-danger", approval: "text-warn", brief: "text-warn", done: "text-ok", result: "text-ok",
  cancelled: "text-muted", cancel: "text-muted", thought: "text-muted", learn: "text-ok", revise: "text-warn",
};

export function EventIcon({ kind, size = 13 }: { kind: string; size?: number }) {
  const Icon = EVENT_ICONS[kind] ?? Info;
  return <Icon size={size} className={`shrink-0 ${EVENT_TONE[kind] ?? "text-accent"}`} />;
}

/** Live mission events: polls with an id cursor and keeps the last `keep` events. */
export function useMissionEvents(missionId?: number, keep = 300, intervalMs = 1000) {
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const cursor = useRef(0);
  useEffect(() => {
    cursor.current = 0;
    setEvents([]);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const batch = await api.missionEvents(cursor.current, missionId);
        if (!stop && batch.length) {
          cursor.current = batch[batch.length - 1].id;
          setEvents((prev) => [...prev, ...batch].slice(-keep));
        }
      } catch {
        /* backend offline: the layout banner says so */
      }
      if (!stop) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [missionId, keep, intervalMs]);
  return events;
}

export function BriefCard({ brief, agentName, onAnswered }: { brief: Brief; agentName: string; onAnswered?: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const answer = async (value: string) => {
    if (!value.trim()) return;
    setBusy(true);
    try {
      await api.answerBrief(brief.id, value.trim());
      onAnswered?.();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-xl border border-warn/40 bg-warn/5 p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-warn">
        <CircleHelp size={13} /> Task brief from {agentName} · mission #{brief.mission_id}
      </div>
      <div className="text-sm">{brief.question}</div>
      {brief.options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {brief.options.map((o) => (
            <button key={o} className="btn-outline text-xs" disabled={busy} onClick={() => answer(o)}>{o}</button>
          ))}
        </div>
      )}
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); answer(text); }}>
        <input className="input py-1.5" placeholder="Or type an answer…" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn-primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  );
}

type SpeechCtor = new () => {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null; start: () => void; stop: () => void;
};

/** Push-to-talk dictation through the Web Speech API, when the webview supports it. */
export function MicButton({ onText }: { onText: (t: string) => void }) {
  const Ctor = (window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor }).SpeechRecognition
    ?? (window as unknown as { webkitSpeechRecognition?: SpeechCtor }).webkitSpeechRecognition;
  const [on, setOn] = useState(false);
  const rec = useRef<InstanceType<SpeechCtor> | null>(null);
  if (!Ctor) return null;
  const toggle = () => {
    if (on) {
      rec.current?.stop();
      return;
    }
    const r = new Ctor();
    r.lang = navigator.language || "en-US";
    r.interimResults = false;
    r.continuous = false;
    r.onresult = (e) => onText(Array.from(e.results).map((x) => x[0].transcript).join(" "));
    r.onend = () => setOn(false);
    r.onerror = () => setOn(false);
    rec.current = r;
    r.start();
    setOn(true);
  };
  return (
    <button type="button" className={`btn-ghost ${on ? "text-danger" : ""}`} onClick={toggle} title="Dictate (voice)">
      {on ? <MicOff size={16} /> : <Mic size={16} />}
    </button>
  );
}
