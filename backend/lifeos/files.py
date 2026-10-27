"""File indexing, search, analysis and reversible organization inside approved folders."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config, db, permissions

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".Trash", "$RECYCLE.BIN"}

TEXT_EXTS = {"txt", "md", "markdown", "csv", "json", "yaml", "yml", "toml", "ini", "log", "html", "htm", "xml",
             "py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "h", "hpp", "cs", "go", "rs", "rb", "php",
             "sh", "sql", "css", "scss", "kt", "swift", "r", "tex"}

TYPE_CATEGORIES: dict[str, set[str]] = {
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "svg", "tiff", "raw"},
    "Videos": {"mp4", "mov", "avi", "mkv", "webm", "wmv", "m4v"},
    "Audio": {"mp3", "wav", "flac", "aac", "ogg", "m4a"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"},
    "Installers": {"exe", "msi", "dmg", "pkg", "deb", "rpm", "appimage", "apk"},
    "Spreadsheets": {"xls", "xlsx", "ods", "csv", "numbers"},
    "Presentations": {"ppt", "pptx", "odp", "key"},
    "Code": {"py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "h", "go", "rs", "rb", "php", "sh", "sql", "ipynb"},
    "Documents": {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "epub", "pages"},
}

# Topic keywords used to categorize documents by content when no model is available.
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Finance": ["invoice", "receipt", "bank", "statement", "tax", "payment", "salary", "payslip", "budget",
                "expense", "billing", "refund", "transaction", "insurance", "loan"],
    "Programming": ["javascript", "typescript", "python", "java", "programming", "code", "api", "algorithm",
                    "software", "developer", "react", "testing", "jest", "unit test", "git", "docker",
                    "database", "sql", "function", "compiler", "rust", "html", "css"],
    "Study": ["lecture", "assignment", "homework", "course", "syllabus", "exam", "quiz", "university",
              "semester", "chapter", "textbook", "notes", "thesis", "research", "study", "tutorial", "paper"],
    "Work": ["meeting", "proposal", "report", "project plan", "agenda", "contract", "client", "quarterly",
             "roadmap", "presentation", "minutes", "policy", "resume", "cv", "offer letter"],
    "Travel": ["flight", "boarding", "hotel", "itinerary", "booking", "reservation", "passport", "visa", "ticket"],
    "Health": ["medical", "prescription", "doctor", "clinic", "health", "lab results", "appointment", "vaccine"],
    "Manuals": ["manual", "user guide", "instructions", "warranty", "quick start", "datasheet", "specification"],
}

IMPORTANT_KEYWORDS = ["deadline", "due date", "due by", "urgent", "important", "action required", "invoice",
                      "contract", "agreement", "tax", "exam", "assignment", "payment", "overdue", "sign",
                      "offer letter", "application", "renewal", "expires"]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- extraction

def extract_text(path: Path, max_chars: int = config.MAX_EXCERPT_CHARS) -> str:
    """Best-effort plain-text extraction for supported formats. Never raises."""
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext == "pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            parts: list[str] = []
            meta = reader.metadata
            if meta and meta.title:
                parts.append(str(meta.title))
            for page in reader.pages[:5]:
                parts.append(page.extract_text() or "")
                if sum(len(p) for p in parts) > max_chars:
                    break
            text = "\n".join(parts)
        elif ext == "docx":
            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", "ignore")
            text = re.sub(r"<w:p[ >].*?</w:p>", lambda m: re.sub(r"<[^>]+>", "", m.group(0)) + "\n", xml, flags=re.S)
            text = re.sub(r"<[^>]+>", "", text)
        elif ext in TEXT_EXTS:
            with open(path, "rb") as fh:
                text = fh.read(min(config.MAX_TEXT_BYTES, max_chars * 4)).decode("utf-8", "ignore")
        else:
            return ""
    except Exception:  # corrupt or encrypted documents are indexed by name only
        return ""
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- indexing

def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.startswith("."):
                yield Path(dirpath) / name


def index_folder(folder_id: int, root: Path) -> dict[str, int]:
    """(Re)index one approved folder. Unchanged files keep their extracted text."""
    seen: set[str] = set()
    added = updated = 0
    with db.session() as conn:
        existing = {r["path"]: (r["size"], r["modified_at"]) for r in
                    conn.execute("SELECT path, size, modified_at FROM files WHERE folder_id = ?", (folder_id,))}
        for count, path in enumerate(_walk(root)):
            if count >= config.MAX_INDEX_FILES:
                break
            try:
                if path.is_symlink():
                    continue
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            seen.add(key)
            mtime = _iso(st.st_mtime)
            if existing.get(key) == (st.st_size, mtime):
                continue
            ext = path.suffix.lower().lstrip(".")
            excerpt = extract_text(path) if st.st_size <= 200 * 1024 * 1024 else ""
            category = categorize(path.name, ext, excerpt)
            conn.execute(
                """INSERT INTO files(path, folder_id, name, ext, size, modified_at, accessed_at, category, excerpt, indexed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET size=excluded.size, modified_at=excluded.modified_at,
                     accessed_at=excluded.accessed_at, category=excluded.category, excerpt=excluded.excerpt,
                     sha256=NULL, indexed_at=excluded.indexed_at, folder_id=excluded.folder_id""",
                (key, folder_id, path.name, ext, st.st_size, mtime, _iso(st.st_atime), category, excerpt, db.now()),
            )
            if key in existing:
                updated += 1
            else:
                added += 1
        removed = [p for p in existing if p not in seen]
        conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in removed])
        conn.execute("UPDATE approved_folders SET last_indexed_at = ? WHERE id = ?", (db.now(), folder_id))
    return {"added": added, "updated": updated, "removed": len(removed), "total": len(seen)}


def index_all() -> dict[str, Any]:
    results = {}
    for fid, root in permissions.approved_roots():
        if root.is_dir():
            results[str(root)] = index_folder(fid, root)
    return results


def ensure_indexed(folder_id: int | None = None) -> None:
    """Index folders that were never indexed (cheap guard before searches)."""
    with db.session() as conn:
        rows = conn.execute("SELECT id, path FROM approved_folders WHERE last_indexed_at IS NULL"
                            + (" AND id = ?" if folder_id else ""), ((folder_id,) if folder_id else ())).fetchall()
    for r in rows:
        if Path(r["path"]).is_dir():
            index_folder(r["id"], Path(r["path"]))


# ---------------------------------------------------------------- classification

def type_category(ext: str) -> str:
    for cat, exts in TYPE_CATEGORIES.items():
        if ext in exts:
            return cat
    return "Other"


def topic_category(name: str, excerpt: str) -> str | None:
    text = f"{name.lower()} {excerpt.lower()[:3000]}"
    text = re.sub(r"[_\-.]+", " ", text)
    scores: dict[str, int] = {}
    for topic, words in TOPIC_KEYWORDS.items():
        score = 0
        for w in words:
            hits = len(re.findall(rf"\b{re.escape(w)}", text))
            # Filename matches are a stronger signal than body text.
            score += hits + (2 if re.search(rf"\b{re.escape(w)}", re.sub(r"[_\-.]+", " ", name.lower())) else 0)
        if score:
            scores[topic] = score
    if not scores:
        return None
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0]


def categorize(name: str, ext: str, excerpt: str) -> str:
    base = type_category(ext)
    if base in ("Documents", "Presentations", "Spreadsheets"):
        return topic_category(name, excerpt) or base
    return base


def is_important(name: str, excerpt: str) -> bool:
    text = f"{name} {excerpt[:3000]}".lower().replace("_", " ").replace("-", " ")
    return any(k in text for k in IMPORTANT_KEYWORDS)


# ---------------------------------------------------------------- queries

def _file_out(row: Any) -> dict[str, Any]:
    d = dict(row)
    d.pop("sha256", None)
    excerpt = d.pop("excerpt", None) or ""
    d["snippet"] = excerpt[:240]
    return d


def search(query: str = "", *, extensions: list[str] | None = None, folder: str | None = None,
           modified_after: str | None = None, modified_before: str | None = None,
           min_size_mb: float | None = None, category: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    folder_id = None
    if folder:
        match = permissions.folder_by_name(folder)
        if match is None:
            folder_id, _ = permissions.resolve_allowed(folder)
        else:
            folder_id = match[0]
    ensure_indexed(folder_id)
    where, params = [], []
    if folder_id:
        where.append("f.folder_id = ?")
        params.append(folder_id)
    if extensions:
        exts = [e.lower().lstrip(".") for e in extensions]
        where.append(f"f.ext IN ({','.join('?' * len(exts))})")
        params.extend(exts)
    if modified_after:
        where.append("f.modified_at >= ?")
        params.append(modified_after)
    if modified_before:
        where.append("f.modified_at < ?")
        params.append(modified_before)
    if min_size_mb:
        where.append("f.size >= ?")
        params.append(int(min_size_mb * 1024 * 1024))
    if category:
        where.append("lower(f.category) = lower(?)")
        params.append(category)
    fts = db.fts_query(query) if query.strip() else ""
    with db.session() as conn:
        if fts:
            sql = ("SELECT f.*, bm25(files_fts, 10.0, 2.0, 1.0) AS score FROM files_fts JOIN files f ON f.id = files_fts.rowid "
                   "WHERE files_fts MATCH ?" + "".join(f" AND {w}" for w in where) + " ORDER BY score LIMIT ?")
            rows = conn.execute(sql, [fts, *params, limit]).fetchall()
        else:
            sql = "SELECT f.* FROM files f" + (" WHERE " + " AND ".join(where) if where else "") + \
                  " ORDER BY f.modified_at DESC LIMIT ?"
            rows = conn.execute(sql, [*params, limit]).fetchall()
    return [_file_out(r) for r in rows]


def large_files(min_mb: float = 100, older_than_days: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    ensure_indexed()
    params: list[Any] = [int(min_mb * 1024 * 1024)]
    sql = "SELECT * FROM files WHERE size >= ?"
    if older_than_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
        sql += " AND coalesce(accessed_at, modified_at) < ?"
        params.append(cutoff)
    sql += " ORDER BY size DESC LIMIT ?"
    params.append(limit)
    with db.session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_file_out(r) for r in rows]


def stale_files(days: int = 180, limit: int = 50) -> list[dict[str, Any]]:
    ensure_indexed()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM files WHERE modified_at < ? ORDER BY modified_at ASC LIMIT ?",
                            (cutoff, limit)).fetchall()
    return [_file_out(r) for r in rows]


def duplicates(folder: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Groups of byte-identical files. Hashes only files that share a size."""
    ensure_indexed()
    params: list[Any] = []
    folder_clause = ""
    if folder:
        match = permissions.folder_by_name(folder) or permissions.resolve_allowed(folder)
        folder_clause = " AND folder_id = ?"
        params.append(match[0])
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE size > 0" + folder_clause +
            " AND size IN (SELECT size FROM files WHERE size > 0" + folder_clause + " GROUP BY size HAVING COUNT(*) > 1)",
            params * 2,
        ).fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            digest = r["sha256"]
            if not digest:
                try:
                    digest = sha256(Path(r["path"]))
                except OSError:
                    continue
                conn.execute("UPDATE files SET sha256 = ? WHERE id = ?", (digest, r["id"]))
            groups.setdefault(digest, []).append(_file_out(r))
    out = [{"sha256": h, "size": g[0]["size"], "files": sorted(g, key=lambda f: f["modified_at"])}
           for h, g in groups.items() if len(g) > 1]
    out.sort(key=lambda g: g["size"] * (len(g["files"]) - 1), reverse=True)
    return out[:limit]


