"""HTTP API consumed by the desktop/web UI. Binds to localhost only."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (__version__, actions, activity, agent, config, conversations, files, llm, memory, permissions,
               proactive, scheduler, search, settings, tasks, tools)

log = logging.getLogger("lifeos")
_scheduler = scheduler.Scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("LIFEOS_NO_SCHEDULER"):
        _scheduler.start()
    scheduler.watch_parent()
    yield
    _scheduler.stop()


app = FastAPI(title="AI LifeOS", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def require_client_header(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health" and request.method != "OPTIONS" \
            and request.headers.get(config.CLIENT_HEADER) != "1":
        return JSONResponse({"detail": f"Missing {config.CLIENT_HEADER} header"}, status_code=403)
    return await call_next(request)


@app.exception_handler(KeyError)
async def not_found(_: Request, exc: KeyError):
    return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=404)


@app.exception_handler(ValueError)
async def bad_request(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(permissions.PermissionDenied)
async def forbidden(_: Request, exc: permissions.PermissionDenied):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(FileNotFoundError)
async def missing_file(_: Request, exc: FileNotFoundError):
    return JSONResponse({"detail": f"File not found: {exc}"}, status_code=404)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    # Surface the real cause in the UI instead of a bare "Internal Server Error".
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    activity.log("error", f"{request.method} {request.url.path} failed: {type(exc).__name__}: {exc}"[:500],
                 status="error")
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ---------------------------------------------------------------- schemas

class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: int | None = None


class ApproveIn(BaseModel):
    exclude: list[str] = []


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    notes: str | None = None
    project: str | None = None
    priority: int | str = 2
    due_at: str | None = None
    remind_at: str | None = None
    estimate_minutes: int | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    project: str | None = None
    priority: int | str | None = None
    status: str | None = None
    due_at: str | None = None
    remind_at: str | None = None
    estimate_minutes: int | None = None


class MemoryIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    kind: str = "fact"
    pinned: bool = False


class MemoryPatch(BaseModel):
    content: str | None = None
    kind: str | None = None
    pinned: bool | None = None


class FolderIn(BaseModel):
    path: str
    label: str | None = None


class PathIn(BaseModel):
    path: str


class PauseIn(BaseModel):
    paused: bool


class RenameIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@app.get("/api/dashboard")
def get_dashboard() -> dict[str, Any]:
    return proactive.dashboard()


@app.post("/api/chat")
def chat(body: ChatIn) -> dict[str, Any]:
    return agent.handle(body.message, body.conversation_id)


@app.get("/api/conversations")
def list_conversations() -> list[dict[str, Any]]:
    return conversations.list_all()


@app.get("/api/conversations/{cid}/messages")
def conversation_messages(cid: int) -> list[dict[str, Any]]:
    conversations.get(cid)
    return conversations.messages(cid)


@app.patch("/api/conversations/{cid}")
def rename_conversation(cid: int, body: RenameIn) -> dict[str, Any]:
    return conversations.rename(cid, body.title)


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: int) -> dict[str, Any]:
    conversations.get(cid)
    conversations.delete(cid)
    activity.log("data", f"Deleted conversation {cid}")
    return {"ok": True}


@app.get("/api/actions")
def list_actions(status: str | None = None) -> list[dict[str, Any]]:
    return actions.list_actions(status)


@app.post("/api/actions/{aid}/approve")
def approve_action(aid: int, body: ApproveIn | None = None) -> dict[str, Any]:
    return actions.approve(aid, {"exclude": body.exclude if body else []})


@app.post("/api/actions/{aid}/reject")
def reject_action(aid: int) -> dict[str, Any]:
    return actions.reject(aid)


@app.post("/api/actions/{aid}/undo")
def undo_action(aid: int) -> dict[str, Any]:
    return actions.undo(aid)


@app.get("/api/tasks")
def list_tasks(status: str = "open") -> list[dict[str, Any]]:
    return tasks.list_tasks(status)


@app.post("/api/tasks")
def create_task(body: TaskIn) -> dict[str, Any]:
    t = tasks.create(body.title, notes=body.notes, project=body.project, priority=body.priority,
                     due=body.due_at, remind=body.remind_at, estimate_minutes=body.estimate_minutes)
    activity.log("data", f"Created task \"{t['title']}\"", tool="create_task")
    return t


@app.patch("/api/tasks/{tid}")
def patch_task(tid: int, body: TaskPatch) -> dict[str, Any]:
    return tasks.update(tid, body.model_dump(exclude_unset=True))


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int) -> dict[str, Any]:
    t = tasks.delete(tid)
    activity.log("data", f"Deleted task \"{t['title']}\"")
    return t


@app.get("/api/tasks/plan")
def plan(hours: float = 6) -> dict[str, Any]:
    return tasks.plan_day(int(hours * 60))


@app.get("/api/memories")
def list_memories(q: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    return memory.search(q, 50) if q else memory.list_all(kind)


@app.post("/api/memories")
def add_memory(body: MemoryIn) -> dict[str, Any]:
    m = memory.add(body.content, body.kind, source="user", pinned=body.pinned)
    activity.log("memory", f"Saved memory: {m['content'][:120]}")
    return m


@app.patch("/api/memories/{mid}")
def patch_memory(mid: int, body: MemoryPatch) -> dict[str, Any]:
    m = memory.update(mid, body.content, body.kind, body.pinned)
    activity.log("memory", f"Edited memory {mid}")
    return m


@app.delete("/api/memories/{mid}")
def delete_memory(mid: int) -> dict[str, Any]:
    m = memory.delete(mid)
    activity.log("memory", f"Deleted memory: {m['content'][:120]}")
    return m


@app.get("/api/files/search")
def search_files(q: str = "", ext: str | None = None, folder: str | None = None, category: str | None = None,
                 limit: int = 50) -> list[dict[str, Any]]:
    exts = [e for e in (ext or "").split(",") if e] or None
    return files.search(q, extensions=exts, folder=folder, category=category, limit=min(limit, 500))


@app.get("/api/files/large")
def large(min_mb: float = 100, older_than_days: int | None = None) -> list[dict[str, Any]]:
    return files.large_files(min_mb, older_than_days)


@app.get("/api/files/duplicates")
def dupes(folder: str | None = None) -> list[dict[str, Any]]:
    return files.duplicates(folder)


@app.get("/api/files/stale")
def stale(days: int = 180) -> list[dict[str, Any]]:
    return files.stale_files(days)


@app.get("/api/files/read")
def read_file(path: str) -> dict[str, Any]:
    return files.read_document(path)


@app.post("/api/files/open")
def open_file(body: PathIn) -> dict[str, Any]:
    out = files.open_path(body.path)
    activity.log("tool", f"Opened {out['opened']}", tool="open_file")
    return out


@app.post("/api/files/index")
def reindex() -> dict[str, Any]:
    out = files.index_all()
    activity.log("tool", "Re-indexed approved folders", tool="index", details=out)
    return out


@app.post("/api/files/organize/preview")
def organize_preview(body: FolderIn) -> dict[str, Any]:
    """Propose an organization plan from the Files page. Returns the pending action."""
    out = tools.invoke("organize_files", {"folder": body.path}, {})
    if out["status"] == "error":
        raise ValueError(out["summary"])
    return out


@app.get("/api/folders")
def list_folders() -> list[dict[str, Any]]:
    return permissions.list_folders()


@app.post("/api/folders")
def add_folder(body: FolderIn) -> dict[str, Any]:
    folder = permissions.add_folder(body.path, body.label)
    files.index_folder(folder["id"], Path(folder["path"]))
    return next(f for f in permissions.list_folders() if f["id"] == folder["id"])


@app.delete("/api/folders/{fid}")
def remove_folder(fid: int) -> dict[str, Any]:
    permissions.remove_folder(fid)
    return {"ok": True}


@app.get("/api/search")
def universal_search(q: str) -> dict[str, Any]:
    return search.everything(q, 15)


@app.get("/api/activity")
def get_activity(limit: int = 200, kind: str | None = None) -> list[dict[str, Any]]:
    return activity.recent(min(limit, 1000), kind)


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings.get_all()


@app.patch("/api/settings")
def patch_settings(body: dict[str, Any]) -> dict[str, Any]:
    out = settings.update(body)
    activity.log("settings", "Updated settings: " + ", ".join(sorted(body)), details=body)
    return out


@app.post("/api/settings/pause")
def pause(body: PauseIn) -> dict[str, Any]:
    out = settings.update({"automation_paused": body.paused})
    activity.log("settings", "Automation paused: all actions now need approval" if body.paused else "Automation resumed")
    return out


@app.get("/api/tools")
def list_tools() -> list[dict[str, Any]]:
    return tools.describe()


@app.get("/api/llm/status")
def llm_status() -> dict[str, Any]:
    return llm.status(force=True)


@app.get("/api/suggestions")
def get_suggestions() -> list[dict[str, Any]]:
    return proactive.suggestions()


@app.post("/api/suggestions/{sid}/dismiss")
def dismiss_suggestion(sid: str) -> dict[str, Any]:
    proactive.dismiss(sid)
    return {"ok": True}


@app.get("/api/notifications")
def get_notifications(all: bool = False) -> list[dict[str, Any]]:
    return scheduler.notifications(unread_only=not all)


@app.post("/api/notifications/read")
def read_notifications(notification_id: int | None = None) -> dict[str, Any]:
    scheduler.mark_read(notification_id)
    return {"ok": True}


# Serve the built web UI when present (browser mode without Tauri).
_dist = Path(os.environ.get("LIFEOS_WEB_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise KeyError(f"No such endpoint: /{full_path}")
        target = (_dist / full_path).resolve()
        if full_path and _dist in target.parents and target.is_file():
            return FileResponse(target)
        return FileResponse(_dist / "index.html")
