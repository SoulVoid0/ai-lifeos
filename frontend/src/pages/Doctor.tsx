// PC doctor: a checkup of temperatures, disks, memory, crashes and clutter, explained in plain words.
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Stethoscope, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type DoctorFinding } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { play } from "../lib/sfx";
import { Empty, PageHeader, Section, Spinner, useToast } from "../components/ui";

const SEV: Record<DoctorFinding["severity"], { icon: typeof Info; cls: string; label: string }> = {
  bad: { icon: AlertOctagon, cls: "border-danger/40 bg-danger/5 text-danger", label: "Fix soon" },
  warn: { icon: AlertTriangle, cls: "border-warn/40 bg-warn/5 text-warn", label: "Worth a look" },
  info: { icon: Info, cls: "border-line text-accent", label: "FYI" },
  ok: { icon: CheckCircle2, cls: "border-line text-ok", label: "Fine" },
};

export default function Doctor() {
  const toast = useToast();
  const st = useAsync(api.doctor, [], 4000);
  const [running, setRunning] = useState(false);
  const [showOk, setShowOk] = useState(false);
  const run = async () => {
    setRunning(true);
    try {
      const r = await api.runDoctor();
      play(r.worst === "bad" ? "error" : r.worst === "warn" ? "notify" : "success");
      st.reload();
    } catch (e) { toast((e as Error).message, "error"); } finally { setRunning(false); }
  };
  const clean = async () => {
    try {
      const r = await api.cleanTemp();
      toast(`Deleted ${r.removed} old temp files and freed ${(r.freed / 1e6).toFixed(0)} MB${r.skipped ? ` (${r.skipped} in use, skipped)` : ""}`);
      play("success");
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const rep = st.data?.report;
  const busy = running || st.data?.busy;
  const shown = (rep?.findings ?? []).filter((f) => showOk || f.severity !== "ok");
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <PageHeader title="PC doctor" subtitle="A read-only checkup of your PC: temperatures, disk space and health, memory, crashes and startup clutter, with what to do in plain words."
        actions={<>
          <button className="btn-outline" onClick={clean}><Trash2 size={14} /> Clear old temp files</button>
          <button className="btn-primary" onClick={run} disabled={!!busy}>{busy ? <Spinner /> : <Stethoscope size={14} />} {busy ? "Checking…" : "Run checkup"}</button>
        </>} />
      {!rep && !busy && <Empty>No checkup yet. It takes about half a minute.</Empty>}
      {rep && (
        <>
          <Section title={`Checkup ${ago(rep.at)}`} icon={<Stethoscope size={15} className="text-accent" />}
            actions={<span className="text-xs text-muted">{rep.counts.bad ?? 0} to fix · {rep.counts.warn ?? 0} to look at · {rep.counts.ok ?? 0} fine · {rep.seconds}s</span>}>
            <p className="whitespace-pre-wrap text-sm">{rep.summary}</p>
          </Section>
          <div className="space-y-2">
            {shown.map((f, i) => {
              const S = SEV[f.severity];
              return (
                <div key={i} className={`card border p-3 ${S.cls.split(" ").filter((c) => c.startsWith("border") || c.startsWith("bg")).join(" ")}`}>
                  <div className="flex items-start gap-2">
                    <S.icon size={16} className={`mt-0.5 shrink-0 ${S.cls.split(" ").find((c) => c.startsWith("text"))}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-2"><b className="text-sm">{f.title}</b><span className="chip">{f.area}</span><span className="text-[11px] text-muted">{S.label}</span></div>
                      <p className="mt-0.5 text-sm text-muted">{f.detail}</p>
                      {f.fix && <p className="mt-1 text-sm"><b>What to do:</b> {f.fix}</p>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <button className="btn-ghost text-xs" onClick={() => setShowOk((v) => !v)}>{showOk ? "Hide" : "Show"} the checks that passed</button>
        </>
      )}
      {st.data && (
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={st.data.config.weekly} onChange={async (e) => st.setData(await api.setDoctor({ weekly: e.target.checked }))} />
          Check every week and tell me if something needs attention
        </label>
      )}
    </div>
  );
}
