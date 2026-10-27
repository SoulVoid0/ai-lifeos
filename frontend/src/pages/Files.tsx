import { useState } from "react";
import { Archive, Copy, FolderTree, HardDrive, RefreshCw, Search, Wand2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, type Action, type DuplicateGroup, type FileItem } from "../lib/api";
import { bytes } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { ApprovalCard } from "../components/ApprovalCard";
import { FileRow } from "../components/FileRow";
import { Empty, ErrorBox, PageHeader, Spinner, useToast } from "../components/ui";

type Tab = "search" | "large" | "duplicates" | "old";

export default function Files() {
  const navigate = useNavigate();
  const toast = useToast();
  const folders = useAsync(api.folders, []);
  const [tab, setTab] = useState<Tab>("search");
  const [q, setQ] = useState("");
  const [ext, setExt] = useState("");
  const [folder, setFolder] = useState("");
  const [minMb, setMinMb] = useState(100);
  const [days, setDays] = useState(180);
  const [results, setResults] = useState<FileItem[] | null>(null);
  const [groups, setGroups] = useState<DuplicateGroup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [proposal, setProposal] = useState<Action | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const search = () => run(async () => setResults(await api.searchFiles(q, ext || undefined, folder || undefined)));
  const switchTab = (t: Tab) => {
    setTab(t);
    setResults(null);
    setGroups(null);
    if (t === "large") run(async () => setResults(await api.largeFiles(minMb)));
    if (t === "duplicates") run(async () => setGroups(await api.duplicates()));
    if (t === "old") run(async () => setResults(await api.staleFiles(days)));
  };

  const organize = (path: string) =>
    run(async () => {
      const out = await api.organizePreview(path);
      if (out.action) setProposal(out.action);
      else toast(out.summary, "info");
    });

  const noFolders = folders.data && folders.data.length === 0;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader
        title="Files"
        subtitle="Search and organize files in the folders you've approved."
        actions={
          <button className="btn-outline" disabled={busy} onClick={() => run(async () => { await api.reindex(); folders.reload(); toast("Index updated"); })}>
            <RefreshCw size={14} /> Re-index
          </button>
        }
      />
      <ErrorBox error={error ?? folders.error} />

      {noFolders && (
        <div className="card mb-4 p-4 text-sm">
          No folders approved yet. <button className="text-accent hover:underline" onClick={() => navigate("/settings")}>Approve a folder in Settings</button>{" "}
          so LifeOS can index it.
        </div>
      )}

      {folders.data && folders.data.length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {folders.data.map((f) => (
            <div key={f.id} className="card flex items-center gap-3 p-3">
              <FolderTree size={18} className="text-accent" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium" title={f.path}>{f.label || f.path}</div>
                <div className="text-xs text-muted">{f.file_count.toLocaleString()} files</div>
              </div>
              <button className="btn-ghost text-xs" disabled={busy} onClick={() => organize(f.path)} title="Propose an organization plan">
                <Wand2 size={14} /> Organize
              </button>
            </div>
          ))}
        </div>
      )}

      {proposal && (
        <div className="mb-4">
          <ApprovalCard action={proposal} onChange={(a) => { if (a.status !== "pending") folders.reload(); }} />
        </div>
      )}

      <div className="mb-3 flex gap-1 border-b border-line">
        {([
          ["search", "Search", Search],
          ["large", "Large", HardDrive],
          ["duplicates", "Duplicates", Copy],
          ["old", "Old / forgotten", Archive],
        ] as const).map(([t, label, Icon]) => (
          <button
            key={t}
            onClick={() => switchTab(t)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${
              tab === t ? "border-accent text-text" : "border-transparent text-muted hover:text-text"
            }`}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {tab === "search" && (
        <form className="mb-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); search(); }}>
          <input className="input min-w-64 flex-1" placeholder="Words in the name or content, e.g. javascript testing" value={q} onChange={(e) => setQ(e.target.value)} />
          <input className="input w-36" placeholder="Types: pdf,docx" value={ext} onChange={(e) => setExt(e.target.value)} />
          <select className="input w-44" value={folder} onChange={(e) => setFolder(e.target.value)}>
            <option value="">All folders</option>
            {(folders.data ?? []).map((f) => <option key={f.id} value={f.path}>{f.label || f.path}</option>)}
          </select>
          <button className="btn-primary" disabled={busy}><Search size={14} /> Search</button>
        </form>
      )}
      {tab === "large" && (
        <div className="mb-3 flex items-center gap-2 text-sm">
          Larger than
          <input type="number" className="input w-24" value={minMb} onChange={(e) => setMinMb(Number(e.target.value))} /> MB
          <button className="btn-outline" onClick={() => switchTab("large")}>Apply</button>
        </div>
      )}
      {tab === "old" && (
        <div className="mb-3 flex items-center gap-2 text-sm">
          Not modified for
          <input type="number" className="input w-24" value={days} onChange={(e) => setDays(Number(e.target.value))} /> days
          <button className="btn-outline" onClick={() => switchTab("old")}>Apply</button>
        </div>
      )}

      <div className="card p-2">
        {busy && <div className="flex justify-center p-6"><Spinner /></div>}
        {!busy && results && results.length === 0 && <Empty>No files found.</Empty>}
        {!busy && results && (
          <>
            {results.length > 0 && tab !== "search" && (
              <div className="px-2 py-1 text-xs text-muted">{results.length} files · {bytes(results.reduce((s, f) => s + f.size, 0))}</div>
            )}
            {results.map((f) => <FileRow key={f.id} file={f} />)}
          </>
        )}
        {!busy && groups && groups.length === 0 && <Empty>No duplicate files.</Empty>}
        {!busy && groups?.map((g) => (
          <div key={g.sha256} className="m-1 rounded-lg border border-line p-2">
            <div className="px-2 text-xs text-muted">
              {g.files.length} identical copies · {bytes(g.size)} each · {bytes(g.size * (g.files.length - 1))} reclaimable
            </div>
            {g.files.map((f) => <FileRow key={f.id} file={f} compact />)}
          </div>
        ))}
        {!busy && !results && !groups && <Empty>Search your approved folders, or pick a tab above.</Empty>}
      </div>
    </div>
  );
}
