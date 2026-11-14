import { useEffect, useState } from "react";
import { Bell, Cloud, KeyRound, Mail, Plus, Trash2, Users, X, Zap } from "lucide-react";
import { api, type EmailAlertEvent, type EmailAlerts, type Settings as S } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Section, Spinner, useToast } from "./ui";

type Props = { form: S; setForm: (s: S) => void; save: (patch: Partial<S>) => Promise<void> };

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}{hint && <span className="block text-xs text-muted">{hint}</span>}</span>
    </label>
  );
}

export function CrewSettings({ form, setForm, save }: Props) {
  const num = (k: "max_parallel_agents" | "agent_max_steps" | "mission_token_budget") => (
    <input type="number" className="input" value={form[k]} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })}
      onBlur={() => save({ [k]: form[k] } as Partial<S>)} />
  );
  return (
    <Section title="Crew & web" icon={<Users size={15} className="text-accent" />}>
      <div className="grid gap-3 sm:grid-cols-3">
        <div><label className="label">Agents working at once</label>{num("max_parallel_agents")}</div>
        <div><label className="label">Steps per task (at least)</label>{num("agent_max_steps")}</div>
        <div><label className="label">Token budget per mission</label>{num("mission_token_budget")}</div>
      </div>
      <p className="mt-2 text-xs text-muted">On a laptop with one local model, 1–2 parallel agents is usually fastest; Ollama queues the rest.
        Each agent can have more steps than this in its own settings (Forge has 30 for coding).</p>
      <div className="mt-3">
        <Toggle checked={form.agent_auto_extend ?? true} onChange={(v) => save({ agent_auto_extend: v })}
          label="Give agents more steps when they run out mid-task"
          hint="An agent still making progress gets one automatic extension, then asks you whether to keep going. Its work so far is kept either way." />
      </div>
      <div className="mt-3">
        <Toggle checked={form.web_access} onChange={(v) => save({ web_access: v })} label="Allow web search and page reading"
          hint="Lets the research agent use web_search and fetch_url. Private and local addresses are always blocked; fetching a page asks you first unless you change its policy." />
      </div>
    </Section>
  );
}

