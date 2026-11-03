import { useState } from "react";
import { BookOpen, Download, GraduationCap, Pin, PinOff, Plus, Trash2, Trophy } from "lucide-react";
import { api, type AgentStatus, type Lesson } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { AgentAvatar } from "./crew";
import { Empty, Section, Spinner, useToast } from "./ui";

const SOURCE_LABEL: Record<Lesson["source"], string> = {
  review: "peer review", reflection: "reflection", failure: "failure", rejection: "you rejected", user: "you",
};

function Stars({ score }: { score: number | null }) {
  if (score === null) return <span className="text-muted">–</span>;
  return <span title={`${score} / 5`} className="font-mono text-xs">{score.toFixed(1)}<span className="text-muted">/5</span></span>;
}

/** How the crew improves: settings, the per-agent scorecard, the shared lesson playbook and training export. */
export function LearningTab({ agents }: { agents: Record<string, AgentStatus> }) {
  const toast = useToast();
  const overview = useAsync(api.learning, [], 5000);
  const card = useAsync(api.scorecard, [], 8000);
  const [filter, setFilter] = useState("");
  const lessons = useAsync(() => api.lessons(filter || undefined), [filter], 6000);
  const [draft, setDraft] = useState({ agent: "*", text: "" });

  const setSetting = async (patch: Record<string, boolean | number>) => {
    try { await api.updateSettings(patch); overview.reload(); } catch (e) { toast((e as Error).message, "error"); }
  };
  const add = async () => {
    if (!draft.text.trim()) return;
    try { await api.addLesson(draft.agent, draft.text.trim()); setDraft({ ...draft, text: "" }); lessons.reload(); overview.reload(); }
    catch (e) { toast((e as Error).message, "error"); }
  };
  const exportData = async () => {
    try {
      const text = await api.exportTraining(4);
      if (!text.trim()) { toast("No tasks scored 4+ yet. Run missions and rate results first.", "error"); return; }
      const url = URL.createObjectURL(new Blob([text], { type: "application/jsonl" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "lifeos-training.jsonl" });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast((e as Error).message, "error"); }
  };

  const o = overview.data;
  const name = (slug: string) => (slug === "*" ? "Whole crew" : agents[slug]?.name ?? slug);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
      <div className="space-y-4">
        <Section title="How the crew learns" icon={<GraduationCap size={15} className="text-accent" />}>
          {!o ? <Spinner /> : (
            <div className="space-y-2.5 text-sm">
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={o.settings.peer_review}
                  onChange={(e) => setSetting({ peer_review: e.target.checked })} />
                <span><b>Peer review.</b> Atlas scores each specialist's result 1–5 with feedback. A score of 2 or less sends it back for one revision.</span>
              </label>
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={o.settings.reflection}
                  onChange={(e) => setSetting({ reflection: e.target.checked })} />
                <span><b>Reflection.</b> After each mission Atlas writes up to 4 lessons for the agents involved or the whole crew. Rejected approvals and failures always become lessons.</span>
              </label>
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={o.settings.auto_route}
                  onChange={(e) => setSetting({ auto_route: e.target.checked })} />
                <span><b>Auto-routing.</b> Agents with extra models (edit an agent → "Other models to try") try each 3 times, then use the one with the best scores.</span>
              </label>
              <label className="flex items-center gap-2">
                <span>Lessons read per task</span>
                <input type="number" min={0} max={20} className="input w-20 py-1" value={o.settings.lessons_per_task}
                  onChange={(e) => setSetting({ lessons_per_task: Number(e.target.value) })} />
              </label>
              <div className="grid grid-cols-3 gap-2 pt-1 text-center">
                <div className="rounded-lg bg-panel-2 p-2"><div className="text-lg font-semibold">{o.lessons}</div><div className="text-[11px] text-muted">lessons</div></div>
                <div className="rounded-lg bg-panel-2 p-2"><div className="text-lg font-semibold">{o.reviewed_tasks}</div><div className="text-[11px] text-muted">scored tasks</div></div>
                <div className="rounded-lg bg-panel-2 p-2"><div className="text-lg font-semibold">{o.exportable}</div><div className="text-[11px] text-muted">training examples</div></div>
              </div>
              <button className="btn-outline w-full" onClick={exportData}><Download size={14} /> Export training data (JSONL)</button>
              <p className="text-[11px] text-muted">
                Model weights don't change here; the crew gets better through reviews, lessons and routing. To fine-tune for real,
                train a LoRA on this file offline (for example with Unsloth on a GPU), then load it in Ollama with a Modelfile
                <code className="mx-1">ADAPTER</code> line and set it as an agent's model.
              </p>
            </div>
          )}
        </Section>

        <Section title="Scorecard" icon={<Trophy size={15} className="text-accent" />}>
          {!card.data ? <Spinner /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr><th className="py-1">Agent</th><th className="text-right">Tasks</th><th className="text-right">Score</th><th className="text-right">Success</th><th className="text-right">👍/👎</th><th className="text-right">Lessons</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {card.data.map((a) => (
                  <tr key={a.slug} className="align-top">
                    <td className="py-1.5">
                      <div className="flex items-center gap-2"><AgentAvatar icon={a.icon} color={a.color} size={22} />{a.name}</div>
                      {a.models.length > 1 && (
                        <div className="mt-1 space-y-0.5 pl-7 text-[11px] text-muted">
                          {a.models.map((m) => <div key={m.model} className="flex justify-between gap-2 font-mono"><span className="truncate">{m.model}</span><span>{m.tasks}× · {m.avg_score ?? "–"}</span></div>)}
                        </div>
                      )}
                    </td>
                    <td className="py-1.5 text-right">{a.tasks}</td>
                    <td className="py-1.5 text-right"><Stars score={a.avg_score} /></td>
                    <td className="py-1.5 text-right text-muted">{a.success_rate === null ? "–" : `${Math.round(a.success_rate * 100)}%`}</td>
                    <td className="py-1.5 text-right text-muted">{a.thumbs_up}/{a.thumbs_down}</td>
                    <td className="py-1.5 text-right">{a.lessons}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      <Section title="Playbook (shared lessons)" icon={<BookOpen size={15} className="text-accent" />}
        actions={<select className="input w-36 py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All agents</option><option value="*">Whole crew</option>
          {Object.values(agents).map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
        </select>}>
        <div className="mb-3 flex gap-2">
          <select className="input w-36" value={draft.agent} onChange={(e) => setDraft({ ...draft, agent: e.target.value })}>
            <option value="*">Whole crew</option>
            {Object.values(agents).map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
          </select>
          <input className="input flex-1" placeholder="Teach a rule, e.g. Never schedule work after 22:00" value={draft.text}
            onChange={(e) => setDraft({ ...draft, text: e.target.value })} onKeyDown={(e) => e.key === "Enter" && add()} />
          <button className="btn-primary" onClick={add} aria-label="Add lesson"><Plus size={14} /></button>
        </div>
        {!(lessons.data ?? []).length && <Empty>No lessons yet. They appear after reviewed missions, rejected proposals, or when you rate a result.</Empty>}
        <ul className="divide-y divide-line">
          {(lessons.data ?? []).map((l) => (
            <li key={l.id} className="flex items-start gap-2 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm">{l.text}</div>
                <div className="mt-0.5 text-[11px] text-muted">
                  {name(l.agent)} · from {SOURCE_LABEL[l.source]}{l.mission_id ? ` (mission #${l.mission_id})` : ""} · used {l.uses}× · {ago(l.created_at)}
                </div>
              </div>
              <button className="btn-ghost px-1.5" title={l.pinned ? "Unpin" : "Pin: always read"}
                onClick={async () => { await api.updateLesson(l.id, { pinned: !l.pinned }); lessons.reload(); }}>
                {l.pinned ? <PinOff size={13} className="text-accent" /> : <Pin size={13} />}
              </button>
              <button className="btn-ghost px-1.5" title="Forget this lesson"
                onClick={async () => { await api.deleteLesson(l.id); lessons.reload(); overview.reload(); }}><Trash2 size={13} /></button>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
