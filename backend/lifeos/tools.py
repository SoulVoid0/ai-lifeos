"""The assistant's tool layer.

The model never touches the OS directly. It can only name tools from this registry; every
call is validated against a pydantic schema, checked against the user's policy for that
tool, and either run, queued for approval, or refused. Everything is logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from . import activity, files, memory, permissions, settings, tasks

Risk = Literal["read", "write", "destructive"]


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- argument schemas

class SearchFilesArgs(BaseModel):
    query: str = Field("", description="Words describing the file: topic, name fragments, content")
    folder: str | None = Field(None, description="Approved folder name such as 'Downloads'")
    extensions: list[str] | None = Field(None, description="File extensions without dots, e.g. ['pdf']")
    modified_within_days: int | None = Field(None, ge=1, le=36500)
    modified_after: str | None = Field(None, description="ISO date")
    modified_before: str | None = Field(None, description="ISO date")
    category: str | None = None
    limit: int = Field(25, ge=1, le=200)


class PathArgs(BaseModel):
    path: str


class LargeFilesArgs(BaseModel):
    min_mb: float = Field(100, gt=0)
    older_than_days: int | None = Field(None, ge=1)


class DuplicatesArgs(BaseModel):
    folder: str | None = None


class OldFilesArgs(BaseModel):
    days: int = Field(180, ge=1)


class ListTasksArgs(BaseModel):
    status: Literal["open", "done", "all"] = "open"
    due_within_days: int | None = Field(None, ge=0, le=3650)


class CreateTaskArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    due: str | None = Field(None, description="Natural language or ISO date, e.g. 'next friday 5pm'")
    remind: str | None = Field(None, description="When to send a reminder, e.g. 'tomorrow 9am'")
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    notes: str | None = None
    project: str | None = None
    estimate_minutes: int | None = Field(None, ge=1, le=10000)
    include_important_files: bool = Field(False, description="Attach important files found earlier in this request")


class CompleteTaskArgs(BaseModel):
    task_id: int | None = None
    title: str | None = None


class MemoryArgs(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    kind: Literal["preference", "project", "fact", "routine", "note", "person"] = "fact"


class SearchMemoryArgs(BaseModel):
    query: str


class SearchEverythingArgs(BaseModel):
    query: str = Field(..., min_length=1)


class DeleteMemoryArgs(BaseModel):
    memory_id: int


class OrganizeArgs(BaseModel):
    folder: str = Field(..., description="Approved folder name or path")
    extensions: list[str] | None = None
    strategy: Literal["category", "type", "date"] = "category"
    recursive: bool = False


class MoveFileArgs(BaseModel):
    path: str
    destination_folder: str


class PlanDayArgs(BaseModel):
    available_hours: float = Field(6, gt=0, le=16)


class NoArgs(BaseModel):
    pass


# ---------------------------------------------------------------- registry

@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    risk: Risk
    default_policy: Literal["auto", "confirm", "deny"]
    run: Callable[[BaseModel, dict[str, Any]], dict[str, Any]] | None = None
    # For confirm-able tools: build a preview without side effects, and execute an approved preview.
    prepare: Callable[[BaseModel, dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    execute: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    undo: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    examples: list[str] = field(default_factory=list)


def _file_list_summary(found: list[dict[str, Any]], what: str = "file") -> str:
    if not found:
        return f"No matching {what}s found."
    names = ", ".join(f["name"] for f in found[:8])
    more = f" and {len(found) - 8} more" if len(found) > 8 else ""
    return f"Found {len(found)} {what}{'s' if len(found) != 1 else ''}: {names}{more}."


def _search_files(a: SearchFilesArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    after = a.modified_after
    if a.modified_within_days:
        after = (datetime.now(timezone.utc) - timedelta(days=a.modified_within_days)).isoformat(timespec="seconds")
    found = files.search(a.query, extensions=a.extensions, folder=a.folder, modified_after=after,
                         modified_before=a.modified_before, category=a.category, limit=a.limit)
    ctx.setdefault("found_files", []).extend(found)
    return {"summary": _file_list_summary(found), "files": found}


def _read_document(a: PathArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    doc = files.read_document(a.path)
    return {"summary": f"Read {doc['name']} ({len(doc['text'])} characters extracted).", "document": doc}


def _open_file(a: PathArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    return {"summary": f"Opened {a.path}.", **files.open_path(a.path)}


def _large_files(a: LargeFilesArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    found = files.large_files(a.min_mb, a.older_than_days)
    ctx.setdefault("found_files", []).extend(found)
    total = sum(f["size"] for f in found) / 1024 ** 3
    return {"summary": _file_list_summary(found, "large file") + (f" Total {total:.2f} GB." if found else ""),
            "files": found}


def _duplicates(a: DuplicatesArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    groups = files.duplicates(a.folder)
    waste = sum(g["size"] * (len(g["files"]) - 1) for g in groups) / 1024 ** 2
    summary = (f"Found {len(groups)} sets of duplicate files wasting {waste:.1f} MB." if groups
               else "No duplicate files found.")
    return {"summary": summary, "groups": groups}


def _old_files(a: OldFilesArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    found = files.stale_files(a.days)
    ctx.setdefault("found_files", []).extend(found)
    return {"summary": _file_list_summary(found, f"file untouched for {a.days}+ days"), "files": found}


def _list_folders(a: NoArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    folders = permissions.list_folders()
    names = ", ".join(f["path"] for f in folders) or "none"
    return {"summary": f"Approved folders: {names}.", "folders": folders}


def _list_tasks(a: ListTasksArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    before = None
    if a.due_within_days is not None:
        before = (datetime.now(timezone.utc) + timedelta(days=a.due_within_days)).isoformat(timespec="seconds")
    items = tasks.list_tasks(a.status, due_before=before)
    names = "; ".join(t["title"] for t in items[:10])
    return {"summary": f"{len(items)} {a.status} task(s)" + (f": {names}" if items else "."), "tasks": items}


def _create_task(a: CreateTaskArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    notes = a.notes or ""
    if a.include_important_files:
        candidates = ctx.get("important_files") or ctx.get("found_files") or []
        if candidates:
            names = dict.fromkeys(f["name"] for f in candidates)
            notes = (notes + "\n" if notes else "") + "Files:\n" + "\n".join(f"- {n}" for n in names)
    remind = a.remind
    if remind is None and a.due is None and ctx.get("default_when"):
        remind = ctx["default_when"]
    task = tasks.create(a.title, notes=notes or None, project=a.project, priority=a.priority, due=a.due,
                        remind=remind, estimate_minutes=a.estimate_minutes)
    when = task["remind_at"] or task["due_at"]
    return {"summary": f"Created task \"{task['title']}\"" + (f" ({'reminder' if task['remind_at'] else 'due'} {when})" if when else "") + ".",
            "task": task}


def _complete_task(a: CompleteTaskArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    task = tasks.get(a.task_id) if a.task_id else (tasks.find(a.title or "") if a.title else None)
    if not task:
        raise ToolError("No matching open task found")
    task = tasks.update(task["id"], {"status": "done"})
    return {"summary": f"Marked \"{task['title']}\" as done.", "task": task}


def _undo_create_task(result: dict[str, Any]) -> dict[str, Any]:
    t = tasks.delete(result["task"]["id"])
    return {"summary": f"Deleted task \"{t['title']}\"."}


def _undo_complete_task(result: dict[str, Any]) -> dict[str, Any]:
    t = tasks.update(result["task"]["id"], {"status": "open"})
    return {"summary": f"Reopened \"{t['title']}\"."}


def _undo_save_memory(result: dict[str, Any]) -> dict[str, Any]:
    m = memory.delete(result["memory"]["id"])
    return {"summary": f"Forgot: {m['content']}"}


def _save_memory(a: MemoryArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    m = memory.add(a.content, a.kind, source="assistant")
    return {"summary": f"Saved to memory: {m['content']}", "memory": m}


def _search_memory(a: SearchMemoryArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    found = memory.search(a.query)
    lines = "; ".join(m["content"] for m in found[:5])
    return {"summary": f"Found {len(found)} memories" + (f": {lines}" if found else "."), "memories": found}


def _search_everything(a: SearchEverythingArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    from . import search

    res = search.everything(a.query)
    ctx.setdefault("found_files", []).extend(res["files"])
    parts = [f"{len(v)} {k}" for k, v in res.items() if v]
    return {"summary": "Found " + (", ".join(parts) if parts else "nothing") + f" for \"{a.query}\".", "results": res}


def _plan_day(a: PlanDayArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    plan = tasks.plan_day(int(a.available_hours * 60))
    if not plan["blocks"]:
        return {"summary": "No open tasks to schedule.", "plan": plan}
    lines = "; ".join(f"{b['title']} ({b['minutes']} min)" for b in plan["blocks"])
    return {"summary": f"Suggested plan for {plan['day']}: {lines}.", "plan": plan}


def _daily_summary(a: NoArgs, ctx: dict[str, Any]) -> dict[str, Any]:
    from . import proactive

    data = proactive.daily_summary()
    return {"summary": data["text"], "data": data}


# confirm-able tools -------------------------------------------------------------

def _prepare_organize(a: OrganizeArgs, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    plan = files.plan_organization(a.folder, extensions=a.extensions, strategy=a.strategy, recursive=a.recursive)
    if a.strategy == "category" and plan["moves"] and ctx.get("categorizer"):
        categories = ctx["categorizer"](plan["moves"])
        if categories:
            plan = files.plan_organization(a.folder, extensions=a.extensions, strategy=a.strategy,
                                           recursive=a.recursive, categories=categories)
    important = [{"path": m["dest"], "name": Path(m["dest"]).name} for m in plan["moves"] if m["important"]]
    ctx["important_files"] = important
    ctx.setdefault("found_files", []).extend({"path": m["src"], "name": Path(m["src"]).name} for m in plan["moves"])
    if not plan["moves"]:
        return "Nothing to organize: the files are already in place.", plan
    cats = ", ".join(f"{k} ({v})" for k, v in sorted(plan["by_category"].items()))
    return f"Move {len(plan['moves'])} file(s) in {plan['folder']} into: {cats}", plan


def _execute_moves(args: dict[str, Any], preview: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    exclude = set(options.get("exclude") or [])
    moves = [m for m in preview.get("moves", []) if m["src"] not in exclude]
    result = files.apply_moves(moves)
    summary = f"Moved {len(result['moved'])} file(s)" + (f", skipped {len(result['skipped'])}" if result["skipped"] else "") + "."
    return {"summary": summary, **result}


def _undo_moves(result: dict[str, Any]) -> dict[str, Any]:
    out = files.undo_moves(result.get("moved", []))
    return {"summary": f"Restored {len(out['restored'])} file(s) to their original location.", **out}


def _prepare_move(a: MoveFileArgs, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    _, src = permissions.resolve_allowed(a.path)
    _, dest_dir = permissions.resolve_allowed(a.destination_folder, must_exist=False)
    dest = dest_dir / src.name
    return f"Move {src.name} to {dest_dir}", {"moves": [{"src": str(src), "dest": str(dest), "category": dest_dir.name}]}


def _prepare_create_folder(a: PathArgs, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    _, resolved = permissions.resolve_allowed(a.path, must_exist=False)
    return f"Create folder {resolved}", {"path": str(resolved)}


def _execute_create_folder(args: dict[str, Any], preview: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    out = files.create_folder(preview["path"])
    return {"summary": f"Created folder {out['path']}." if out["created"] else f"{out['path']} already existed.", **out}


def _undo_create_folder(result: dict[str, Any]) -> dict[str, Any]:
    path = Path(result["path"])
    if result.get("created") and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        return {"summary": f"Removed empty folder {path}."}
    return {"summary": f"Left {path} in place (not empty or pre-existing)."}


def _prepare_delete_memory(a: DeleteMemoryArgs, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    m = memory.get(a.memory_id)
    return f"Forget memory: \"{m['content']}\"", {"memory": m}


def _execute_delete_memory(args: dict[str, Any], preview: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    m = memory.delete(preview["memory"]["id"])
    return {"summary": f"Forgot: {m['content']}", "memory": m}


def _undo_delete_memory(result: dict[str, Any]) -> dict[str, Any]:
    m = result["memory"]
    restored = memory.add(m["content"], m["kind"], source=m.get("source"), pinned=bool(m.get("pinned")))
    return {"summary": f"Restored memory: {restored['content']}", "memory": restored}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("search_files", "Search indexed files in approved folders by topic, name, type, folder or date.",
         SearchFilesArgs, "read", "auto", run=_search_files),
    Tool("read_document", "Extract text from a document (PDF, DOCX, text, code) in an approved folder.",
         PathArgs, "read", "auto", run=_read_document),
    Tool("open_file", "Open a file in its default application.", PathArgs, "read", "auto", run=_open_file),
    Tool("find_large_files", "List large files, optionally only those not used for N days.",
         LargeFilesArgs, "read", "auto", run=_large_files),
    Tool("find_duplicates", "Find byte-identical duplicate files.", DuplicatesArgs, "read", "auto", run=_duplicates),
    Tool("find_old_files", "List files not modified for N days (archiving candidates).",
         OldFilesArgs, "read", "auto", run=_old_files),
    Tool("list_folders", "List the folders the user has approved.", NoArgs, "read", "auto", run=_list_folders),
    Tool("list_tasks", "List tasks, optionally only those due within N days.", ListTasksArgs, "read", "auto", run=_list_tasks),
    Tool("plan_day", "Suggest a schedule for today's open tasks.", PlanDayArgs, "read", "auto", run=_plan_day),
    Tool("daily_summary", "Summarize today: schedule, deadlines, overdue tasks, recent activity, suggestions.",
         NoArgs, "read", "auto", run=_daily_summary),
    Tool("search_memory", "Search long-term memory.", SearchMemoryArgs, "read", "auto", run=_search_memory),
    Tool("search_everything", "Search files, memories, tasks and past conversations at once.",
         SearchEverythingArgs, "read", "auto", run=_search_everything),
    Tool("create_task", "Create a task or reminder. Use 'remind' for reminders and 'due' for deadlines.",
         CreateTaskArgs, "write", "auto", run=_create_task, undo=_undo_create_task),
    Tool("complete_task", "Mark a task done by id or title.", CompleteTaskArgs, "write", "auto", run=_complete_task,
         undo=_undo_complete_task),
    Tool("save_memory", "Remember a fact or preference the user explicitly asked to remember.",
         MemoryArgs, "write", "auto", run=_save_memory, undo=_undo_save_memory),
    Tool("organize_files", "Propose moving files of an approved folder into category subfolders. Requires approval.",
         OrganizeArgs, "write", "confirm", prepare=_prepare_organize, execute=_execute_moves, undo=_undo_moves),
    Tool("move_file", "Move one file to another approved folder. Requires approval.",
         MoveFileArgs, "write", "confirm", prepare=_prepare_move, execute=_execute_moves, undo=_undo_moves),
    Tool("create_folder", "Create a folder inside an approved folder. Requires approval.",
         PathArgs, "write", "confirm", prepare=_prepare_create_folder, execute=_execute_create_folder,
         undo=_undo_create_folder),
    Tool("delete_memory", "Forget a stored memory. Requires approval.", DeleteMemoryArgs, "destructive", "confirm",
         prepare=_prepare_delete_memory, execute=_execute_delete_memory, undo=_undo_delete_memory),
]}


def describe_call(name: str, args: dict[str, Any]) -> str:
    if name == "create_task":
        when = args.get("remind") or args.get("due")
        return f"Create task \"{args['title']}\"" + (f" ({when})" if when else "")
    if name == "complete_task":
        return f"Mark task {args.get('task_id') or args.get('title')!r} as done"
    if name == "save_memory":
        return f"Remember: {args['content']}"
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items() if v not in (None, [], ""))
    return f"Run {name}({shown})"


def _ctx_snapshot(ctx: dict[str, Any]) -> dict[str, Any]:
    """The JSON-safe parts of a request context, kept so a queued call runs as it would have."""
    return {k: ctx[k] for k in ("important_files", "found_files", "default_when") if k in ctx}


def run_queued(name: str, preview: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved call to a tool that has no custom executor."""
    tool, parsed = validate(name, preview.get("args") or {})
    assert tool.run is not None
    return tool.run(parsed, dict(preview.get("ctx") or {}))


