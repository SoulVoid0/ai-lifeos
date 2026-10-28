import type { ReactNode } from "react";

/** Small, safe Markdown renderer (headings, lists, code, quotes, bold/italic/code/links). No raw HTML. */
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      out.push(
        <pre key={key++} className="my-2 overflow-x-auto rounded-lg border border-line bg-bg p-3 font-mono text-xs">
          {body.join("\n")}
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const size = ["text-lg", "text-base", "text-sm", "text-sm"][h[1].length - 1];
      out.push(<div key={key++} className={`mb-1 mt-3 font-semibold ${size}`}>{inline(h[2])}</div>);
      i++;
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      const Tag = ordered ? "ol" : "ul";
      out.push(
        <Tag key={key++} className={`my-1 space-y-0.5 pl-5 ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((it, j) => <li key={j}>{inline(it)}</li>)}
        </Tag>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) body.push(lines[i++].replace(/^>\s?/, ""));
      out.push(<blockquote key={key++} className="my-2 border-l-2 border-accent pl-3 text-muted">{inline(body.join(" "))}</blockquote>);
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      out.push(<hr key={key++} className="my-3 border-line" />);
      i++;
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\s*([-*•]|\d+[.)])\s+|---+$)/.test(lines[i])) para.push(lines[i++]);
    out.push(<p key={key++} className="my-1.5">{inline(para.join(" "))}</p>);
  }
  return <div className={`text-sm leading-relaxed ${className}`}>{out}</div>;
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) parts.push(<code key={k++} className="rounded bg-bg px-1 font-mono text-[0.85em]">{t.slice(1, -1)}</code>);
    else if (t.startsWith("**") || t.startsWith("__")) parts.push(<strong key={k++}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("[")) {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t)!;
      const safe = /^https?:\/\//i.test(lm[2]);
      parts.push(safe ? <a key={k++} href={lm[2]} target="_blank" rel="noreferrer" className="text-accent underline">{lm[1]}</a> : lm[1]);
    } else parts.push(<em key={k++}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