export function CloudSettings({ form, setForm, save }: Props) {
  const toast = useToast();
  const providers = useAsync(api.providers, []);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [price, setPrice] = useState({ model: "", in: "", out: "" });
  const [testRef, setTestRef] = useState("");
  const [testResult, setTestResult] = useState<string | null>(null);

  const storeKey = async (id: string) => {
    try {
      await api.setProviderKey(id, keys[id] ?? "");
      setKeys({ ...keys, [id]: "" });
      providers.reload();
      toast("Key stored in the OS credential store");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Section title="Cloud AI (optional)" icon={<Cloud size={15} className="text-accent" />}>
      <p className="mb-3 text-xs text-muted">
        Everything works with local models. Turn this on to give individual agents a cloud model (set it per agent in the Control Center → Crew), e.g.
        <code className="mx-1">openai:gpt-4o-mini</code> or <code className="mx-1">anthropic:claude-sonnet-4-5</code>. Keys live in {providers.data?.vault ?? "the OS keyring"}, never in the database.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Toggle checked={form.cloud_enabled} onChange={(v) => save({ cloud_enabled: v })} label="Allow cloud models" hint="Off: every cloud call is refused." />
        <div>
          <label className="label">Daily cloud budget (USD)</label>
          <input type="number" step="0.5" min={0} className="input" value={form.cloud_daily_budget_usd}
            onChange={(e) => setForm({ ...form, cloud_daily_budget_usd: Number(e.target.value) })}
            onBlur={() => save({ cloud_daily_budget_usd: form.cloud_daily_budget_usd })} />
        </div>
      </div>

      <div className="mt-4 divide-y divide-line">
        {(providers.data?.providers ?? []).filter((p) => p.id !== "ollama").map((p) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 py-2">
            <div className="w-56 text-sm">
              {p.label}
              <div className="font-mono text-[11px] text-muted">{p.id}:model-name</div>
            </div>
            {p.has_key ? (
              <>
                <span className="chip border-ok/40 text-ok"><KeyRound size={11} /> key stored</span>
                <button className="btn-ghost text-xs" onClick={async () => { await api.deleteProviderKey(p.id); providers.reload(); }}><Trash2 size={13} /> Remove</button>
              </>
            ) : (
              <>
                <input type="password" className="input w-64 flex-1" placeholder={p.id === "custom" ? "API key (optional)" : "API key"}
                  value={keys[p.id] ?? ""} onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })} autoComplete="off" />
                <button className="btn-outline" disabled={!keys[p.id]} onClick={() => storeKey(p.id)}>Save</button>
              </>
            )}
          </div>
        ))}
      </div>

      <div className="mt-3">
        <label className="label">Custom OpenAI-compatible server (LM Studio, vLLM, llama.cpp)</label>
        <input className="input" placeholder="http://127.0.0.1:1234/v1" value={form.custom_provider_url}
          onChange={(e) => setForm({ ...form, custom_provider_url: e.target.value })} onBlur={() => save({ custom_provider_url: form.custom_provider_url })} />
      </div>

      <div className="mt-3">
        <label className="label">Test a model</label>
        <div className="flex gap-2">
          <input className="input font-mono" placeholder="openai:gpt-4o-mini" value={testRef} onChange={(e) => setTestRef(e.target.value)} />
          <button className="btn-outline" disabled={!testRef} onClick={async () => {
            setTestResult("…");
            const r = await api.testModel(testRef);
            setTestResult(r.ok ? `OK in ${r.seconds}s: "${r.reply}"` : `Failed: ${r.error}`);
          }}><Zap size={14} /> Test</button>
        </div>
        {testResult && <div className={`mt-1 text-xs ${testResult.startsWith("OK") ? "text-ok" : testResult === "…" ? "text-muted" : "text-danger"}`}>{testResult === "…" ? <Spinner size={12} /> : testResult}</div>}
      </div>

      <div className="mt-4">
        <label className="label">Prices (USD per 1M tokens) for the cost ledger</label>
        <ul className="mb-2 space-y-1">
          {Object.entries(form.model_prices).map(([m, p]) => (
            <li key={m} className="flex items-center gap-2 text-xs">
              <span className="flex-1 font-mono">{m}</span><span className="text-muted">in ${p.in} · out ${p.out}</span>
              <button className="btn-ghost px-1" onClick={() => { const n = { ...form.model_prices }; delete n[m]; save({ model_prices: n }); }}><X size={12} /></button>
            </li>
          ))}
        </ul>
        <div className="flex gap-2">
          <input className="input flex-1 font-mono" placeholder="openai:gpt-4o-mini" value={price.model} onChange={(e) => setPrice({ ...price, model: e.target.value })} />
          <input className="input w-24" placeholder="in" type="number" step="0.01" value={price.in} onChange={(e) => setPrice({ ...price, in: e.target.value })} />
          <input className="input w-24" placeholder="out" type="number" step="0.01" value={price.out} onChange={(e) => setPrice({ ...price, out: e.target.value })} />
          <button className="btn-outline" disabled={!price.model} onClick={() => {
            save({ model_prices: { ...form.model_prices, [price.model.trim()]: { in: Number(price.in) || 0, out: Number(price.out) || 0 } } });
            setPrice({ model: "", in: "", out: "" });
          }}><Plus size={14} /></button>
        </div>
        <p className="mt-1 text-[11px] text-muted">Check your provider's pricing page; prices change. Token counts are always recorded.</p>
      </div>
    </Section>
  );
}

