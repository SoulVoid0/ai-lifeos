// Agent arena: two agents (or two models) answer the same prompt blind; you pick the better one and both learn from it.
import { useState } from "react";
import { Scale, Swords, Trash2, Trophy } from "lucide-react";
import { api, type ArenaMatch } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

export default function Arena() {
  const toast = useToast();
  const agents = useAsync(api.agents, []);
  const models = useAsync(api.llmStatus, []);
  const matches = useAsync(api.arena, [], 3000);
  const board = useAsync(api.arenaBoard, [], 15000);
  const [prompt, setPrompt] = useState("");
  const [a, setA] = useState({ agent: "research", model: "" });
  const [b, setB] = useState({ agent: "quick", model: "" });
  const [busy, setBusy] = useState(false);

  const start = async () => {
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      await api.startArena(prompt.trim(), { agent: a.agent, model: a.model || undefined }, { agent: b.agent, model: b.model || undefined });
      setPrompt("");
      matches.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const crew = (agents.data ?? []).filter((x) => x.enabled);
  const picker = (v: { agent: string; model: string }, set: (v: { agent: string; model: string }) => void, label: string) => (
    <div className="grid flex-1 gap-2">
      <label className="block text-xs text-muted">{label}
        <select className="input mt-1" value={v.agent} onChange={(e) => set({ ...v, agent: e.target.value })}>
          {crew.map((x) => <option key={x.slug} value={x.slug}>{x.name} ({x.role})</option>)}
        </select>
      </label>
      <label className="block text-xs text-muted">Model
        <select className="input mt-1" value={v.model} onChange={(e) => set({ ...v, model: e.target.value })}>
          <option value="">The agent's own model</option>
          {(models.data?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
    </div>
  );

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <PageHeader title="Arena" subtitle="Two contestants answer the same prompt without knowing who's who. Pick the better answer: the winner and loser each get a lesson they use from then on." />
      <Section title="New round" icon={<Swords size={15} className="text-accent" />}>
        <textarea className="input min-h-20" placeholder="e.g. Explain how a heat pump works to a 10-year-old" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <div className="mt-3 flex flex-wrap gap-3">
          {picker(a, setA, "Contestant 1")}
          {picker(b, setB, "Contestant 2")}
        </div>
        <p className="mt-2 text-xs text-muted">Pick the same agent twice with two models to see which model suits it. Answers are written without tools, so nothing gets done twice.</p>
        <div className="mt-3 flex justify-end">
          <button className="btn-primary" onClick={start} disabled={busy || !prompt.trim()}>{busy ? <Spinner /> : <Swords size={14} />} Fight</button>
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="space-y-4">
          {matches.data?.length === 0 && <Empty>No rounds yet.</Empty>}
          {(matches.data ?? []).map((m) => <Round key={m.id} m={m} onChange={() => { matches.reload(); board.reload(); }} />)}
        </div>
        <Section title="Leaderboard" icon={<Trophy size={15} className="text-accent" />} className="self-start">
          {!board.data?.rounds && <p className="text-sm text-muted">Decide a round to start the table.</p>}
          {(["agents", "models"] as const).map((k) => (board.data?.[k].length ?? 0) > 0 && (
            <div key={k} className="mb-3">
              <div className="mb-1 text-xs uppercase tracking-wide text-muted">{k}</div>
              {board.data![k].map((r, i) => (
                <div key={r.key} className="flex items-center gap-2 py-1 text-sm">
                  <span className="w-5 text-muted">{i + 1}.</span>
                  <span className="flex-1 truncate">{r.name}</span>
                  <span className="text-xs text-muted">{r.win}-{r.loss}{r.tie ? `-${r.tie}` : ""}</span>
                  <span className="w-10 text-right text-xs">{r.win_rate == null ? "" : `${Math.round(r.win_rate * 100)}%`}</span>
                </div>
              ))}
            </div>
          ))}
        </Section>
      </div>
    </div>
  );
}

function Round({ m, onChange }: { m: ArenaMatch; onChange: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [voting, setVoting] = useState(false);
  const vote = async (w: "a" | "b" | "tie") => {
    setVoting(true);
    try {
      const r = await api.voteArena(m.id, w, reason);
      toast(r.new_lessons?.length ? `Saved ${r.new_lessons.length} lesson(s)` : "Vote saved");
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setVoting(false);
    }
  };
  const side = (k: "a" | "b") => {
    const text = m[`${k}_result`], secs = m[`${k}_seconds`];
    const won = m.winner === k;
    return (
      <div className={`flex-1 rounded-lg border p-3 ${won ? "border-ok/50 bg-ok/5" : "border-line"}`}>
        <div className="mb-2 flex items-center gap-2 text-xs text-muted">
          <b className="text-text">{k === "a" ? "Answer 1" : "Answer 2"}</b>
          {m.status === "decided" && <span>· {m[`${k}_name`]} · {m[`${k}_model`]}</span>}
          {secs != null && <span className="ml-auto">{secs.toFixed(1)} s</span>}
          {won && <Trophy size={13} className="text-ok" />}
        </div>
        {text == null ? <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> thinking…</div>
          : <div className="max-h-96 overflow-y-auto whitespace-pre-wrap text-sm">{text}</div>}
        {m.status === "ready" && <button className="btn-outline mt-3 w-full justify-center" disabled={voting} onClick={() => vote(k)}>This one is better</button>}
      </div>
    );
  };
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-start gap-2">
        <div className="flex-1">
          <div className="text-sm font-medium">{m.prompt}</div>
          <div className="text-xs text-muted">{ago(m.created_at)} · {m.status === "decided" ? (m.winner === "tie" ? "tie" : "decided") : m.status}</div>
        </div>
        <button className="btn-ghost" title="Delete round" onClick={async () => { await api.deleteArena(m.id); onChange(); }}><Trash2 size={14} /></button>
      </div>
      {m.error && <p className="mb-2 text-sm text-danger">{m.error}</p>}
      <div className="flex flex-col gap-3 md:flex-row">{side("a")}{side("b")}</div>
      {m.status === "ready" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className="input flex-1" placeholder="Why? (optional, makes the lessons better)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn-ghost" disabled={voting} onClick={() => vote("tie")}><Scale size={14} /> Tie</button>
        </div>
      )}
      {m.status === "decided" && m.reason && <p className="mt-2 text-xs text-muted">Your reason: {m.reason}</p>}
    </section>
  );
}