def read_document(path: str, max_chars: int = 8000) -> dict[str, Any]:
    _, resolved = permissions.resolve_allowed(path)
    if not resolved.is_file():
        raise ValueError(f"Not a file: {resolved}")
    st = resolved.stat()
    return {"path": str(resolved), "name": resolved.name, "size": st.st_size,
            "modified_at": _iso(st.st_mtime), "text": extract_text(resolved, max_chars)}


# ---------------------------------------------------------------- organization

def _unique_dest(dest: Path, taken: set[Path]) -> Path:
    candidate, n = dest, 1
    while candidate.exists() or candidate in taken:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    return candidate


def plan_organization(folder: str, *, extensions: list[str] | None = None, strategy: str = "category",
                      recursive: bool = False, categories: dict[str, str] | None = None) -> dict[str, Any]:
    """Build (but do not execute) a move plan for files in an approved folder.

    `categories` optionally overrides the category for specific paths, e.g. from a model.
    """
    match = permissions.folder_by_name(folder)
    folder_id, root = match if match else permissions.resolve_allowed(folder)
    if not root.is_dir():
        raise ValueError(f"Not a folder: {root}")
    _reindex({folder_id})
    exts = {e.lower().lstrip(".") for e in extensions} if extensions else None
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM files WHERE path LIKE ? ORDER BY name", (str(root) + os.sep + "%",)).fetchall()
    moves, taken = [], set()
    for r in rows:
        src = Path(r["path"])
        if root not in src.parents or (not recursive and src.parent != root):
            continue
        if exts and r["ext"] not in exts:
            continue
        if strategy == "type":
            cat = type_category(r["ext"])
        elif strategy == "date":
            cat = r["modified_at"][:7]  # YYYY-MM
        else:
            cat = (categories or {}).get(str(src)) or r["category"] or type_category(r["ext"])
        cat = re.sub(r'[<>:"/\\|?*]', "", cat).strip() or "Other"
        if src.parent.name == cat:
            continue
        dest = _unique_dest(root / cat / src.name, taken)
        taken.add(dest)
        moves.append({"src": str(src), "dest": str(dest), "category": cat, "size": r["size"],
                      "important": is_important(r["name"], r["excerpt"] or "")})
    summary: dict[str, int] = {}
    for m in moves:
        summary[m["category"]] = summary.get(m["category"], 0) + 1
    return {"folder": str(root), "strategy": strategy, "moves": moves, "by_category": summary}


