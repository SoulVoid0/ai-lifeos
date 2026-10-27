"""Proposed actions awaiting approval, their execution, and undo."""
from __future__ import annotations

import json
from typing import Any

from . import activity, db

STATUSES = ("pending", "running", "executed", "rejected", "superseded", "failed", "undoing", "undone")


def _get(conn: Any, action_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise KeyError(f"Action {action_id} not found")
    return db.row_to_dict(row, ("args", "preview", "result"))  # type: ignore[return-value]


def get(action_id: int) -> dict[str, Any]:
    with db.session() as conn:
        return _get(conn, action_id)


def create(tool: str, args: dict[str, Any], summary: str, preview: dict[str, Any],
           conversation_id: int | None = None) -> dict[str, Any]:
    with db.session() as conn:
        if preview.get("folder"):
            # A fresh plan for the same folder replaces older unapproved ones, which would be stale.
            for row in conn.execute("SELECT id, preview FROM actions WHERE status = 'pending' AND tool = ?", (tool,)).fetchall():
                if json.loads(row["preview"] or "{}").get("folder") == preview["folder"]:
                    conn.execute("UPDATE actions SET status = 'superseded', decided_at = ? WHERE id = ?", (db.now(), row["id"]))
        cur = conn.execute(
            "INSERT INTO actions(tool, args, summary, preview, status, conversation_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (tool, json.dumps(args), summary, json.dumps(preview, default=str), "pending", conversation_id, db.now()),
        )
        action = _get(conn, int(cur.lastrowid))
    activity.log("proposal", f"Proposed: {summary}", tool=tool, details={"args": args}, status="pending",
                 action_id=action["id"])
    return action


def list_actions(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with db.session() as conn:
        if status:
            rows = conn.execute("SELECT * FROM actions WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return db.rows_to_dicts(rows, ("args", "preview", "result"))


def _claim(action_id: int, from_status: str, to_status: str) -> dict[str, Any]:
    """Atomically move an action between states so double-clicks can't execute twice."""
    with db.session() as conn:
        cur = conn.execute("UPDATE actions SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
                           (to_status, db.now(), action_id, from_status))
        action = _get(conn, action_id)
        if cur.rowcount != 1:
            raise ValueError(f"Action {action_id} is {action['status']}, not {from_status}")
    return action


def approve(action_id: int, options: dict[str, Any] | None = None, *, auto: bool = False) -> dict[str, Any]:
    from .tools import TOOLS

    action = _claim(action_id, "pending", "running")
    tool = TOOLS[action["tool"]]
    try:
        if tool.execute is not None:
            result = tool.execute(action["args"], action["preview"] or {}, options or {})
        else:
            from .tools import run_queued

            result = run_queued(action["tool"], action["preview"] or {})
        status, error = "executed", None
    except Exception as exc:  # report every failure back to the user
        result, status, error = None, "failed", str(exc)
    with db.session() as conn:
        conn.execute("UPDATE actions SET status = ?, result = ?, error = ?, executed_at = ? WHERE id = ?",
                     (status, json.dumps(result, default=str) if result else None, error, db.now(), action_id))
        action = _get(conn, action_id)
    how = "Auto-ran" if auto else "Approved and ran"
    activity.log("action", f"{how}: {(result or {}).get('summary') or action['summary']}" if status == "executed"
                 else f"Failed: {action['summary']} ({error})", tool=action["tool"],
                 details={"options": options, "result": result}, status="ok" if status == "executed" else "error",
                 action_id=action_id)
    return action


def reject(action_id: int) -> dict[str, Any]:
    action = _claim(action_id, "pending", "rejected")
    activity.log("decision", f"Rejected: {action['summary']}", tool=action["tool"], status="rejected", action_id=action_id)
    return action


def undo(action_id: int) -> dict[str, Any]:
    from .tools import TOOLS

    action = get(action_id)
    tool = TOOLS[action["tool"]]
    if action["status"] != "executed" or tool.undo is None:
        raise ValueError("This action cannot be undone")
    _claim(action_id, "executed", "undoing")
    try:
        out = tool.undo(action["result"] or {})
    except Exception as exc:
        with db.session() as conn:
            conn.execute("UPDATE actions SET status = 'executed' WHERE id = ?", (action_id,))
        raise ValueError(f"Undo failed: {exc}") from exc
    with db.session() as conn:
        conn.execute("UPDATE actions SET status = 'undone' WHERE id = ?", (action_id,))
        action = _get(conn, action_id)
    activity.log("undo", out["summary"], tool=action["tool"], details=out, action_id=action_id)
    action["undo"] = out
    return action
