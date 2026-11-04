// Crew stats: XP, levels and ranks, earned from real work (finished tasks, good reviews, arena wins, council votes).
import { Award, Clock, Flame, Star, Swords, Trophy } from "lucide-react";
import { api, type XpAgent } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { AgentAvatar } from "../components/crew";
import { PageHeader, Section, Spinner } from "../components/ui";

const hours = (s: number) => (s >= 3600 ? `${(s / 3600).toFixed(1)} h` : `${Math.round(s / 60)} min`);

export default function Stats() {
  const board = useAsync(api.xp, [], 20000);
  const b = board.data;
  if (!b) return <div className="p-8"><Spinner /></div>;
  const top = b.agents[0];
  const weekly = [...b.agents].sort((x, y) => y.week_xp - x.week_xp)[0];
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <PageHeader title="Crew stats" subtitle={`Crew level ${b.crew_level} · ${b.crew_xp.toLocaleString()} XP in total. XP comes only from real work, so it can't be farmed.`} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Tile icon={<Trophy size={16} />} label="Top of the crew" value={top ? `${top.name} · Lv ${top.level}` : "-"} sub={top?.rank} />
        <Tile icon={<Flame size={16} />} label="Hardest worker this week" value={weekly?.week_xp ? weekly.name : "Nobody yet"} sub={weekly?.week_xp ? `${weekly.week_xp} XP` : undefined} />
        <Tile icon={<Clock size={16} />} label="Time worked" value={hours(b.agents.reduce((n, a) => n + a.work_seconds, 0))}
          sub={`${b.agents.reduce((n, a) => n + a.tasks_done, 0)} tasks done`} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {b.agents.map((a, i) => <AgentCard key={a.slug} a={a} place={i + 1} />)}
      </div>
      <Section title="How XP is earned" icon={<Award size={15} className="text-accent" />}>
        <div className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {Object.entries({ task: "Finishing a task", task_good: "Bonus: reviewed 4+ out of 5", rated_high: "Bonus: you rated it 4-5 stars",
            task_failed: "A failed task (for trying)", arena_win: "Winning an arena round", arena_tie: "An arena tie",
            council: "Sitting on a council", council_majority: "Voting with the council's decision", lesson: "Learning a lesson" })
            .map(([k, label]) => <div key={k} className="flex justify-between"><span className="text-muted">{label}</span><b>+{b.xp_table[k]}</b></div>)}
        </div>
        <p className="mt-2 text-xs text-muted">Level n starts at 50·n·(n−1) XP; a new rank every 3 levels, from Cadet to Fleet Admiral.</p>
      </Section>
    </div>
  );
}

function Tile({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-xs text-muted"><span className="text-accent">{icon}</span>{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

function AgentCard({ a, place }: { a: XpAgent; place: number }) {
  const stat = (label: string, v: string | number) => <div><div className="text-[11px] text-muted">{label}</div><div className="text-sm font-medium">{v}</div></div>;
  return (
    <div className="card p-4">
      <div className="flex items-center gap-3">
        <span className="w-6 text-center text-sm text-muted">#{place}</span>
        <AgentAvatar icon={a.icon} color={a.color} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2"><b>{a.name}</b><span className="truncate text-xs text-muted">{a.role}</span></div>
          <div className="text-xs"><span className="text-accent">Level {a.level}</span> · {a.rank}</div>
        </div>
        <div className="text-right"><div className="text-lg font-semibold tabular-nums">{a.xp.toLocaleString()}</div><div className="text-[11px] text-muted">XP</div></div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-panel-2">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(a.progress * 100)}%` }} />
      </div>
      <div className="mt-1 text-right text-[11px] text-muted">{a.next_level - a.xp} XP to level {a.level + 1}</div>
      <div className="mt-2 grid grid-cols-4 gap-2">
        {stat("Tasks", a.tasks_done)}
        {stat("Success", a.success_rate == null ? "-" : `${Math.round(a.success_rate * 100)}%`)}
        {stat("Avg review", a.avg_score == null ? "-" : a.avg_score.toFixed(1))}
        {stat("Worked", hours(a.work_seconds))}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
        {a.rated_high > 0 && <span className="chip"><Star size={11} /> {a.rated_high} top-rated</span>}
        {a.arena_wins + a.arena_losses + a.arena_ties > 0 && <span className="chip"><Swords size={11} /> {a.arena_wins}-{a.arena_losses}{a.arena_ties ? `-${a.arena_ties}` : ""}</span>}
        {a.councils > 0 && <span className="chip">{a.councils} council{a.councils > 1 ? "s" : ""}</span>}
        {a.lessons > 0 && <span className="chip">{a.lessons} lessons</span>}
        {a.week_xp > 0 && <span className="chip">+{a.week_xp} this week</span>}
      </div>
    </div>
  );
}