def apply_moves(moves: list[dict[str, str]]) -> dict[str, Any]:
    """Execute a move plan. Re-validates every path; never overwrites. Returns what actually moved."""
    done, skipped = [], []
    touched_folders: set[int] = set()
    for m in moves:
        try:
            fid_src, src = permissions.resolve_allowed(m["src"])
            fid_dst, dest_parent = permissions.resolve_allowed(Path(m["dest"]).parent, must_exist=False)
            dest = dest_parent / Path(m["dest"]).name
            if not src.is_file():
                raise FileNotFoundError(str(src))
            dest = _unique_dest(dest, set())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            done.append({"src": str(src), "dest": str(dest)})
            touched_folders.update({fid_src, fid_dst})
        except (OSError, permissions.PermissionDenied) as exc:
            skipped.append({**m, "error": str(exc)})
    _reindex(touched_folders)
    return {"moved": done, "skipped": skipped}


def undo_moves(moved: list[dict[str, str]]) -> dict[str, Any]:
    restored, failed = [], []
    touched: set[int] = set()
    for m in reversed(moved):
        try:
            fid, dest = permissions.resolve_allowed(m["dest"])
            _, src_parent = permissions.resolve_allowed(Path(m["src"]).parent, must_exist=False)
            src = src_parent / Path(m["src"]).name
            if src.exists():
                raise FileExistsError(f"{src} already exists")
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
            restored.append(m)
            touched.add(fid)
            # Remove category folders the plan created, if now empty.
            try:
                dest.parent.rmdir()
            except OSError:
                pass
        except (OSError, permissions.PermissionDenied) as exc:
            failed.append({**m, "error": str(exc)})
    _reindex(touched)
    return {"restored": restored, "failed": failed}


def create_folder(path: str) -> dict[str, Any]:
    fid, resolved = permissions.resolve_allowed(path, must_exist=False)
    existed = resolved.exists()
    resolved.mkdir(parents=True, exist_ok=True)
    return {"path": str(resolved), "created": not existed}


def _reindex(folder_ids: set[int]) -> None:
    roots = dict(permissions.approved_roots())
    for fid in folder_ids:
        if fid in roots and roots[fid].is_dir():
            index_folder(fid, roots[fid])


def open_path(path: str) -> dict[str, Any]:
    """Open a file with the OS default application (desktop only)."""
    import subprocess
    import sys

    _, resolved = permissions.resolve_allowed(path)
    if sys.platform.startswith("win"):
        os.startfile(str(resolved))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
    else:
        subprocess.Popen(["xdg-open", str(resolved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": str(resolved)}
