import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bot, MessageSquarePlus, Network, Send, Trash2, User } from "lucide-react";
import { api, type Message } from "../lib/api";
import { ago } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { StepView } from "../components/StepView";
import { ErrorBox, Spinner, notifyChanged } from "../components/ui";
import { MicButton } from "../components/crew";

const EXAMPLES = [
  "Find all PDFs in my Downloads folder, organize them by category, and remind me to review the important ones tomorrow.",
  "Find the PDF about JavaScript testing that I downloaded a few months ago.",
  "Find large files I haven't used recently.",
  "Remind me to finish my programming project tomorrow at 6pm.",
  "Plan my day",
  "Remember that I prefer studying in the morning.",
];

export default function Assistant() {
  const [params, setParams] = useSearchParams();
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const navigate = useNavigate();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const convs = useAsync(api.conversations, []);
  const bottom = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const open = async (id: number | null) => {
    setConversationId(id);
    setError(undefined);
    setMessages(id ? await api.messages(id) : []);
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || sending) return;
    setInput("");
    setSending(true);
    setError(undefined);
    const optimistic: Message = {
      id: -Date.now(), conversation_id: conversationId ?? 0, role: "user", content: message, data: null,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    try {
      const res = await api.chat(message, conversationId);
      setConversationId(res.conversation_id);
      setMessages(await api.messages(res.conversation_id));
      convs.reload();
      notifyChanged();
    } catch (e) {
      setError((e as Error).message);
      setMessages((m) => m.filter((x) => x.id !== optimistic.id));
      setInput(message);
    } finally {
      setSending(false);
    }
  };

  // Commands launched from the dashboard arrive as ?prompt=
  useEffect(() => {
    const prompt = params.get("prompt");
    if (prompt && !started.current) {
      started.current = true;
      setParams({}, { replace: true });
      send(prompt);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-full">
      <div className="hidden w-60 shrink-0 flex-col border-r border-line md:flex">
        <div className="p-3">
          <button className="btn-outline w-full justify-center" onClick={() => open(null)}>
            <MessageSquarePlus size={15} /> New conversation
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {(convs.data ?? []).map((c) => (
            <div
              key={c.id}
              className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm ${
                c.id === conversationId ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2"
              }`}
            >
              <button className="min-w-0 flex-1 text-left" onClick={() => open(c.id)}>
                <div className="truncate">{c.title}</div>
                <div className="text-[11px] text-muted">{ago(c.updated_at)}</div>
              </button>
              <button
                className="opacity-0 hover:text-danger group-hover:opacity-100"
                title="Delete conversation"
                onClick={async () => {
                  await api.deleteConversation(c.id);
                  if (c.id === conversationId) open(null);
                  convs.reload();
                }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-3xl space-y-5">
            {messages.length === 0 && !sending && (
              <div className="pt-10 text-center">
                <Bot size={36} className="mx-auto text-accent" />
                <h2 className="mt-3 text-lg font-semibold">What should I take care of?</h2>
                <p className="mt-1 text-sm text-muted">
                  I act only inside folders you approve, and ask before moving or changing anything.
                </p>
                <div className="mx-auto mt-6 grid max-w-2xl gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((e) => (
                    <button key={e} onClick={() => send(e)} className="card p-3 text-left text-sm text-muted hover:border-accent hover:text-text">
                      {e}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className="flex gap-3">
                <div className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${m.role === "user" ? "bg-panel-2" : "bg-accent"}`}>
                  {m.role === "user" ? <User size={14} /> : <Bot size={14} className="text-white" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="whitespace-pre-line text-sm leading-relaxed">{m.content}</div>
                  {m.data?.steps?.map((s, i) => <StepView key={i} step={s} />)}
                  {m.data?.planner && (
                    <div className="mt-1 text-[11px] text-muted">
                      {m.data.planner === "llm" ? "planned by local model" : "handled by built-in rules"}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Spinner /> Working on it…
              </div>
            )}
            <ErrorBox error={error} />
            <div ref={bottom} />
          </div>
        </div>
        <form
          className="border-t border-line p-4"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              className="input min-h-[44px] resize-none"
              rows={Math.min(6, Math.max(1, input.split("\n").length))}
              placeholder="Ask LifeOS to find, organize, remind, plan or remember…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
            <MicButton onText={(t) => setInput((v) => (v ? `${v} ${t}` : t))} />
            <button type="button" className="btn-outline h-[44px]" disabled={!input.trim()} title="Hand this to the crew as a mission"
              onClick={() => navigate(`/crew?goal=${encodeURIComponent(input.trim())}`)}>
              <Network size={15} />
            </button>
            <button className="btn-primary h-[44px]" disabled={sending || !input.trim()}>
              <Send size={15} />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
