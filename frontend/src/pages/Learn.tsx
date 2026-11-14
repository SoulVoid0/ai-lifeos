// Learn mode: flashcard decks made by Sage from a topic, notes or a file, reviewed with spaced repetition.
import { useEffect, useState } from "react";
import { Brain, Check, Download, Flame, GraduationCap, Plus, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { api, type Card, type Deck } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { play } from "../lib/sfx";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

const GRADES = [
  { g: 0, label: "Again", key: "1", cls: "border-danger/40 text-danger" },
  { g: 1, label: "Hard", key: "2", cls: "border-warn/40 text-warn" },
  { g: 2, label: "Good", key: "3", cls: "border-ok/40 text-ok" },
  { g: 3, label: "Easy", key: "4", cls: "border-accent/40 text-accent" },
];

export default function Learn() {
  const toast = useToast();
  const data = useAsync(api.learn, [], 5000);
  const [mode, setMode] = useState<{ review: number | "all" } | { deck: number } | null>(null);
  const [form, setForm] = useState({ topic: "", text: "", path: "", count: 15, src: "topic" as "topic" | "text" | "path" });
  const make = async () => {
    try {
      await api.makeDeck({ topic: form.src === "topic" ? form.topic : "", text: form.src === "text" ? form.text : "",
        path: form.src === "path" ? form.path : "", count: form.count });
      play("start");
      toast("Sage is writing the cards…", "info");
      setForm((f) => ({ ...f, topic: "", text: "", path: "" }));
      data.reload();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const st = data.data?.stats;
  if (mode && "review" in mode) return <Review deck={mode.review === "all" ? undefined : mode.review} onDone={() => { setMode(null); data.reload(); }} />;
  if (mode && "deck" in mode) return <DeckEditor id={mode.deck} onBack={() => { setMode(null); data.reload(); }} />;
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <PageHeader title="Learn" subtitle="Flashcards with spaced repetition: cards you know come back weeks later, the ones you miss come back soon. A few minutes a day is enough."
        actions={<button className="btn-primary" disabled={!st?.due} onClick={() => setMode({ review: "all" })}><Brain size={14} /> Review {st?.due ? `(${st.due})` : ""}</button>} />
      {st && (
        <div className="grid gap-3 sm:grid-cols-4">
          {[["Due now", st.due], ["Cards", st.cards], ["Learned", st.learned], ["Streak", `${st.streak} day${st.streak === 1 ? "" : "s"}`]].map(([l, v]) => (
            <div key={String(l)} className="card p-3"><div className="flex items-center gap-1 text-xs text-muted">{l === "Streak" && <Flame size={12} className="text-warn" />}{l}</div><div className="text-xl font-semibold">{v}</div></div>
          ))}
        </div>
      )}
      <Section title="New deck" icon={<GraduationCap size={15} className="text-accent" />}>
        <div className="mb-2 flex gap-1">
          {(["topic", "text", "path"] as const).map((k) => (
            <button key={k} className={`btn px-2.5 py-1 text-xs ${form.src === k ? "bg-accent-soft text-text" : "text-muted"}`} onClick={() => setForm({ ...form, src: k })}>
              {k === "topic" ? "From a topic" : k === "text" ? "From my notes" : "From a file"}</button>
          ))}
        </div>
        {form.src === "topic" && <input className="input" placeholder="e.g. Japanese hiragana, the French Revolution, Python list methods" value={form.topic} onChange={(e) => setForm({ ...form, topic: e.target.value })} />}
        {form.src === "text" && <textarea className="input min-h-28" placeholder="Paste notes, an article or a chapter" value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} />}
        {form.src === "path" && <input className="input" placeholder="Full path of a PDF, document or text file in an approved folder" value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} />}
        <div className="mt-3 flex items-center gap-3">
          <label className="text-xs text-muted">Cards <input type="number" min={3} max={60} className="input ml-1 inline w-20" value={form.count} onChange={(e) => setForm({ ...form, count: Number(e.target.value) })} /></label>
          <button className="btn-primary ml-auto" onClick={make}><Plus size={14} /> Make deck</button>
        </div>
      </Section>
      <div className="grid gap-3 md:grid-cols-2">
        {(data.data?.decks ?? []).length === 0 && <Empty>No decks yet.</Empty>}
        {(data.data?.decks ?? []).map((d) => (
          <div key={d.id} className="card p-4">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{d.title}</div>
                <div className="text-xs text-muted">{d.source}</div>
              </div>
              {d.status === "making" && <Spinner />}
            </div>
            {d.error && <p className="mt-1 text-xs text-danger">{d.error}</p>}
            <div className="mt-2 flex gap-3 text-xs text-muted"><span>{d.cards} cards</span><span>{d.due} due</span><span>{d.new} new</span><span>{d.learned} learned</span></div>
            <div className="mt-2 h-1.5 overflow-hidden rounded bg-panel-2"><div className="h-full bg-ok" style={{ width: `${d.cards ? (d.learned / d.cards) * 100 : 0}%` }} /></div>
            <div className="mt-3 flex gap-2">
              <button className="btn-primary" disabled={!d.due} onClick={() => setMode({ review: d.id })}>Study</button>
              <button className="btn-outline" onClick={() => setMode({ deck: d.id })}>Cards</button>
              <button className="btn-ghost ml-auto" title="Delete deck" onClick={async () => { await api.deleteDeck(d.id); data.reload(); }}><Trash2 size={14} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Review({ deck, onDone }: { deck?: number; onDone: () => void }) {
  const [queue, setQueue] = useState<Card[] | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [answer, setAnswer] = useState("");
  const [check, setCheck] = useState<{ correct: boolean; feedback: string; suggested_grade: number } | null>(null);
  const [checking, setChecking] = useState(false);
  const [done, setDone] = useState(0);
  useEffect(() => { api.learnQueue(deck).then(setQueue).catch(() => setQueue([])); }, [deck]);
  const card = queue?.[0];
  const grade = async (g: number) => {
    if (!card) return;
    const updated = await api.reviewCard(card.id, g);
    play(g === 0 ? "error" : g >= 2 ? "approve" : "click");
    setDone((n) => n + 1);
    setFlipped(false); setAnswer(""); setCheck(null);
    setQueue((q) => {
      const rest = (q ?? []).slice(1);
      return g === 0 ? [...rest, { ...card, ...updated }] : rest;  // missed cards come back at the end of the session
    });
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === " " && !flipped) { e.preventDefault(); setFlipped(true); }
      const g = GRADES.find((x) => x.key === e.key);
      if (g && flipped) grade(g.g);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  const checkIt = async () => {
    if (!card || !answer.trim()) { setFlipped(true); return; }
    setChecking(true);
    try { setCheck(await api.checkCard(card.id, answer)); } finally { setChecking(false); setFlipped(true); }
  };
  if (!queue) return <div className="p-8"><Spinner /></div>;
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="flex items-center gap-2">
        <button className="btn-ghost" onClick={onDone}><X size={14} /> Done</button>
        <span className="ml-auto text-sm text-muted">{done} reviewed · {queue.length} left</span>
      </div>
      {!card ? (
        <div className="card p-10 text-center">
          <Check size={32} className="mx-auto text-ok" />
          <div className="mt-2 text-lg font-semibold">All caught up</div>
          <p className="text-sm text-muted">{done ? `You reviewed ${done} card${done === 1 ? "" : "s"}. See you tomorrow.` : "Nothing is due right now."}</p>
          <button className="btn-primary mt-4" onClick={onDone}>Back</button>
        </div>
      ) : (
        <div className="card p-6">
          <div className="text-xs text-muted">{card.deck_title}{card.reps === 0 ? " · new" : ""}</div>
          <div className="mt-3 whitespace-pre-wrap text-xl font-medium">{card.front}</div>
          {!flipped ? (
            <div className="mt-6 space-y-2">
              <input className="input" placeholder="Type your answer (optional), then Enter" value={answer} onChange={(e) => setAnswer(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") checkIt(); }} autoFocus />
              <div className="flex gap-2">
                <button className="btn-primary" onClick={checkIt} disabled={checking}>{checking ? <Spinner /> : null} {answer.trim() ? "Check" : "Show answer"} <span className="text-xs opacity-70">(space)</span></button>
              </div>
            </div>
          ) : (
            <div className="mt-6 space-y-4">
              <div className="rounded-lg bg-panel-2 p-4 whitespace-pre-wrap">{card.back}</div>
              {check && <p className={`text-sm ${check.correct ? "text-ok" : "text-warn"}`}>{check.feedback}</p>}
              <div className="grid grid-cols-4 gap-2">
                {GRADES.map((g) => (
                  <button key={g.g} className={`btn justify-center border ${g.cls} ${check?.suggested_grade === g.g ? "bg-panel-2 ring-1 ring-current" : ""}`} onClick={() => grade(g.g)}>
                    {g.label} <span className="text-[10px] opacity-60">{g.key}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DeckEditor({ id, onBack }: { id: number; onBack: () => void }) {
  const toast = useToast();
  const d = useAsync(() => api.deck(id), [id]);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [imp, setImp] = useState("");
  const deck: Deck | undefined = d.data;
  const exportTsv = async () => {
    const res = await fetch(api.deckExportUrl(id), { headers: { "X-LifeOS-Client": "1" } });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `${deck?.title ?? "deck"}.txt`; a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <div className="flex items-center gap-2">
        <button className="btn-ghost" onClick={onBack}><RotateCcw size={14} /> Decks</button>
        <h1 className="flex-1 text-xl font-semibold">{deck?.title}</h1>
        <button className="btn-outline" onClick={exportTsv} title="Tab-separated: imports into Anki"><Download size={14} /> Export</button>
      </div>
      <Section title="Add a card" icon={<Plus size={15} className="text-accent" />}>
        <div className="grid gap-2 md:grid-cols-2">
          <textarea className="input min-h-16" placeholder="Front (question)" value={front} onChange={(e) => setFront(e.target.value)} />
          <textarea className="input min-h-16" placeholder="Back (answer)" value={back} onChange={(e) => setBack(e.target.value)} />
        </div>
        <div className="mt-2 flex gap-2">
          <button className="btn-primary" disabled={!front.trim() || !back.trim()} onClick={async () => { d.setData(await api.addCard(id, front, back)); setFront(""); setBack(""); }}>Add</button>
          <input className="input flex-1" placeholder="Or paste Anki/TSV lines: front<TAB>back" value={imp} onChange={(e) => setImp(e.target.value)} />
          <button className="btn-outline" disabled={!imp.includes("\t")} onClick={async () => { const r = await api.importCards(id, imp); toast(`Added ${r.added} card(s)`); setImp(""); d.reload(); }}><Upload size={14} /> Import</button>
        </div>
      </Section>
      <div className="space-y-2">
        {(deck?.card_list ?? []).map((c) => (
          <div key={c.id} className="card flex items-start gap-3 p-3 text-sm">
            <div className="flex-1"><div className="font-medium">{c.front}</div><div className="mt-1 whitespace-pre-wrap text-muted">{c.back}</div></div>
            <div className="w-24 text-right text-[11px] text-muted">{c.reps ? `every ${c.interval_days < 1 ? "<1" : Math.round(c.interval_days)} d` : "new"}{c.lapses ? ` · missed ${c.lapses}×` : ""}</div>
            <button className="btn-ghost" onClick={async () => { await api.deleteCard(c.id); d.reload(); }}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}