export function IntegrationSettings() {
  const toast = useToast();
  const status = useAsync(api.emailStatus, []);
  const [f, setF] = useState({ host: "", port: 993, username: "", folder: "INBOX", password: "" });
  const [testing, setTesting] = useState(false);
  useEffect(() => {
    if (status.data) setF((x) => ({ ...x, host: status.data!.host ?? "", port: status.data!.port ?? 993, username: status.data!.username ?? "", folder: status.data!.folder ?? "INBOX" }));
  }, [status.data]);
  const saveEmail = async () => {
    try {
      await api.setEmail({ host: f.host, port: f.port, username: f.username, folder: f.folder, ...(f.password ? { password: f.password } : {}) });
      setF({ ...f, password: "" });
      status.reload();
      toast("Email account saved");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Section title="Integrations" icon={<Mail size={15} className="text-accent" />}>
      <div className="mb-2 text-sm font-medium">Email (read-only, IMAP)</div>
      <p className="mb-2 text-xs text-muted">
        The comms agent can read headers and snippets; nothing is sent, deleted or marked read. For Gmail/Outlook use an app password
        (Gmail: imap.gmail.com, Outlook: outlook.office365.com, port 993). The password goes to the OS credential store.
      </p>
      <div className="grid gap-2 sm:grid-cols-4">
        <input className="input sm:col-span-2" placeholder="imap.gmail.com" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} />
        <input className="input" type="number" value={f.port} onChange={(e) => setF({ ...f, port: Number(e.target.value) })} />
        <input className="input" placeholder="INBOX" value={f.folder} onChange={(e) => setF({ ...f, folder: e.target.value })} />
        <input className="input sm:col-span-2" placeholder="you@example.com" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <input className="input sm:col-span-2" type="password" autoComplete="off" placeholder={status.data?.configured ? "•••••••• (stored)" : "App password"}
          value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button className="btn-primary" onClick={saveEmail} disabled={!f.host || !f.username}>Save</button>
        <button className="btn-outline" disabled={!status.data?.configured || testing} onClick={async () => {
          setTesting(true);
          const r = await api.testEmail();
          setTesting(false);
          toast(r.ok ? `Connected. Latest: ${r.latest ?? "(empty mailbox)"}` : `Failed: ${r.error}`, r.ok ? "ok" : "error");
        }}>{testing ? <Spinner /> : <Zap size={14} />} Test</button>
        <span className={`text-xs ${status.data?.configured ? "text-ok" : "text-muted"}`}>{status.data?.configured ? "configured" : "not set up"}</span>
      </div>
      <p className="mt-3 text-xs text-muted">Calendars (ICS subscriptions and files) are on the Calendar page. Cloud model providers are above.</p>
    </Section>
  );
}

const ALERT_EVENTS: [EmailAlertEvent, string][] = [
  ["mission_done", "A mission finishes"],
  ["mission_failed", "A mission fails"],
  ["approval", "An agent needs your approval"],
  ["question", "An agent asks you something (including \"Keep going?\" at its step limit)"],
  ["workflow", "An automation finishes or fails"],
];

