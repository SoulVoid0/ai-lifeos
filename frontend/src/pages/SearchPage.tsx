import { Link, useSearchParams } from "react-router-dom";
import { Brain, CalendarDays, FileText, MessageSquare } from "lucide-react";
import { api } from "../lib/api";
import { ago, when } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { FileRow } from "../components/FileRow";
import { Empty, ErrorBox, PageHeader, Section, Spinner } from "../components/ui";

export default function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const { data, error, loading } = useAsync(() => api.search(q), [q]);
  const total = data ? data.files.length + data.memories.length + data.tasks.length + data.messages.length : 0;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title={`Results for “${q}”`} subtitle={data ? `${total} results across your files, memories, tasks and conversations` : undefined} />
      <ErrorBox error={error} />
      {loading && <Spinner />}
      {data && total === 0 && <Empty>Nothing found. Try different words.</Empty>}
      {data && total > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.memories.length > 0 && (
            <Section title="Memories" icon={<Brain size={15} className="text-accent" />}>
              <ul className="space-y-2">
                {data.memories.map((m) => (
                  <li key={m.id} className="text-sm"><span className="chip mr-2">{m.kind}</span>{m.content}</li>
                ))}
              </ul>
            </Section>
          )}
          {data.tasks.length > 0 && (
            <Section title="Tasks" icon={<CalendarDays size={15} className="text-accent" />}>
              <ul className="space-y-1.5">
                {data.tasks.map((t) => (
                  <li key={t.id} className="text-sm">
                    <span className={t.status === "done" ? "text-muted line-through" : ""}>{t.title}</span>
                    {t.due_at && <span className="ml-2 text-xs text-muted">due {when(t.due_at)}</span>}
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {data.files.length > 0 && (
            <Section title="Files" icon={<FileText size={15} className="text-accent" />} className="md:col-span-2">
              {data.files.map((f) => <FileRow key={f.id} file={f} />)}
            </Section>
          )}
          {data.messages.length > 0 && (
            <Section title="Conversations" icon={<MessageSquare size={15} className="text-accent" />} className="md:col-span-2">
              <ul className="space-y-2">
                {data.messages.map((m) => (
                  <li key={m.id} className="text-sm">
                    <Link to="/assistant" className="font-medium hover:underline">{m.conversation_title}</Link>
                    <span className="ml-2 text-xs text-muted">{m.role} · {ago(m.created_at)}</span>
                    <div className="text-xs text-muted">{m.snippet}</div>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
