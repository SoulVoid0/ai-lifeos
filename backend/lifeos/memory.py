"""Long-term memory: user-approved facts, preferences and project context.

Search is hybrid: embedding similarity via the local model when available, blended with
FTS5 keyword ranking so it still works (and stays useful) without a model.
"""
from __future__ import annotations

from typing import Any

from . import db, llm

KINDS = ("preference", "project", "fact", "routine", "note", "person")


def _embed_one(text: str) -> tuple[bytes | None, str | None]:
    vectors = llm.embed([text])
    if not vectors:
        return None, None
    from . import settings

    return llm.pack(vectors[0]), settings.get("embed_model")


def get(memory_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        raise KeyError(f"Memory {memory_id} not found")
    return db.row_to_dict(row)  # type: ignore[return-value]


def add(content: str, kind: str = "fact", source: str | None = None, pinned: bool = False) -> dict[str, Any]:
    content = content.strip()
    if not content:
        raise ValueError("Memory content is required")
    kind = kind if kind in KINDS else "fact"
    with db.session() as conn:
        dup = conn.execute("SELECT id FROM memories WHERE lower(content) = lower(?)", (content,)).fetchone()
        if dup:
            return get(dup["id"])
    blob, model = _embed_one(content)
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO memories(kind, content, source, pinned, embedding, embedding_model, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (kind, content, source, int(pinned), blob, model, db.now(), db.now()),
        )
        mid = int(cur.lastrowid)
    return get(mid)


def update(memory_id: int, content: str | None = None, kind: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
    current = get(memory_id)
    fields: dict[str, Any] = {"updated_at": db.now()}
    if content is not None and content.strip() != current["content"]:
        fields["content"] = content.strip()
        fields["embedding"], fields["embedding_model"] = _embed_one(fields["content"])
    if kind is not None:
        fields["kind"] = kind if kind in KINDS else current["kind"]
    if pinned is not None:
        fields["pinned"] = int(pinned)
    with db.session() as conn:
        conn.execute(f"UPDATE memories SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     [*fields.values(), memory_id])
    return get(memory_id)


def delete(memory_id: int) -> dict[str, Any]:
    m = get(memory_id)
    with db.session() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return m


def list_all(kind: str | None = None) -> list[dict[str, Any]]:
    with db.session() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM memories WHERE kind = ? ORDER BY pinned DESC, updated_at DESC", (kind,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC").fetchall()
    return db.rows_to_dicts(rows)


def search(query: str, limit: int = 8, min_score: float = 0.0) -> list[dict[str, Any]]:
    scores: dict[int, float] = {}
    fts = db.fts_query(query)
    with db.session() as conn:
        if fts:
            for rank, row in enumerate(conn.execute(
                    "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT 50",
                    (fts,))):
                scores[row["rowid"]] = scores.get(row["rowid"], 0) + 0.5 / (1 + rank * 0.25)
        qvec = llm.embed([query])
        if qvec:
            for row in conn.execute("SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"):
                sim = llm.cosine(qvec[0], llm.unpack(row["embedding"]))
                if sim > 0.35:
                    scores[row["id"]] = scores.get(row["id"], 0) + sim
        ids = [i for i, s in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if s >= min_score][:limit]
        if not ids:
            return []
        rows = conn.execute(f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    by_id = {r["id"]: db.row_to_dict(r) for r in rows}
    out = []
    for i in ids:
        item = by_id[i]
        item["score"] = round(scores[i], 3)  # type: ignore[index]
        out.append(item)
    return out  # type: ignore[return-value]


def context_for(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Memories to inject into the assistant prompt: pinned ones plus the most relevant."""
    pinned = [m for m in list_all() if m["pinned"]][:5]
    relevant = search(query, limit)
    seen, out = set(), []
    for m in pinned + relevant:
        if m["id"] not in seen:
            seen.add(m["id"])
            out.append(m)
    return out[: limit + 5]


def reembed_missing() -> int:
    """Embed memories saved while the model was offline."""
    with db.session() as conn:
        rows = conn.execute("SELECT id, content FROM memories WHERE embedding IS NULL").fetchall()
    if not rows:
        return 0
    vectors = llm.embed([r["content"] for r in rows])
    if not vectors:
        return 0
    from . import settings

    model = settings.get("embed_model")
    with db.session() as conn:
        for r, v in zip(rows, vectors):
            conn.execute("UPDATE memories SET embedding = ?, embedding_model = ? WHERE id = ?", (llm.pack(v), model, r["id"]))
    return len(rows)