function EmailAlertSettings() {
  const toast = useToast();
  const status = useAsync(api.emailAlerts, []);
  const [f, setF] = useState<Pick<EmailAlerts, "to" | "host" | "port" | "username" | "security">>(
    { to: "", host: "smtp.gmail.com", port: 465, username: "", security: "ssl" });
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const st = status.data;
  useEffect(() => {
    if (st) setF({ to: st.to, host: st.host, port: st.port, username: st.username, security: st.security });
  }, [st]);
  const put = async (patch: Parameters<typeof api.setEmailAlerts>[0], msg?: string) => {
    try {
      await api.setEmailAlerts(patch);
      status.reload();
      if (msg) toast(msg);
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const saveAccount = async () => {
    if (await put({ ...f, to: f.to || f.username, ...(password ? { password } : {}) }, "Email alerts saved")) setPassword("");
  };
  const setSecurity = (security: "ssl" | "starttls") => setF({ ...f, security, port: security === "ssl" ? 465 : 587 });
  if (!st) return <Spinner />;
  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="mb-1 flex items-center gap-2 text-sm font-medium"><Mail size={14} className="text-accent" /> Email alerts</div>
      <p className="mb-2 text-xs text-muted">
        Get an email when the crew finishes or needs you. Gmail: smtp.gmail.com, port 465 (SSL), your Gmail address and an app
        password from myaccount.google.com/apppasswords. Outlook: smtp.office365.com, port 587 (STARTTLS). The password goes to the
        OS credential store; if it's the same account as the read-only email above, that password is used.
      </p>
      <Toggle checked={st.enabled} onChange={(v) => put({ enabled: v }, v ? "Email alerts on" : "Email alerts off")} label="Send me email alerts" />
      <div className="mt-2 grid gap-2 sm:grid-cols-4">
        <input className="input sm:col-span-2" placeholder="smtp.gmail.com" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} />
        <select className="input" value={f.security} onChange={(e) => setSecurity(e.target.value as "ssl" | "starttls")}>
          <option value="ssl">SSL</option>
          <option value="starttls">STARTTLS</option>
        </select>
        <input className="input" type="number" value={f.port} onChange={(e) => setF({ ...f, port: Number(e.target.value) })} />
        <input className="input sm:col-span-2" placeholder="Login: you@gmail.com" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <input className="input sm:col-span-2" type="password" autoComplete="off"
          placeholder={st.password_stored ? "•••••••• (stored)" : st.password_from_imap ? "Using the IMAP password" : "App password"}
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <input className="input sm:col-span-4" placeholder={`Send to: ${f.username || "you@gmail.com"}`} value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button className="btn-primary" onClick={saveAccount} disabled={!f.host || !f.username}>Save</button>
        <button className="btn-outline" disabled={!st.configured || busy} onClick={async () => {
          setBusy(true);
          const r = await api.testEmailAlerts().catch((e: Error) => ({ ok: false, error: e.message, to: undefined }));
          setBusy(false);
          status.reload();
          toast(r.ok ? `Test email sent to ${r.to}` : `Failed: ${r.error}`, r.ok ? "ok" : "error");
        }}>{busy ? <Spinner /> : <Zap size={14} />} Send test email</button>
        <span className={`text-xs ${st.configured ? "text-ok" : "text-muted"}`}>{st.configured ? "configured" : "not set up"}</span>
      </div>
      {st.last_error && <p className="mt-1 text-xs text-danger">Last error: {st.last_error}</p>}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {ALERT_EVENTS.map(([key, label]) => (
          <Toggle key={key} checked={st.events[key]} onChange={(v) => put({ events: { ...st.events, [key]: v } })} label={label} />
        ))}
      </div>
      <p className="mt-1 text-[11px] text-muted">Approvals and questions from the same mission send at most one email every 10 minutes.</p>
    </div>
  );
}

export function NotificationSettings({ form, setForm, save }: Props) {
  const prefs = form.notification_prefs;
  const q = form.quiet_hours;
  return (
    <Section title="Notifications" icon={<Bell size={15} className="text-accent" />}>
      <div className="grid gap-2 sm:grid-cols-2">
        <Toggle checked={prefs.reminders} onChange={(v) => save({ notification_prefs: { ...prefs, reminders: v } })} label="Task reminders" />
        <Toggle checked={prefs.workflows} onChange={(v) => save({ notification_prefs: { ...prefs, workflows: v } })} label="Workflow summaries" />
        <Toggle checked={prefs.missions} onChange={(v) => save({ notification_prefs: { ...prefs, missions: v } })} label="Crew: questions, approvals and finished missions" />
        <Toggle checked={prefs.suggestions} onChange={(v) => save({ notification_prefs: { ...prefs, suggestions: v } })} label="Desktop pop-ups for new suggestions" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Toggle checked={q.enabled} onChange={(v) => save({ quiet_hours: { ...q, enabled: v } })} label="Quiet hours (no desktop pop-ups)" />
        <input type="time" className="input w-32" value={q.start} onChange={(e) => setForm({ ...form, quiet_hours: { ...q, start: e.target.value } })}
          onBlur={() => save({ quiet_hours: form.quiet_hours })} />
        <span className="text-xs text-muted">to</span>
        <input type="time" className="input w-32" value={q.end} onChange={(e) => setForm({ ...form, quiet_hours: { ...q, end: e.target.value } })}
          onBlur={() => save({ quiet_hours: form.quiet_hours })} />
      </div>
      <EmailAlertSettings />
    </Section>
  );
}
