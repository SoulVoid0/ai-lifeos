"""HTTP API consumed by the desktop/web UI. Binds to localhost only."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (__version__, actions, activity, agent, config, conversations, crew, files, integrations, lean, learning, llm, mailer, media, memory,
               missions, permissions, proactive, projects, scheduler, search, services, settings, tasks, tools, vault,
               updates, workflows)
from . import backup, cloud, custom_tools, renamer, workspaces

log = logging.getLogger("lifeos")
_scheduler = scheduler.Scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    crew.ensure_seeded()
    interrupted = missions.recover()
    if interrupted:
        activity.log("crew", f"Marked {interrupted} interrupted mission(s) as failed after a restart", status="error")
    if not os.environ.get("LIFEOS_NO_SCHEDULER"):
        _scheduler.start()
        workflows.run_startup()
    scheduler.watch_parent()
    if not os.environ.get("LIFEOS_NO_SERVICES"):
        services.start_background()
    yield
    _scheduler.stop()
    services.stop_all()


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


class AgentIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    slug: str | None = None
    role: str = "Specialist"
    description: str = ""
    persona: str = ""
    model: str = ""
    tools: list[str] = []
    color: str = "slate"
    icon: str = "bot"
    max_steps: int = 10
    candidates: list[str] = []
    voice: str = ""
    quirks: str = ""
    catchphrases: list[str] = []
    idle_lines: list[str] = []


class AgentPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    persona: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    color: str | None = None
    icon: str | None = None
    max_steps: int | None = None
    enabled: bool | None = None
    candidates: list[str] | None = None
    voice: str | None = None
    quirks: str | None = None
    catchphrases: list[str] | None = None
    idle_lines: list[str] | None = None


class LessonIn(BaseModel):
    agent: str = Field(..., min_length=1, max_length=40)
    text: str = Field(..., min_length=1, max_length=400)
    pinned: bool = False


class LessonPatch(BaseModel):
    text: str | None = Field(None, max_length=400)
    pinned: bool | None = None
    agent: str | None = None


class RatingIn(BaseModel):
    rating: int
    note: str = Field("", max_length=400)


class ChatTurn(BaseModel):
    role: str
    content: str = Field(..., max_length=4000)


class AgentChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatTurn] = []


class MissionIn(BaseModel):
    goal: str = Field(..., min_length=1, max_length=8000)
    agent: str | None = None


class AnswerIn(BaseModel):
    answer: str = Field(..., min_length=1, max_length=2000)


class ModelIn(BaseModel):
    model: str


class KeyIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=500)


class TextIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    trigger: dict[str, Any] = {"type": "manual"}
    conditions: list[dict[str, Any]] = []
    steps: list[dict[str, Any]]
    source_text: str | None = None


class ProjectIn(BaseModel):
    path: str
    name: str | None = None
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    pinned: bool | None = None


class SourceIn(BaseModel):
    location: str
    label: str | None = None


class EventIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    start: str
    end: str | None = None
    location: str | None = None
    description: str | None = None


class EmailIn(BaseModel):
    host: str
    port: int = 993
    username: str
    folder: str = "INBOX"
    password: str | None = None


class EmailAlertsIn(BaseModel):
    enabled: bool | None = None
    to: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    security: str | None = None
    events: dict[str, bool] | None = None
    password: str | None = None


class NameIn(BaseModel):
    name: str


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


# ---------------------------------------------------------------- cloud folders, workspaces, custom tools, backups

@app.get("/api/folders/cloud")
def cloud_folders() -> list[dict[str, Any]]:
    return cloud.folders()


class WorkspaceItemIn(BaseModel):
    kind: str
    target: str = Field(max_length=2000)
    label: str = Field(default="", max_length=80)


class WorkspaceIn(BaseModel):
    name: str = Field(max_length=60)
    items: list[WorkspaceItemIn] = Field(max_length=20)


class AutomateIn(BaseModel):
    time: str = "08:00"
    days: list[str] = ["mon", "tue", "wed", "thu", "fri"]


@app.get("/api/workspaces")
def list_workspaces() -> list[dict[str, Any]]:
    return workspaces.list_all()


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceIn) -> dict[str, Any]:
    return workspaces.save(body.model_dump())


@app.put("/api/workspaces/{wid}")
def update_workspace(wid: int, body: WorkspaceIn) -> dict[str, Any]:
    workspaces.get(wid)
    return workspaces.save(body.model_dump(), wid)


@app.delete("/api/workspaces/{wid}")
def delete_workspace(wid: int) -> dict[str, Any]:
    return workspaces.delete(wid)


@app.post("/api/workspaces/{wid}/open")
def open_workspace(wid: int) -> dict[str, Any]:
    return workspaces.open_workspace(wid)


@app.post("/api/workspaces/{wid}/automate")
def automate_workspace(wid: int, body: AutomateIn) -> dict[str, Any]:
    return workspaces.automate(wid, body.time, body.days)


class CustomToolIn(BaseModel):
    name: str = Field(max_length=40)
    kind: str
    description: str = Field(default="", max_length=300)
    config: dict[str, Any] = {}
    enabled: bool = True
    secret: str | None = Field(default=None, max_length=2000)


class CustomToolTestIn(BaseModel):
    input: str = Field(default="", max_length=4000)


@app.get("/api/custom-tools")
def list_custom_tools() -> list[dict[str, Any]]:
    return custom_tools.list_all()


@app.post("/api/custom-tools")
def create_custom_tool(body: CustomToolIn) -> dict[str, Any]:
    return custom_tools.save(body.model_dump())


@app.put("/api/custom-tools/{tid}")
def update_custom_tool(tid: int, body: CustomToolIn) -> dict[str, Any]:
    return custom_tools.save(body.model_dump(), tid)


@app.delete("/api/custom-tools/{tid}")
def delete_custom_tool(tid: int) -> dict[str, Any]:
    return custom_tools.delete(tid)


@app.get("/api/custom-tools/{tid}/export")
def export_custom_tool(tid: int) -> dict[str, Any]:
    return custom_tools.export(tid)


@app.post("/api/custom-tools/import")
def import_custom_tool(body: dict[str, Any]) -> dict[str, Any]:
    return custom_tools.import_tool(body)


@app.post("/api/custom-tools/{tid}/test")
def test_custom_tool(tid: int, body: CustomToolTestIn) -> dict[str, Any]:
    """Run a tool once from Settings (the user is pressing the button, so no approval step)."""
    t = custom_tools.get(tid)
    if t["kind"] == "webhook":
        return custom_tools.call_webhook(t, body.input)
    return {"response": custom_tools.run_prompt(t, body.input)}


class BackupIn(BaseModel):
    include_media: bool | None = None


class RestoreIn(BaseModel):
    path: str = Field(max_length=2000)


@app.get("/api/backups")
def list_backups() -> dict[str, Any]:
    return {"backups": backup.list_backups(), "pending_restore": backup.pending_restore()}


@app.post("/api/backups")
def create_backup(body: BackupIn) -> dict[str, Any]:
    return backup.create(body.include_media)


@app.post("/api/backups/restore")
def restore_backup(body: RestoreIn) -> dict[str, Any]:
    return backup.request_restore(body.path)


@app.post("/api/tasks/{task_id}/breakdown")
def break_down_task(task_id: int) -> dict[str, Any]:
    return tasks.break_down(task_id)


@app.post("/api/tasks/{task_id}/replan")
def replan_task(task_id: int) -> dict[str, Any]:
    return tasks.replan(task_id)


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


# ---------------------------------------------------------------- crew and missions

@app.get("/api/crew/agents")
def list_agents() -> list[dict[str, Any]]:
    return crew.list_agents()


@app.post("/api/crew/agents")
def create_agent(body: AgentIn) -> dict[str, Any]:
    return crew.create(body.model_dump())


@app.patch("/api/crew/agents/{slug}")
def patch_agent(slug: str, body: AgentPatch) -> dict[str, Any]:
    return crew.update(slug, body.model_dump(exclude_unset=True))


@app.delete("/api/crew/agents/{slug}")
def delete_agent(slug: str) -> dict[str, Any]:
    return crew.delete(slug)


@app.post("/api/crew/reset")
def reset_crew() -> list[dict[str, Any]]:
    return crew.reset_defaults()


@app.get("/api/crew/status")
def crew_status() -> dict[str, Any]:
    return {"agents": missions.crew_status(), "briefs": missions.open_briefs(),
            "active": missions.list_missions(20, "active"), "spend": llm.spend_today(),
            "pending_actions": actions.list_actions("pending", 50)}


@app.get("/api/crew/models")
def crew_models() -> dict[str, Any]:
    st = llm.status()
    return {"local": st["models"], "ollama_ok": st["ok"], "providers": llm.providers(),
            "cloud_enabled": settings.get("cloud_enabled"), "default": settings.get("chat_model"),
            "recommended": crew.RECOMMENDED}


@app.post("/api/crew/test-model")
def crew_test_model(body: ModelIn) -> dict[str, Any]:
    return llm.test_model(body.model)


@app.get("/api/crew/pack")
def export_pack() -> dict[str, Any]:
    return crew.export_pack()


@app.post("/api/crew/pack")
def import_pack(body: dict[str, Any]) -> dict[str, Any]:
    return crew.import_pack(body)


@app.get("/api/missions")
def list_missions(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return missions.list_missions(min(limit, 200), status)


@app.post("/api/missions")
def start_mission(body: MissionIn) -> dict[str, Any]:
    return missions.start(body.goal, body.agent or None)


@app.get("/api/missions/events")
def mission_events(after: int = 0, mission_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    return missions.events(after, mission_id, min(limit, 1000))


@app.post("/api/missions/stop-all")
def stop_all_missions() -> dict[str, Any]:
    return {"stopped": missions.stop_all()}


@app.get("/api/missions/{mid}")
def get_mission(mid: int) -> dict[str, Any]:
    return missions.get(mid)


@app.post("/api/missions/{mid}/cancel")
def cancel_mission(mid: int) -> dict[str, Any]:
    return missions.cancel(mid)


@app.post("/api/missions/tasks/{task_id}/rate")
def rate_task(task_id: int, body: RatingIn) -> dict[str, Any]:
    return learning.rate_task(task_id, body.rating, body.note)


@app.post("/api/crew/agents/{slug}/chat")
def chat_with_agent(slug: str, body: AgentChatIn) -> dict[str, Any]:
    return missions.talk(slug, body.message, [t.model_dump() for t in body.history])


@app.get("/api/crew/agents/{slug}/activity")
def agent_activity(slug: str) -> dict[str, Any]:
    return missions.agent_activity(slug)


class MediaIn(BaseModel):
    kind: str = "image"
    prompt: str = Field(..., min_length=3, max_length=2000)
    negative: str = Field("", max_length=500)
    size: str = "square"
    seconds: int = Field(4, ge=1, le=10)
    seed: int | None = None


@app.get("/api/system/gpu")
def gpu_status() -> dict[str, Any]:
    """What is using the GPU: models Ollama has loaded, and whether an image/video job is running."""
    models = llm.loaded_models()
    return {"models": models, "vram": sum(m["vram"] for m in models), "media_busy": media.gpu_busy()}


@app.get("/api/system/services")
def services_status() -> dict[str, Any]:
    return services.status()


@app.post("/api/system/services/{name}/start")
def start_service(name: str) -> dict[str, Any]:
    if name not in ("ollama", "comfyui"):
        raise KeyError(f"Unknown service {name}")
    ok = services.ensure_ollama() if name == "ollama" else services.ensure_comfy()
    return {"ok": ok, **services.status()}


class PullIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class LeanIn(BaseModel):
    download: bool = True


@app.get("/api/system/lean")
def lean_status() -> dict[str, Any]:
    return lean.status()


@app.post("/api/system/lean")
def lean_apply(body: LeanIn | None = None) -> dict[str, Any]:
    return lean.apply(download=(body or LeanIn()).download)


@app.post("/api/system/ollama/tuning")
def ollama_tuning_apply() -> dict[str, Any]:
    return {**services.apply_tuning(restart=True), **services.tuning_status()}


@app.get("/api/system/models")
def models_installed() -> dict[str, Any]:
    return {"models": lean.installed(), "pulls": lean.pulls()}


@app.post("/api/system/models/pull")
def models_pull(body: PullIn) -> dict[str, Any]:
    return lean.pull(body.model)


@app.delete("/api/system/models/{name:path}")
def models_remove(name: str) -> dict[str, Any]:
    return lean.remove(name)


@app.post("/api/system/shutdown")
def shutdown() -> dict[str, Any]:
    """Used by the desktop app on Quit: stop helper programs LifeOS started, then exit."""
    import threading

    services.stop_all()
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


class UpdateCfgIn(BaseModel):
    repo: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=500)


@app.get("/api/system/old-data")
def old_data() -> dict[str, Any]:
    return config.store_python_data()


@app.post("/api/system/old-data/import")
def import_old_data() -> dict[str, Any]:
    return config.request_store_python_import()


@app.get("/api/system/update/config")
def update_config() -> dict[str, Any]:
    return updates.cfg()


@app.patch("/api/system/update/config")
def set_update_config(body: UpdateCfgIn) -> dict[str, Any]:
    return updates.set_cfg(body.repo, body.token)


@app.get("/api/system/update/manifest")
def update_manifest() -> Response:
    """latest.json for the desktop app's updater (204 when there's no release yet)."""
    try:
        data = updates.manifest()
    except updates.UpdateError as e:
        return JSONResponse({"detail": str(e)}, status_code=502)
    return Response(status_code=204) if data is None else JSONResponse(data)


@app.get("/api/system/update/asset/{asset_id}")
def update_asset(asset_id: int) -> Response:
    try:
        client, r = updates.open_asset(asset_id)
    except updates.UpdateError as e:
        return JSONResponse({"detail": str(e)}, status_code=502)
    headers = {k: r.headers[k] for k in ("content-length",) if k in r.headers}
    return StreamingResponse(updates.iter_asset(client, r), media_type="application/octet-stream", headers=headers)


@app.get("/api/media/status")
def media_status() -> dict[str, Any]:
    return {**media.status(), "settings": media.cfg()}


@app.patch("/api/media/settings")
def media_settings(body: dict[str, Any]) -> dict[str, Any]:
    out = media.update_cfg(body)
    activity.log("settings", "Updated media settings: " + ", ".join(sorted(body)))
    return out


@app.get("/api/media")
def list_media(kind: str | None = None, agent: str | None = None, since: str | None = None,
               limit: int = 60) -> list[dict[str, Any]]:
    return media.list_media(min(limit, 500), kind, agent or None, since or None)


@app.post("/api/media")
def generate_media(body: MediaIn) -> dict[str, Any]:
    try:
        return media.generate(body.kind, body.prompt, negative=body.negative, size=body.size, seed=body.seed,
                              frames=body.seconds * 24 + 1, wait=False)
    except media.MediaError as exc:
        raise ValueError(str(exc)) from exc


@app.get("/api/media/{media_id}")
def get_media(media_id: int) -> dict[str, Any]:
    return media.get(media_id)


@app.get("/api/media/{media_id}/file")
def media_file(media_id: int) -> FileResponse:
    path, mime = media.file_for(media_id)
    return FileResponse(path, media_type=mime)


@app.post("/api/media/{media_id}/open")
def open_media(media_id: int) -> dict[str, Any]:
    path, _ = media.file_for(media_id)
    files.launch(path)
    return {"ok": True}


@app.delete("/api/media/{media_id}")
def delete_media(media_id: int) -> dict[str, Any]:
    return media.delete(media_id)


@app.get("/api/learning")
def learning_overview() -> dict[str, Any]:
    return learning.overview()


@app.get("/api/learning/lessons")
def list_lessons(agent: str | None = None) -> list[dict[str, Any]]:
    return learning.list_lessons(agent)


@app.post("/api/learning/lessons")
def add_lesson(body: LessonIn) -> dict[str, Any]:
    lesson = learning.add_lesson(body.agent, body.text, "user", pinned=body.pinned)
    if lesson is None:
        raise ValueError("A very similar lesson already exists for this agent")
    return lesson


@app.patch("/api/learning/lessons/{lesson_id}")
def patch_lesson(lesson_id: int, body: LessonPatch) -> dict[str, Any]:
    return learning.update_lesson(lesson_id, body.text, body.pinned, body.agent)


@app.delete("/api/learning/lessons/{lesson_id}")
def delete_lesson(lesson_id: int) -> dict[str, Any]:
    return learning.delete_lesson(lesson_id)


@app.get("/api/learning/scorecard")
def scorecard() -> list[dict[str, Any]]:
    return learning.scorecard()


@app.get("/api/learning/export.jsonl")
def export_training(min_score: float = 4.0, agent: str | None = None) -> Response:
    return Response(learning.export_training(min_score, agent), media_type="application/jsonl",
                    headers={"Content-Disposition": "attachment; filename=lifeos-training.jsonl"})


@app.post("/api/missions/{mid}/retry")
def retry_mission(mid: int) -> dict[str, Any]:
    m = missions.get(mid)
    return missions.start(m["goal"], m["target_agent"])


@app.get("/api/briefs")
def list_briefs() -> list[dict[str, Any]]:
    return missions.open_briefs()


@app.post("/api/briefs/{bid}/answer")
def answer_brief(bid: int, body: AnswerIn) -> dict[str, Any]:
    return missions.answer_brief(bid, body.answer)


def _outbox_file(name: str) -> Path:
    folder = tools.outbox_dir()
    target = (folder / Path(name).name).resolve()
    if target.parent != folder.resolve() or not target.is_file():
        raise KeyError(f"No outbox file named {name}")
    return target


@app.get("/api/outbox")
def list_outbox() -> list[dict[str, Any]]:
    folder = tools.outbox_dir()
    items = []
    for p in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.is_file():
            st = p.stat()
            items.append({"name": p.name, "path": str(p), "size": st.st_size,
                          "modified_at": files._iso(st.st_mtime)})
    return items


@app.get("/api/outbox/file")
def read_outbox(name: str) -> dict[str, Any]:
    target = _outbox_file(name)
    return {"name": target.name, "path": str(target), "text": target.read_text(encoding="utf-8", errors="replace")[:500_000]}


@app.post("/api/outbox/open")
def open_outbox(body: NameIn) -> dict[str, Any]:
    target = _outbox_file(body.name)
    files.launch(target)
    return {"opened": str(target)}


@app.delete("/api/outbox/{name}")
def delete_outbox(name: str) -> dict[str, Any]:
    target = _outbox_file(name)
    target.unlink()
    activity.log("data", f"Deleted outbox file {target.name}")
    return {"ok": True}


@app.get("/api/ledger")
def get_ledger(days: int = 7) -> dict[str, Any]:
    return llm.ledger(max(1, min(days, 365)))


# ---------------------------------------------------------------- providers and credentials

@app.get("/api/providers")
def list_providers() -> dict[str, Any]:
    return {"providers": llm.providers(), "vault": vault.backend(), "cloud_enabled": settings.get("cloud_enabled")}


@app.put("/api/providers/{provider}/key")
def set_provider_key(provider: str, body: KeyIn) -> dict[str, Any]:
    if provider not in llm.PROVIDERS or provider == "ollama":
        raise KeyError(f"Unknown provider {provider}")
    vault.set_secret(llm.key_name(provider), body.key.strip())
    activity.log("settings", f"Stored an API key for {provider}")
    return {"ok": True}


@app.delete("/api/providers/{provider}/key")
def delete_provider_key(provider: str) -> dict[str, Any]:
    if provider not in llm.PROVIDERS:
        raise KeyError(f"Unknown provider {provider}")
    vault.delete_secret(llm.key_name(provider))
    activity.log("settings", f"Removed the API key for {provider}")
    return {"ok": True}


# ---------------------------------------------------------------- workflows

@app.get("/api/workflows")
def list_workflows() -> list[dict[str, Any]]:
    return workflows.list_workflows()


@app.post("/api/workflows")
def create_workflow(body: WorkflowIn) -> dict[str, Any]:
    return workflows.create(body.model_dump())


@app.get("/api/workflows/meta")
def workflow_meta() -> dict[str, Any]:
    return {"triggers": list(workflows.TRIGGERS), "metrics": workflows.METRICS, "ops": list(workflows.OPS),
            "step_types": list(workflows.STEP_TYPES), "days": workflows.DAYS}


@app.get("/api/workflows/templates")
def workflow_templates() -> list[dict[str, Any]]:
    return workflows.templates()


@app.post("/api/workflows/draft")
def draft_workflow(body: TextIn) -> dict[str, Any]:
    return workflows.draft_from_text(body.text)


@app.get("/api/workflow-runs")
def all_workflow_runs(limit: int = 50) -> list[dict[str, Any]]:
    return workflows.runs(None, min(limit, 500))


@app.get("/api/workflows/{wid}")
def get_workflow(wid: int) -> dict[str, Any]:
    return workflows.get(wid)


@app.patch("/api/workflows/{wid}")
def patch_workflow(wid: int, body: dict[str, Any]) -> dict[str, Any]:
    return workflows.update(wid, body)


@app.delete("/api/workflows/{wid}")
def delete_workflow(wid: int) -> dict[str, Any]:
    return workflows.delete(wid)


@app.post("/api/workflows/{wid}/run")
def run_workflow(wid: int) -> dict[str, Any]:
    workflows.run_async(wid, "manual")
    return {"started": True}


@app.get("/api/workflows/{wid}/runs")
def workflow_runs(wid: int, limit: int = 30) -> list[dict[str, Any]]:
    return workflows.runs(wid, min(limit, 500))


# ---------------------------------------------------------------- projects

@app.get("/api/projects")
def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    return projects.list_projects(status)


@app.post("/api/projects")
def add_project(body: ProjectIn) -> dict[str, Any]:
    return projects.add(body.path, body.name, body.description)


@app.post("/api/projects/detect")
def detect_projects() -> dict[str, Any]:
    return projects.detect()


@app.get("/api/projects/{pid}")
def project_context(pid: int) -> dict[str, Any]:
    return projects.context(pid)


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: ProjectPatch) -> dict[str, Any]:
    return projects.update(pid, body.model_dump(exclude_unset=True))


@app.delete("/api/projects/{pid}")
def delete_project(pid: int) -> dict[str, Any]:
    return projects.delete(pid)


# ---------------------------------------------------------------- calendar and email

@app.get("/api/calendar/sources")
def calendar_sources() -> list[dict[str, Any]]:
    return integrations.list_sources()


@app.post("/api/calendar/sources")
def add_calendar_source(body: SourceIn) -> dict[str, Any]:
    return integrations.add_source(body.location, body.label)


@app.delete("/api/calendar/sources/{sid}")
def remove_calendar_source(sid: int) -> dict[str, Any]:
    integrations.remove_source(sid)
    return {"ok": True}


@app.post("/api/calendar/sources/{sid}/sync")
def sync_calendar_source(sid: int) -> dict[str, Any]:
    return integrations.sync_source(sid)


@app.get("/api/calendar/overlaps")
def calendar_overlaps(days: int = 14) -> list[dict[str, Any]]:
    return integrations.overlaps(days)


@app.get("/api/calendar/export.ics")
def export_calendar() -> Response:
    return Response(integrations.export_ics(), media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=lifeos.ics"})


@app.get("/api/events")
def list_events(start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    return integrations.events(start, end)


@app.post("/api/events")
def create_event(body: EventIn) -> dict[str, Any]:
    return integrations.add_event(body.title, body.start, body.end, body.location, body.description)


@app.delete("/api/events/{eid}")
def delete_event(eid: int) -> dict[str, Any]:
    integrations.delete_event(eid)
    return {"ok": True}


@app.get("/api/email/status")
def email_status() -> dict[str, Any]:
    return integrations.email_status()


@app.put("/api/email")
def set_email(body: EmailIn) -> dict[str, Any]:
    settings.update({"email": {"host": body.host.strip(), "port": body.port, "username": body.username.strip(),
                               "folder": body.folder.strip() or "INBOX"}})
    if body.password is not None:
        integrations.set_email_password(body.password)
    activity.log("settings", f"Email account set to {body.username}@{body.host}")
    return integrations.email_status()


@app.post("/api/email/test")
def test_email() -> dict[str, Any]:
    return integrations.test_email()


@app.get("/api/email-alerts")
def email_alerts() -> dict[str, Any]:
    return mailer.status()


@app.put("/api/email-alerts")
def set_email_alerts(body: EmailAlertsIn) -> dict[str, Any]:
    out = mailer.configure(body.model_dump(exclude={"password"}), body.password)
    activity.log("settings", f"Email alerts {'on' if out['enabled'] else 'off'}" + (f", sent to {out['to']}" if out["to"] else ""))
    return out


@app.post("/api/email-alerts/test")
def test_email_alerts() -> dict[str, Any]:
    return mailer.test()


@app.get("/api/email/messages")
def email_messages(limit: int = 15, unread: bool = True) -> list[dict[str, Any]]:
    return integrations.check_email(limit, unread)


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