def policy_for(tool: Tool) -> str:
    cfg = settings.get_all()
    policy = cfg["tool_policies"].get(tool.name, tool.default_policy)
    if tool.risk == "destructive" and policy == "auto":
        policy = "confirm"  # destructive tools can never run unattended
    if cfg["automation_paused"] and tool.risk != "read" and policy == "auto":
        policy = "confirm"
    return policy


def describe() -> list[dict[str, Any]]:
    out = []
    for t in TOOLS.values():
        out.append({"name": t.name, "description": t.description, "risk": t.risk,
                    "default_policy": t.default_policy, "policy": policy_for(t),
                    "parameters": t.args.model_json_schema()})
    return out


def validate(name: str, args: dict[str, Any]) -> tuple[Tool, BaseModel]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool '{name}'")
    try:
        return tool, tool.args.model_validate(args or {})
    except ValidationError as exc:
        raise ToolError(f"Invalid arguments for {name}: {exc.errors(include_url=False)}") from exc


def invoke(name: str, args: dict[str, Any], ctx: dict[str, Any], conversation_id: int | None = None) -> dict[str, Any]:
    """Validate, authorize, then run or queue a tool call. Never raises for tool failures."""
    from . import actions

    try:
        tool, parsed = validate(name, args)
    except ToolError as exc:
        activity.log("tool", f"Rejected call to {name}", tool=name, details={"args": args, "error": str(exc)}, status="error")
        return {"tool": name, "status": "error", "summary": str(exc)}

    policy = policy_for(tool)
    if policy == "deny":
        activity.log("tool", f"Blocked {name} (denied by policy)", tool=name, details={"args": args}, status="blocked")
        return {"tool": name, "status": "blocked", "summary": f"The {name} tool is disabled in Settings → Permissions."}

    try:
        if tool.prepare is not None:
            summary, preview = tool.prepare(parsed, ctx)
            if not preview.get("moves", True) and "moves" in preview:
                return {"tool": name, "status": "done", "summary": summary, "result": preview}
            if policy == "confirm":
                action = actions.create(name, parsed.model_dump(), summary, preview, conversation_id)
                return {"tool": name, "status": "pending", "summary": summary, "action": action}
            action = actions.create(name, parsed.model_dump(), summary, preview, conversation_id)
            action = actions.approve(action["id"], auto=True)
            return {"tool": name, "status": action["status"], "summary": (action.get("result") or {}).get("summary", summary),
                    "action": action}
        assert tool.run is not None
        if policy == "confirm":
            summary = describe_call(name, parsed.model_dump())
            action = actions.create(name, parsed.model_dump(), summary, {"args": parsed.model_dump(), "ctx": _ctx_snapshot(ctx)},
                                    conversation_id)
            return {"tool": name, "status": "pending", "summary": summary, "action": action}
        result = tool.run(parsed, ctx)
    except (ToolError, permissions.PermissionDenied, FileNotFoundError, KeyError, ValueError) as exc:
        msg = str(exc).strip("'\"")
        activity.log("tool", f"{name} failed: {msg}", tool=name, details={"args": args}, status="error")
        return {"tool": name, "status": "error", "summary": msg}

    if tool.risk != "read":
        activity.log("action", result["summary"], tool=name, details={"args": parsed.model_dump()})
    else:
        activity.log("tool", f"{name}: {result['summary'][:200]}", tool=name, details={"args": parsed.model_dump()})
    return {"tool": name, "status": "done", "summary": result["summary"], "result": result}
