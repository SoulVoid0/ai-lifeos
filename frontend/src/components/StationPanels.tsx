// Side panels for the Station: an agent's live view (now / chat / history) and the interactive props.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, Archive, BookOpen, Brain, CalendarDays, Cpu, Film, ExternalLink, FolderGit2, Gamepad2, ImageIcon, Inbox,
  MessageCircle, Network, Rocket, Search, Send, Star, ThumbsDown, ThumbsUp, Trophy, Workflow, Wrench, X,
} from "lucide-react";
import { api, type AgentStatus, type MediaItem, type Mission } from "../lib/api";
import { ago, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "./ApprovalCard";
import { AgentAvatar, BriefCard, EventIcon, STATE_LABEL, STATE_TONE, colorOf } from "./crew";
import { GenerateForm, MediaAlbum, MediaView } from "./media";
import { PersonalityEditor } from "./Personality";
import { Empty, Spinner, notifyChanged, useToast } from "./ui";

type Chat = { role: "user" | "assistant"; content: string }[];
const chats = new Map<string, Chat>();

function Shell({ title, icon, onClose, children, footer }: {
  title: ReactNode; icon?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode;
}) {
  return (
    <div className="absolute inset-y-0 right-0 flex w-full max-w-sm flex-col border-l border-line bg-bg/95 shadow-2xl backdrop-blur" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-start gap-3 border-b border-line p-3">
        {icon}
        <div className="min-w-0 flex-1">{title}</div>
        <button className="btn-ghost" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">{children}</div>
      {footer && <div className="border-t border-line p-3">{footer}</div>}
    </div>
  );
}

const Stars = ({ score }: { score: number | null }) => score == null ? null : (
  <span className="inline-flex items-center gap-0.5 text-warn">{Array.from({ length: 5 }, (_, i) => (
    <Star key={i} size={10} fill={i < Math.round(score) ? "currentColor" : "none"} />
  ))}</span>
);

// ---------------------------------------------------------------- agents

export function AgentPanel({ agent, onClose }: { agent: AgentStatus; onClose: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"now" | "chat" | "history" | "profile">(agent.state === "idle" ? "chat" : "now");
  // Chat mode is the default: talking never starts work. Task mode has to be picked on purpose.
  const [mode, setMode] = useState<"chat" | "task">("chat");
  const hello = agent.catchphrases?.[0] ? `${agent.catchphrases[0]} ` : "";
  const greeting: Chat = [{ role: "assistant", content: `${hello}I'm ${agent.name}, the ${agent.role.toLowerCase()}. ${agent.description}` }];
  const [chat, setChat] = useState<Chat>(() => chats.get(agent.slug) ?? greeting);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const act = useAsync(() => api.agentActivity(agent.slug), [agent.slug], 2000);
  const missionId = agent.current?.mission_id;
  const mission = useAsync<Mission | null>(() => (missionId ? api.mission(missionId) : Promise.resolve(null)), [missionId], 2000);
  const briefs = (mission.data?.briefs ?? []).filter((b) => b.status === "open" && b.agent === agent.slug);
  const approvals = agent.state === "waiting" ? mission.data?.pending_actions ?? [] : [];
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setChat(chats.get(agent.slug) ?? greeting);
    setTab(agent.state === "idle" ? "chat" : "now");
    setMode("chat");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.slug]);
  useEffect(() => { chats.set(agent.slug, chat); endRef.current?.scrollIntoView({ block: "end" }); }, [chat, agent.slug, tab]);

  const say = async () => {
    const msg = text.trim();
    if (!msg || busy) return;
    const next: Chat = [...chat, { role: "user", content: msg }];
    setChat(next);
    setText("");
    setTab("chat");
    setBusy(true);
    try {
      const r = await api.chatAgent(agent.slug, msg, chat.slice(-10));
      setChat([...next, { role: "assistant", content: r.reply }]);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const giveTask = async () => {
    const goal = text.trim();
    if (mode !== "task" || !goal || busy) return;
    setBusy(true);
    try {
      const m = await api.startMission(goal, agent.slug);
      setChat([...chat, { role: "user", content: `Task: ${goal}` },
        { role: "assistant", content: `On it. Mission #${m.id} started; watch me head to my desk.` }]);
      setText("");
      setMode("chat");
      setTab("now");
      notifyChanged();
    } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); }
  };

  const a = act.data;
  const stats = a?.stats;
  return (
    <Shell onClose={onClose}
      icon={<AgentAvatar icon={agent.icon} color={agent.color} state={agent.state} size={40} />}
      title={<>
        <div className="font-semibold">{agent.name} <span className="text-xs font-normal text-muted">{agent.role}</span></div>
        <div className={`text-xs ${STATE_TONE[agent.state]}`}>{STATE_LABEL[agent.state]}</div>
        <div className="mt-0.5 flex items-center gap-1 truncate font-mono text-[10px] text-muted">
          <Cpu size={10} /> {agent.effective_model}
          {agent.model_missing && <span className="chip border-warn/40 px-1 py-0 text-[9px] text-warn" title={`${agent.wanted_model} isn't pulled yet`}>fallback</span>}
        </div>
        <div className="mt-2 flex gap-1">
          {(["now", "chat", "history", "profile"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-md px-2 py-0.5 text-xs ${tab === t ? "bg-accent text-white" : "text-muted hover:bg-panel-2"}`}>
              {{ now: "Now", chat: "Chat", history: "History", profile: "Personality" }[t]}
            </button>
          ))}
        </div>
      </>}
      footer={<>
        <div className="mb-2 flex items-center gap-1 rounded-lg border border-line p-0.5 text-xs" role="radiogroup" aria-label="Mode">
          <button role="radio" aria-checked={mode === "chat"} onClick={() => setMode("chat")}
            className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 ${mode === "chat" ? "bg-accent text-white" : "text-muted hover:bg-panel-2"}`}>
            <MessageCircle size={12} /> Chat only
          </button>
          <button role="radio" aria-checked={mode === "task"} onClick={() => setMode("task")} disabled={agent.state === "offline"}
            className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 ${mode === "task" ? "bg-warn text-black" : "text-muted hover:bg-panel-2"}`}>
            <Rocket size={12} /> Give a task
          </button>
        </div>
        {mode === "task" && (
          <div className="mb-2 rounded-lg border border-warn/50 bg-warn/10 px-2 py-1 text-[11px] text-warn">
            Task mode: {agent.name} will start working on what you type here, using its tools (changes still ask for approval).
          </div>
        )}
        <textarea className={`input min-h-16 text-sm ${mode === "task" ? "border-warn/60" : ""}`}
          placeholder={mode === "chat" ? `Chat with ${agent.name}. Nothing gets done in chat mode.` : `Describe the task for ${agent.name}…`} value={text}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (mode === "chat") say(); }
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && mode === "task") giveTask();
          }} />
        <div className="mt-2">
          {mode === "chat"
            ? <button className="btn-outline w-full" onClick={say} disabled={busy || !text.trim()}><MessageCircle size={14} /> Send</button>
            : <button className="btn-primary w-full" onClick={giveTask} disabled={busy || !text.trim()}><Rocket size={14} /> Start task (Ctrl+Enter)</button>}
        </div>
      </>}>
      {briefs.map((b) => <BriefCard key={b.id} brief={b} agentName={agent.name} onAnswered={() => { mission.reload(); notifyChanged(); }} />)}
      {approvals.map((x) => <ApprovalCard key={x.id} action={x} onChange={() => { mission.reload(); notifyChanged(); }} />)}

      {tab === "now" && (
        <>
          {agent.current ? (
            <div className="rounded-lg border border-line bg-panel p-2.5 text-xs">
              <div className="text-muted">Working on</div>
              <div className="font-medium">{agent.current.title}</div>
              <div className="mt-0.5 text-muted">for “{agent.current.goal}”</div>
              {a?.task && (
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-muted">
                  <span>step {a.task.steps}/{agent.max_steps}</span>
                  {a.task.model && <span className="font-mono">{a.task.model}</span>}
                  {a.task.started_at && <span>started {ago(a.task.started_at)}</span>}
                </div>
              )}
              {a?.task && (
                <div className="mt-1.5 h-1 overflow-hidden rounded bg-panel-2">
                  <div className="h-full bg-accent transition-all" style={{ width: `${Math.min(100, (a.task.steps / Math.max(1, agent.max_steps)) * 100)}%` }} />
                </div>
              )}
              <button className="btn-ghost mt-1 px-0 text-xs text-accent" onClick={() => navigate(`/crew?mission=${agent.current!.mission_id}`)}>
                <ExternalLink size={12} /> Open mission #{agent.current.mission_id}
              </button>
            </div>
          ) : (
            <div className="rounded-lg border border-line bg-panel p-2.5 text-xs text-muted">
              {agent.state === "offline" ? "Disabled in the Control Center." : "Idle. Give me a task below, or talk to me."}
              {a?.recent_tasks[0] && <div className="mt-1">Last: <span className="text-text">{a.recent_tasks[0].title}</span> ({a.recent_tasks[0].status}, {ago(a.recent_tasks[0].finished_at)})</div>}
            </div>
          )}
          {a?.last_tool && agent.current && (
            <div className="rounded-lg border border-line bg-panel p-2.5 text-xs">
              <div className="mb-0.5 flex items-center gap-1 text-muted"><Wrench size={11} /> Last tool call · {ago(a.last_tool.created_at)}</div>
              <div className="[overflow-wrap:anywhere]">{a.last_tool.summary}</div>
            </div>
          )}
          <div>
            <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted"><Activity size={12} /> Live log</div>
            {!a ? <Spinner /> : !a.log.length ? <div className="text-xs text-muted">Nothing yet.</div> : (
              <div className="space-y-1">
                {a.log.map((e) => (
                  <div key={e.id} className="flex items-start gap-1.5 text-xs">
                    <span className="mt-0.5"><EventIcon kind={e.kind} size={12} /></span>
                    <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{e.summary}</span>
                    <span className="shrink-0 text-[10px] text-muted">{ago(e.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {tab === "chat" && (
        <div className="space-y-2">
          {chat.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : ""}`}>
              <div className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-1.5 text-sm ${m.role === "user" ? "bg-accent text-white" : "border border-line bg-panel"}`}
                style={m.role === "assistant" ? { borderColor: `color-mix(in oklab, ${colorOf(agent.color)} 40%, var(--color-line))` } : undefined}>
                {m.content}
              </div>
            </div>
          ))}
          {busy && <div className="text-xs text-muted">{agent.name} is thinking…</div>}
          <div ref={endRef} />
        </div>
      )}

      {tab === "profile" && <PersonalityEditor agent={agent} />}

      {tab === "history" && (!a ? <Spinner /> : (
        <>
          {stats && (
            <div className="grid grid-cols-4 gap-1.5 text-center text-xs">
              {[["tasks", stats.tasks], ["done", stats.done], ["score", stats.avg_score?.toFixed(1) ?? "–"], ["lessons", stats.lessons]].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line bg-panel py-1.5"><div className="text-sm font-semibold">{v}</div><div className="text-[10px] text-muted">{k}</div></div>
              ))}
            </div>
          )}
          {!!a.media.length && (
            <div>
              <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted"><ImageIcon size={12} /> Made recently</div>
              <div className="grid grid-cols-4 gap-1">
                {a.media.map((m) => (
                  <button key={m.id} onClick={() => navigate("/gallery")} title={m.prompt}><MediaView m={m} className="aspect-square w-full rounded" /></button>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="mb-1 text-xs font-medium text-muted">Recent work</div>
            {!a.recent_tasks.length ? <div className="text-xs text-muted">No tasks yet.</div> : (
              <div className="space-y-1.5">
                {a.recent_tasks.map((t) => (
                  <button key={t.id} onClick={() => navigate(`/crew?mission=${t.mission_id}`)} className="block w-full rounded-lg border border-line bg-panel p-2 text-left text-xs hover:border-accent/50">
                    <div className="flex items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate font-medium">{t.title}</span>
                      <Stars score={t.score} />
                      {t.user_rating === 1 && <ThumbsUp size={10} className="text-ok" />}
                      {t.user_rating === -1 && <ThumbsDown size={10} className="text-danger" />}
                    </div>
                    <div className="text-[10px] text-muted">{t.status} · {t.model ?? "default model"} · {ago(t.finished_at)}</div>
                    {t.result && <div className="mt-1 line-clamp-3 text-muted">{t.result}</div>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted"><BookOpen size={12} /> What I've learned</div>
            {!a.lessons.length ? <div className="text-xs text-muted">No lessons yet. They come from reviews, failures and your ratings.</div> : (
              <ul className="list-disc space-y-1 pl-4 text-xs">{a.lessons.map((l) => <li key={l.id}>{l.text}</li>)}</ul>
            )}
          </div>
        </>
      ))}
    </Shell>
  );
}

// ---------------------------------------------------------------- props

const LINKS: Record<string, { title: string; icon: ReactNode; to: string; text: string; button: string }> = {
  research: { title: "Research terminal", icon: <Search size={18} />, to: "/search", text: "Search everything LifeOS knows: files, memories, tasks and notes. Scout uses the web from here.", button: "Open search" },
  files: { title: "Archive cabinets", icon: <Archive size={18} />, to: "/files", text: "Every indexed file from your approved folders. Archivist sorts and finds things here.", button: "Open Files" },
  projects: { title: "Project bench", icon: <FolderGit2 size={18} />, to: "/projects", text: "Your project folders with their recent files, open tasks and notes.", button: "Open Projects" },
  activity: { title: "Activity printer", icon: <Activity size={18} />, to: "/activity", text: "A printout of everything LifeOS and the crew did, with undo where possible.", button: "Open Activity" },
  automations: { title: "Automation dish", icon: <Workflow size={18} />, to: "/automations", text: "Scheduled and triggered workflows that run missions and tools for you.", button: "Open Automations" },
};

export function ObjectPanel({ use, agents, frames, onClose, onCompose }: {
  use: string; agents: AgentStatus[]; frames: MediaItem[]; onClose: () => void; onCompose: () => void;
}) {
  const navigate = useNavigate();
  const kind = use.split(":")[0];
  const link = LINKS[kind];
  if (link) {
    return (
      <Shell onClose={onClose} icon={<span className="text-accent">{link.icon}</span>} title={<div className="font-semibold">{link.title}</div>}>
        <p className="text-sm text-muted">{link.text}</p>
        <button className="btn-primary" onClick={() => navigate(link.to)}><ExternalLink size={14} /> {link.button}</button>
      </Shell>
    );
  }
  switch (kind) {
    case "missions": return <MissionBoard onClose={onClose} />;
    case "planning": return <PlanningBoard onClose={onClose} />;
    case "memory": return <MemoryShelf onClose={onClose} />;
    case "lessons": return <LessonOrb onClose={onClose} />;
    case "arcade": return <Arcade onClose={onClose} />;
    case "outbox": return <Outbox onClose={onClose} />;
    case "models": return <ModelRack agents={agents} onClose={onClose} />;
    case "easel": return <Easel onClose={onClose} />;
    case "frame": {
      const m = frames[Number(use.split(":")[1])];
      return (
        <Shell onClose={onClose} icon={<ImageIcon size={18} className="text-accent" />} title={<div className="font-semibold">Gallery</div>}>
          {!m ? <Empty>This frame is empty. Generate something at the Studio easel.</Empty> : (
            <>
              <MediaView m={m} className="w-full rounded-lg" controls />
              <div className="text-sm">{m.prompt}</div>
              <div className="text-xs text-muted">{m.kind} · {m.model ?? m.backend}{m.agent ? ` · by ${m.agent}` : ""} · {ago(m.created_at)}</div>
            </>
          )}
          <button className="btn-outline" onClick={() => navigate("/gallery")}><ExternalLink size={14} /> Open the Gallery</button>
        </Shell>
      );
    }
    case "album": return <AlbumOverlay kind={use.endsWith("video") ? "video" : "image"} agents={agents} onClose={onClose} />;
    case "desk": onCompose(); return null;
    default: return null;
  }
}

function MissionBoard({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const list = useAsync(() => api.missions(), [], 3000);
  const missions = (list.data ?? []).slice(0, 12);
  const active = missions.filter((m) => ["queued", "planning", "running", "waiting"].includes(m.status));
  const tone: Record<string, string> = { done: "text-ok", failed: "text-danger", cancelled: "text-muted", waiting: "text-warn" };
  return (
    <Shell onClose={onClose} icon={<Network size={18} className="text-accent" />}
      title={<><div className="font-semibold">Mission board</div><div className="text-xs text-muted">{active.length} active</div></>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/crew")}><ExternalLink size={14} /> Open the Control Center</button>}>
      {!list.data ? <Spinner /> : !missions.length ? <Empty>No missions yet. Use your desk to start one.</Empty> : missions.map((m) => (
        <button key={m.id} onClick={() => navigate(`/crew?mission=${m.id}`)} className="block w-full rounded-lg border border-line bg-panel p-2 text-left text-xs hover:border-accent/50">
          <div className="flex items-center gap-2">
            <span className="text-muted">#{m.id}</span>
            <span className="min-w-0 flex-1 truncate font-medium">{m.goal}</span>
            <span className={tone[m.status] ?? "text-accent"}>{m.status}</span>
          </div>
          {m.task_counts && <div className="mt-0.5 text-[10px] text-muted">{Object.entries(m.task_counts).map(([k, v]) => `${v} ${k}`).join(" · ")} · {ago(m.created_at)}</div>}
        </button>
      ))}
    </Shell>
  );
}

function PlanningBoard({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const tasks = useAsync(() => api.tasks("open"), []);
  const events = useAsync(() => {
    const now = new Date();
    return api.events(now.toISOString(), new Date(now.getTime() + 7 * 864e5).toISOString());
  }, []);
  return (
    <Shell onClose={onClose} icon={<CalendarDays size={18} className="text-accent" />} title={<div className="font-semibold">Planning board</div>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/calendar")}><ExternalLink size={14} /> Open the Calendar</button>}>
      <div className="text-xs font-medium text-muted">Next 7 days</div>
      {!(events.data ?? []).length ? <div className="text-xs text-muted">No events.</div> : (events.data ?? []).slice(0, 8).map((e) => (
        <div key={e.id} className="flex gap-2 text-xs"><span className="w-28 shrink-0 text-muted">{when(e.start_at)}</span><span className="truncate">{e.title}</span></div>
      ))}
      <div className="pt-2 text-xs font-medium text-muted">Open tasks</div>
      {!(tasks.data ?? []).length ? <div className="text-xs text-muted">Nothing open.</div> : (tasks.data ?? []).slice(0, 10).map((t) => (
        <div key={t.id} className="flex gap-2 text-xs"><span className="min-w-0 flex-1 truncate">{t.title}</span>{t.due_at && <span className="text-muted">{when(t.due_at)}</span>}</div>
      ))}
    </Shell>
  );
}

function MemoryShelf({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const list = useAsync(() => api.memories(q || undefined), [q]);
  return (
    <Shell onClose={onClose} icon={<Brain size={18} className="text-accent" />} title={<div className="font-semibold">Memory library</div>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/memory")}><ExternalLink size={14} /> Open Memory</button>}>
      <input className="input" placeholder="Search memories…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.stopPropagation()} />
      {!list.data ? <Spinner /> : !list.data.length ? <Empty>Nothing remembered yet.</Empty> : list.data.slice(0, 15).map((m) => (
        <div key={m.id} className="rounded-lg border border-line bg-panel p-2 text-xs">
          <div className="line-clamp-3">{m.content}</div>
          <div className="mt-0.5 text-[10px] text-muted">{m.kind} · {ago(m.created_at)}</div>
        </div>
      ))}
    </Shell>
  );
}

function LessonOrb({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const list = useAsync(() => api.lessons(), []);
  return (
    <Shell onClose={onClose} icon={<BookOpen size={18} className="text-accent" />}
      title={<><div className="font-semibold">Lesson orb</div><div className="text-xs text-muted">What the crew learned from reviews, failures and your ratings</div></>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/crew?tab=learning")}><ExternalLink size={14} /> Open Learning</button>}>
      {!list.data ? <Spinner /> : !list.data.length ? <Empty>No lessons yet.</Empty> : list.data.slice(0, 15).map((l) => (
        <div key={l.id} className="rounded-lg border border-line bg-panel p-2 text-xs">
          <div>{l.text}</div>
          <div className="mt-0.5 text-[10px] text-muted">{l.agent} · {l.source} · used {l.uses}×</div>
        </div>
      ))}
    </Shell>
  );
}

function Arcade({ onClose }: { onClose: () => void }) {
  const list = useAsync(() => api.scorecard(), []);
  const rows = [...(list.data ?? [])].filter((c) => c.tasks > 0)
    .sort((a, b) => (b.avg_score ?? 0) - (a.avg_score ?? 0) || b.done - a.done);
  return (
    <Shell onClose={onClose} icon={<Gamepad2 size={18} className="text-accent" />} title={<div className="font-semibold">Arcade: crew high scores</div>}>
      <div className="rounded-lg border-2 border-[#4c1d95] bg-[#0b0718] p-3 font-mono text-xs">
        <div className="mb-2 text-center text-[#facc15]">★ HIGH SCORES ★</div>
        {!list.data ? <Spinner /> : !rows.length ? <div className="text-center text-[#a78bfa]">INSERT MISSION TO PLAY</div> : rows.map((c, i) => (
          <div key={c.slug} className="flex gap-2" style={{ color: colorOf(c.color) }}>
            <span className="w-4">{i + 1}.</span>
            <span className="flex-1">{c.name.toUpperCase()}</span>
            <span>{(c.avg_score ?? 0).toFixed(1)}</span>
            <span className="w-14 text-right">{c.done}/{c.tasks}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1 text-xs text-muted"><Trophy size={12} /> Average review score (1–5) and tasks completed.</div>
    </Shell>
  );
}

function Outbox({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const list = useAsync(() => api.outbox(), []);
  return (
    <Shell onClose={onClose} icon={<Inbox size={18} className="text-accent" />} title={<div className="font-semibold">Outbox</div>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/crew?tab=outbox")}><ExternalLink size={14} /> Open the outbox</button>}>
      {!list.data ? <Spinner /> : !list.data.length ? <Empty>No reports or drafts yet.</Empty> : list.data.slice(0, 15).map((f) => (
        <button key={f.name} onClick={() => api.openOutbox(f.name)} className="block w-full truncate rounded-lg border border-line bg-panel p-2 text-left text-xs hover:border-accent/50">
          {f.name}<div className="text-[10px] text-muted">{ago(f.modified_at)}</div>
        </button>
      ))}
    </Shell>
  );
}

function ModelRack({ agents, onClose }: { agents: AgentStatus[]; onClose: () => void }) {
  const navigate = useNavigate();
  const llm = useAsync(() => api.llmStatus(), []);
  return (
    <Shell onClose={onClose} icon={<Cpu size={18} className="text-accent" />}
      title={<><div className="font-semibold">Model rack</div><div className="text-xs text-muted">{llm.data ? (llm.data.ok ? `Ollama: ${llm.data.models.length} model(s)` : "Ollama isn't reachable") : "checking…"}</div></>}
      footer={<button className="btn-primary w-full" onClick={() => navigate("/crew?tab=crew")}><ExternalLink size={14} /> Change models</button>}>
      {agents.map((a) => (
        <div key={a.slug} className="flex items-center gap-2 text-xs">
          <AgentAvatar icon={a.icon} color={a.color} size={22} />
          <span className="w-20 truncate">{a.name}</span>
          <span className="min-w-0 flex-1 truncate font-mono text-muted">{a.effective_model}</span>
          {a.model_missing && <span className="chip border-warn/40 text-[10px] text-warn" title={`Pull ${a.wanted_model} to use it`}>needs pull</span>}
        </div>
      ))}
    </Shell>
  );
}

function Easel({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  const status = useAsync(() => api.mediaStatus(), []);
  const recent = useAsync(() => api.media(undefined, 6), [], 3000);
  const [idea, setIdea] = useState("");
  const askMuse = async () => {
    if (!idea.trim()) return;
    try {
      const m = await api.startMission(idea.trim(), null);
      toast(`The studio is on it (mission #${m.id})`);
      setIdea("");
      notifyChanged();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <Shell onClose={onClose} icon={<ImageIcon size={18} className="text-accent" />}
      title={<><div className="font-semibold">Studio easel</div><div className={`text-xs ${status.data?.ok ? "text-ok" : "text-warn"}`}>{status.data?.detail ?? "checking the backend…"}</div></>}
      footer={<button className="btn-outline w-full" onClick={() => navigate("/gallery")}><ExternalLink size={14} /> Open the Gallery</button>}>
      <GenerateForm compact videoOk={!status.data || status.data.video} onStarted={() => recent.reload()} />
      <div className="border-t border-line pt-3">
        <div className="mb-1 text-xs text-muted">Or brief the studio: Atlas has Muse plan it and Lumen or Reel make it.</div>
        <div className="flex gap-2">
          <input className="input" placeholder="3 album covers for a lo-fi playlist" value={idea} onChange={(e) => setIdea(e.target.value)}
            onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") askMuse(); }} />
          <button className="btn-primary" onClick={askMuse} disabled={!idea.trim()}><Send size={14} /></button>
        </div>
      </div>
      {!!recent.data?.length && (
        <div className="grid grid-cols-3 gap-1">{recent.data.map((m) => <MediaView key={m.id} m={m} className="aspect-square w-full rounded" />)}</div>
      )}
    </Shell>
  );
}

function AlbumOverlay({ kind, agents, onClose }: { kind: "image" | "video"; agents: AgentStatus[]; onClose: () => void }) {
  const creators = agents.filter((a) => a.tools.some((t) => t.startsWith("generate_")) || ["creative", "photo", "video"].includes(a.slug));
  return (
    <div className="absolute inset-0 z-10 flex flex-col bg-bg/95 p-4 backdrop-blur" onClick={(e) => e.stopPropagation()}>
      <div className="mb-3 flex items-center gap-2">
        {kind === "image" ? <ImageIcon size={18} className="text-accent" /> : <Film size={18} className="text-accent" />}
        <div className="font-semibold">{kind === "image" ? "Photo album" : "Video album"}</div>
        <div className="text-xs text-muted">{kind === "image" ? "Everything Lumen, Muse and you have made" : "Every clip Reel has rendered"}</div>
        <button className="btn-ghost ml-auto" onClick={onClose} aria-label="Close"><X size={16} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <MediaAlbum kind={kind} creators={creators} columns="grid-cols-2 sm:grid-cols-4 xl:grid-cols-6" />
      </div>
    </div>
  );
}
