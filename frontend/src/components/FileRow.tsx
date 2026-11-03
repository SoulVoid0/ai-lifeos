import { ExternalLink, FileText, Film, Image, Music, Archive, Code, File } from "lucide-react";
import { api, type FileItem } from "../lib/api";
import { ago, bytes, dirname } from "../lib/format";
import { useToast } from "./ui";

const ICONS: Record<string, typeof File> = {
  pdf: FileText, doc: FileText, docx: FileText, txt: FileText, md: FileText,
  jpg: Image, jpeg: Image, png: Image, gif: Image, webp: Image, heic: Image, svg: Image,
  mp4: Film, mov: Film, mkv: Film, avi: Film, webm: Film,
  mp3: Music, wav: Music, flac: Music, m4a: Music,
  zip: Archive, rar: Archive, "7z": Archive, tar: Archive, gz: Archive,
  py: Code, js: Code, ts: Code, tsx: Code, java: Code, go: Code, rs: Code, c: Code, cpp: Code,
};

export function FileRow({ file, compact = false }: { file: FileItem; compact?: boolean }) {
  const toast = useToast();
  const Icon = ICONS[file.ext] ?? File;
  const open = () => api.openFile(file.path).catch((e) => toast((e as Error).message, "error"));
  return (
    <div className="group flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-panel-2">
      <Icon size={18} className="mt-0.5 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <button onClick={open} className="truncate text-left text-sm font-medium hover:underline" title={file.path}>
            {file.name}
          </button>
          {file.category && <span className="chip shrink-0">{file.category}</span>}
        </div>
        {!compact && <div className="truncate text-xs text-muted" title={file.path}>{dirname(file.path)}</div>}
        {!compact && file.snippet && <div className="mt-0.5 line-clamp-2 text-xs text-muted/80">{file.snippet}</div>}
      </div>
      <div className="shrink-0 text-right text-xs text-muted">
        <div>{bytes(file.size)}</div>
        <div>{ago(file.modified_at)}</div>
      </div>
      <button onClick={open} className="btn-ghost px-1.5 opacity-0 group-hover:opacity-100" title="Open">
        <ExternalLink size={14} />
      </button>
    </div>
  );
}
