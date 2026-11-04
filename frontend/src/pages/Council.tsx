// Agent council: several agents debate a question, then vote. Advice only; one click turns the decision into a mission.
import { useEffect, useState } from "react";
import { Gavel, Landmark, Rocket, Square, Trash2 } from "lucide-react";
import { api, type CouncilSession } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { play } from "../lib/sfx";
import { AgentAvatar } from "../components/crew";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

export default function Council() {
  const toast = useToast();
  const agents = useAsync(api.agents, []);
  const defaults = useAsync(api.councilDefaults, []);
  const list = useAsync(api.councils, [], 3000);
  const [q, setQ] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [rounds, setRounds] = useState(2);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { if (defaults.data && !members.length) setMembers(defaults.data.members); }, [defaults.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const crew = (agents.data ?? []).filter((a) => a.enabled);
  const toggle = (slug: string) => setMembers((m) => (m.includes(slug) ? m.filter((x) => x !== slug) : [...m, slug]));
  const start = async () => {
    try {
      const s = await api.startCouncil(q.trim(), members, rounds);
      play("start");
      setQ("");
      setOpen(s.id);
      list.reload();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const sessions = list.data ?? [];
  const current = sessions.find((s) => s.id === open) ?? sessions[0];
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <PageHeader title="Council" subtitle="Put a hard decision to a panel of agents. They argue it out over a few rounds, then vote; the majority wins and the chair breaks ties. Nothing is done until you say so." />
      <Section title="Convene the council" icon={<Landmark size={15} className="text-accent" />}>
        <textarea className="input min-h-20" placeholder="e.g. Should I learn Rust or Go next, given I mostly build web backends?" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="mt-3 flex flex-wrap gap-2">
          {crew.map((a) => (
            <button key={a.slug} onClick={() => toggle(a.slug)}
              className={`flex items-center gap-2 rounded-full border px-2.5 py-1 text-sm ${members.includes(a.slug) ? "border-accent bg-accent-soft" : "border-line text-muted"}`}>
              <AgentAvatar icon={a.icon} color={a.color} size={18} /> {a.name}
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="text-xs text-muted">Rounds of debate
            <select className="input ml-2 inline w-20" value={rounds} onChange={(e) => setRounds(Number(e.target.value))}>
              {[1, 2, 3, 4].map((n) => <option key={n}>{n}</option>)}
            </select></label>
          <span className="text-xs text-muted">{members.length} member{members.length === 1 ? "" : "s"} (2 to {defaults.data?.max ?? 6})</span>
          <button className="btn-primary ml-auto" disabled={q.trim().length < 5 || members.length < 2} onClick={start}><Gavel size={14} /> Convene</button>
        </div>
      </Section>
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-1">
          {sessions.length === 0 && <Empty>No councils yet.</Empty>}
          {sessions.map((s) => (
            <button key={s.id} onClick={() => setOpen(s.id)}
              className={`w-full rounded-lg px-3 py-2 text-left text-sm ${current?.id === s.id ? "bg-accent-soft" : "hover:bg-panel-2"}`}>
              <div className="line-clamp-2">{s.question}</div>
              <div className="text-xs text-muted">{s.status} · {ago(s.created_at)}</div>
            </button>
          ))}
        </div>
        {current && <Session s={current} onChange={list.reload} />}
      </div>
    </div>
  );
}

function Session({ s, onChange }: { s: CouncilSession; onChange: () => void }) {
  const toast = useToast();
  const agents = useAsync(api.agents, []);
  const bySlug = Object.fromEntries((agents.data ?? []).map((a) => [a.slug, a]));
  const live = s.status === "running" || s.status === "voting";
  const total = s.agents.length * s.rounds;
  const rounds = [...new Set(s.transcript.map((t) => t.round))];
  const pick = s.user_choice ?? s.decision;
  return (
    <section className="card space-y-4 p-4">
      <div className="flex items-start gap-2">
        <h2 className="flex-1 text-base font-semibold">{s.question}</h2>
        {live && <button className="btn-ghost" onClick={async () => { await api.stopCouncil(s.id); onChange(); }}><Square size={14} /> Stop</button>}
        <button className="btn-ghost" title="Delete" onClick={async () => { await api.deleteCouncil(s.id); onChange(); }}><Trash2 size={14} /></button>
      </div>
      {s.error && <p className="text-sm text-danger">{s.error}</p>}
      {live && <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> {s.status === "voting" ? "Voting…" : `Debating: turn ${s.transcript.length + 1} of ${total}`}</div>}
      {rounds.map((r) => (
        <div key={r}>
          <div className="mb-2 text-xs uppercase tracking-wide text-muted">Round {r}</div>
          <div className="space-y-2">
            {s.transcript.filter((t) => t.round === r).map((t, i) => (
              <div key={i} className="flex gap-2">
                {bySlug[t.agent] && <AgentAvatar icon={bySlug[t.agent].icon} color={bySlug[t.agent].color} size={26} />}
                <div className="flex-1 rounded-lg bg-panel-2 p-2.5 text-sm">
                  <div className="mb-0.5 text-xs font-semibold">{s.names[t.agent] ?? t.agent}</div>
                  <div className="whitespace-pre-wrap">{t.text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      {s.options.length > 0 && (
        <div>
          <div className="mb-2 text-xs uppercase tracking-wide text-muted">The vote</div>
          {s.summary && <p className="mb-2 text-sm text-muted">{s.summary}</p>}
          <div className="space-y-2">
            {s.options.map((o, i) => {
              const n = s.tally[i] ?? 0;
              const won = s.decision?.startsWith(o);
              return (
                <div key={i} className={`rounded-lg border p-2.5 ${won ? "border-ok/50 bg-ok/5" : "border-line"}`}>
                  <div className="flex items-center gap-2 text-sm">
                    <span className="flex-1">{o}</span>
                    <span className="text-xs text-muted">{n} vote{n === 1 ? "" : "s"}</span>
                    {s.status === "decided" && (
                      <button className={`btn-ghost px-2 py-0.5 text-xs ${s.user_choice === o ? "text-accent" : ""}`}
                        onClick={async () => { await api.chooseCouncil(s.id, i); onChange(); }}>{s.user_choice === o ? "Your pick" : "Go with this"}</button>
                    )}
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-panel-2">
                    <div className="h-full bg-accent" style={{ width: `${(n / Math.max(1, s.votes.length)) * 100}%` }} />
                  </div>
                  <div className="mt-1 space-y-0.5">
                    {s.votes.filter((v) => v.choice === i).map((v) => (
                      <div key={v.agent} className="text-xs text-muted"><b className="text-text">{s.names[v.agent]}</b>: {v.reason}</div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {s.status === "decided" && pick && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-accent/40 bg-accent-soft p-3 text-sm">
          <Gavel size={15} className="text-accent" /> <b>Decision:</b> <span className="flex-1">{pick}</span>
          <button className="btn-primary" onClick={async () => {
            try { await api.councilMission(s.id); play("start"); toast("Mission started (Control Center)"); } catch (e) { toast((e as Error).message, "error"); }
          }}><Rocket size={14} /> Make it a mission</button>
        </div>
      )}
    </section>
  );
}
