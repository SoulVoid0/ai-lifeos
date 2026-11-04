"""Self-upgrade: Forge proposes one improvement to LifeOS's own code; you approve it (by email if you like).

How a proposal is made (all local, with Forge's model):
  1. Forge looks at what has been going wrong lately (errors in the activity log, failed missions), your focus
     note if you set one, and the list of LifeOS source files, and picks ONE small, safe improvement and the
     file(s) it needs (at most two).
  2. It reads those files and writes the change as exact find-and-replace edits. LifeOS checks every edit matches
     the file exactly once, stays inside the repository, and that changed Python files still compile.
  3. The proposal becomes an approval with the full diff, in the app and (with email alerts on) as an email with
     Approve / Deny buttons.

Approving applies the change on a new branch in a separate git worktree (your own checkout and its uncommitted
work are never touched), runs the backend tests there, and commits. Optionally it pushes the branch so it shows
up on GitHub for you to merge (the release workflow builds a new version when it's merged with [release]).
Undo deletes the branch (and the pushed copy). Nothing ever lands on your main branch by itself.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import actions, activity, common, crew, db, llm

ALLOWED_EXT = {".py", ".ts", ".tsx", ".css", ".md", ".json", ".yml", ".yaml", ".ps1", ".sh", ".rs", ".toml"}
SKIP_PARTS = {".git", "node_modules", "dist", "build", "target", "__pycache__", ".venv", "venv", "gen", "binaries"}
MAX_FILE_BYTES = 60_000
MAX_EDITS = 8
TOOL = "apply_self_upgrade"
_busy = threading.Lock()
_last: dict[str, Any] = {"state": "idle", "error": "", "at": None, "action_id": None}

PICK_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "why": {"type": "string"},
    "files": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "why", "files"]}
EDIT_SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"},
    "edits": {"type": "array", "items": {"type": "object", "properties": {
        "file": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}},
        "required": ["file", "find", "replace"]}}}, "required": ["summary", "edits"]}


def cfg() -> dict[str, Any]:
    return common.group("self_upgrade")


def configure(changes: dict[str, Any]) -> dict[str, Any]:
    def check(c: dict[str, Any]) -> None:
        if not 1 <= c["every_days"] <= 90:
            raise ValueError("every_days must be between 1 and 90")
        if c["repo_path"].strip() and not is_repo(Path(c["repo_path"].strip()).expanduser()):
            raise ValueError("That folder isn't a LifeOS git checkout (it needs .git and backend/lifeos)")
        c["focus"] = c["focus"].strip()[:500]

    return common.save_group("self_upgrade", changes, check)


# ---------------------------------------------------------------- the repository

def is_repo(path: Path) -> bool:
    return (path / ".git").exists() and (path / "backend" / "lifeos").is_dir()


def find_repo() -> Path | None:
    configured = cfg()["repo_path"].strip()
    if configured:
        p = Path(configured).expanduser()
        return p if is_repo(p) else None
    home = Path.home()
    candidates = [Path(__file__).resolve().parents[2], home / "ai-lifeos", home / "src" / "ai-lifeos",
                  home / "code" / "ai-lifeos", home / "Documents" / "GitHub" / "ai-lifeos"]
    if os.name == "nt":
        candidates = [Path(f"{d}:\\ai-lifeos") for d in "ACDE"] + candidates
    return next((c for c in candidates if is_repo(c)), None)


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    git = shutil.which("git")
    if not git:
        raise ValueError("git isn't installed (or not on PATH)")
    proc = subprocess.run([git, "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                          **common.hidden())
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout


def source_files(repo: Path) -> list[tuple[str, int]]:
    out = []
    for p in sorted((repo / "backend" / "lifeos").rglob("*")) + sorted((repo / "frontend" / "src").rglob("*")):
        if p.is_file() and p.suffix in ALLOWED_EXT and not (set(p.relative_to(repo).parts) & SKIP_PARTS):
            out.append((p.relative_to(repo).as_posix(), p.stat().st_size))
    return out


def _safe_path(repo: Path, rel: str) -> Path:
    rel = rel.strip().lstrip("/\\").replace("\\", "/")
    target = (repo / rel).resolve()
    if repo.resolve() not in target.parents or set(Path(rel).parts) & SKIP_PARTS or target.suffix not in ALLOWED_EXT:
        raise ValueError(f"Not an allowed LifeOS source file: {rel}")
    return target


# ---------------------------------------------------------------- what's been going wrong

def pain_points(days: int = 7, limit: int = 15) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.session() as conn:
        errors = [r["summary"] for r in conn.execute(
            "SELECT summary, COUNT(*) AS n FROM activity WHERE status = 'error' AND created_at >= ? GROUP BY summary "
            "ORDER BY n DESC LIMIT ?", (since, limit)).fetchall()]
        failed = [f"Mission failed: {r['error']}" for r in conn.execute(
            "SELECT error FROM missions WHERE status = 'failed' AND error IS NOT NULL AND created_at >= ? "
            "GROUP BY error ORDER BY MAX(id) DESC LIMIT 5", (since,)).fetchall()]
    return [e[:300] for e in errors + failed]


# ---------------------------------------------------------------- proposing

def _model() -> str:
    m = cfg()["model"].strip()
    return m or crew.get("coder")["effective_model"]


def validate_edits(repo: Path, edits: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    """Apply edits in memory. Returns {relative path: (old text, new text)}; raises ValueError if any edit is unsafe."""
    if not edits:
        raise ValueError("The proposal has no edits")
    if len(edits) > MAX_EDITS:
        raise ValueError(f"Too many edits ({len(edits)}); a proposal is one small change")
    changed: dict[str, tuple[str, str]] = {}
    for e in edits:
        target = _safe_path(repo, e["file"])
        rel = target.relative_to(repo.resolve()).as_posix()
        old = changed[rel][1] if rel in changed else (target.read_text(encoding="utf-8") if target.is_file() else "")
        find, replace = e.get("find", ""), e.get("replace", "")
        if not target.is_file() and find:
            raise ValueError(f"{rel} doesn't exist")
        if find:
            n = old.count(find)
            if n != 1:
                raise ValueError(f"An edit to {rel} matches {n} places instead of exactly one")
            new = old.replace(find, replace, 1)
        else:
            if target.is_file():
                raise ValueError(f"An edit to {rel} has nothing to find (only new files can be written whole)")
            new = replace
        if new == old:
            continue
        changed[rel] = (changed[rel][0] if rel in changed else old, new)
    for rel, (_, new) in changed.items():
        if rel.endswith(".py"):
            try:
                compile(new, rel, "exec")
            except SyntaxError as exc:
                raise ValueError(f"The change breaks {rel}: {exc.msg} on line {exc.lineno}") from exc
        if rel.endswith(".json"):
            json.loads(new)
    if not changed:
        raise ValueError("The edits don't change anything")
    return changed


def diff_text(changed: dict[str, tuple[str, str]]) -> str:
    parts = []
    for rel, (old, new) in changed.items():
        parts.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                          fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
    return "".join(parts)


def propose(focus: str | None = None) -> dict[str, Any]:
    """Ask Forge for one improvement and queue it for approval. Blocks until done (run it in a thread)."""
    if not _busy.acquire(blocking=False):
        raise ValueError("Forge is already working on a proposal")
    try:
        _last.update(state="thinking", error="", at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        repo = find_repo()
        if not repo:
            raise ValueError("Couldn't find the LifeOS source code. Set its folder (for example A:\\ai-lifeos) in "
                             "Settings → Self-upgrade.")
        model = _model()
        if not llm.model_available(model):
            raise ValueError(f"Forge's model {model} isn't available")
        focus = (focus if focus is not None else cfg()["focus"]).strip()
        files = source_files(repo)
        listing = "\n".join(f"{p} ({size // 1024} KB)" for p, size in files if size <= MAX_FILE_BYTES)
        pains = pain_points()
        earlier = [a["summary"] for a in actions.list_actions(None, 200) if a["tool"] == TOOL][:15]
        forge = crew.get("coder")
        system = (f"You are {forge['name']}, the coder on the LifeOS crew, improving LifeOS's own source code. "
                  "LifeOS is a local AI assistant: a Python FastAPI backend (backend/lifeos) and a React + TypeScript UI "
                  "(frontend/src). Propose ONE small, safe, self-contained improvement that a reviewer can check in a "
                  "minute: fix a bug behind a recent error, handle an edge case, improve an error message, or add a "
                  "small convenience. No refactors, no new dependencies, no changes to security checks or permissions.")
        pick_prompt = ((f"The user's focus: {focus}\n\n" if focus else "")
                       + ("Recent problems on this PC:\n- " + "\n- ".join(pains) + "\n\n" if pains else "")
                       + ("Already proposed before (pick something else):\n- " + "\n- ".join(earlier) + "\n\n" if earlier else "")
                       + f"Source files:\n{listing}\n\nPick the improvement and the 1-2 files to change.")
        with llm.track(None, "coder"):
            pick = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": pick_prompt}],
                                 PICK_SCHEMA, model=model, timeout=900)
        chosen = [f for f in (pick.get("files") or [])[:2] if isinstance(f, str)]
        if not chosen:
            raise ValueError("Forge didn't pick any file")
        contents = []
        for rel in chosen:
            path = _safe_path(repo, rel)
            if not path.is_file():
                raise ValueError(f"Forge picked {rel}, which doesn't exist")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"{rel} is too big to change safely in one go")
            contents.append(f"=== {rel} ===\n{path.read_text(encoding='utf-8')}")
        edit_prompt = (f"Improvement: {pick.get('title')}\nWhy: {pick.get('why')}\n\n" + "\n\n".join(contents)
                       + "\n\nWrite the change as edits. Each edit's `find` must be copied EXACTLY from the file above "
                         "(whole lines, enough of them to be unique) and `replace` is what goes there instead. Keep the "
                         "change minimal and in the file's existing style. `summary` explains the change in 1-3 sentences.")
        with llm.track(None, "coder"):
            out = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": edit_prompt}],
                                EDIT_SCHEMA, model=model, timeout=1800)
        edits = [{"file": str(e.get("file", "")), "find": str(e.get("find", "")), "replace": str(e.get("replace", ""))}
                 for e in (out.get("edits") or []) if isinstance(e, dict)]
        changed = validate_edits(repo, edits)
        diff = diff_text(changed)
        title = re.sub(r"\s+", " ", str(pick.get("title") or "Improvement")).strip()[:100]
        summary = f"Self-upgrade: {title}"
        preview = {"repo": str(repo), "title": title, "why": str(pick.get("why") or "")[:600],
                   "summary": str(out.get("summary") or "")[:800], "edits": edits, "diff": diff[:60_000],
                   "files": sorted(changed), "model": model, "head": _git(repo, "rev-parse", "HEAD").strip()}
        action = actions.create(TOOL, {"title": title}, summary, preview)
        _last.update(state="proposed", action_id=action["id"])
        activity.log("crew", f"Forge proposed a LifeOS change: {title}", status="pending", action_id=action["id"])
        common.notify("Forge proposes a LifeOS upgrade", f"{title}. Review the diff in Settings → Self-upgrade.")
        try:
            from . import mailer

            mailer.request_approval(action["id"], "Forge proposes a LifeOS upgrade",
                                    f"{preview['why']}\n\n{preview['summary']}\n\n{diff[:6000]}")
        except Exception:  # noqa: BLE001 - email is optional
            pass
        common.save_group("self_upgrade", {"last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return action
    except Exception as exc:
        _last.update(state="failed", error=f"{exc}"[:400])
        common.save_group("self_upgrade", {"last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        raise
    finally:
        _busy.release()


def propose_async(focus: str | None = None) -> dict[str, Any]:
    if _busy.locked():
        raise ValueError("Forge is already working on a proposal")
    common.background("self-upgrade", lambda: _propose_quiet(focus))
    _last.update(state="thinking", error="")
    return status()


def _propose_quiet(focus: str | None) -> None:
    try:
        propose(focus)
    except Exception as exc:  # noqa: BLE001 - recorded in _last
        activity.log("crew", f"Self-upgrade: no proposal ({exc})"[:300], status="error")


def scheduled() -> object:
    c = cfg()
    if not c["enabled"] or _busy.locked():
        return None
    if any(a["tool"] == TOOL for a in actions.list_actions("pending", 50)):
        return None  # one proposal at a time
    last = c["last_run_at"]
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=c["every_days"]):
                return None
        except ValueError:
            pass
    from . import gamemode, missions

    if gamemode.active() or missions.list_missions(5, "active"):
        return None  # not while you're gaming or the crew is busy
    return propose_async()


# ---------------------------------------------------------------- applying (the approved action)

def _python() -> str | None:
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python") or shutil.which("python3") or shutil.which("py")


def _run_tests(tree: Path) -> dict[str, Any]:
    py = _python()
    if not py:
        return {"ran": False, "ok": None, "output": "Python isn't on PATH, so the tests were skipped"}
    proc = subprocess.run([py, "-m", "pytest", "-q", "-x", "tests"], cwd=str(tree / "backend"), capture_output=True,
                          text=True, timeout=1200, env={**os.environ, "LIFEOS_NO_SCHEDULER": "1",
                                                        "LIFEOS_NO_SERVICES": "1"}, **common.hidden())
    tail = (proc.stdout + proc.stderr).strip()[-1500:]
    if "No module named pytest" in tail:
        return {"ran": False, "ok": None, "output": "pytest isn't installed for that Python, so the tests were skipped"}
    return {"ran": True, "ok": proc.returncode == 0, "output": tail}


def execute(args: dict[str, Any], preview: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    repo = Path(preview["repo"])
    if not is_repo(repo):
        raise ValueError(f"{repo} is no longer a LifeOS checkout")
    branch = f"lifeos/upgrade-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    tree = common.feature_dir("upgrades") / branch.split("/")[-1]
    _git(repo, "worktree", "add", "-b", branch, str(tree), preview.get("head") or "HEAD")
    try:
        changed = validate_edits(tree, preview["edits"])
        for rel, (_, new) in changed.items():
            target = _safe_path(tree, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new, encoding="utf-8", newline="")
        tests = _run_tests(tree) if cfg()["run_tests"] else {"ran": False, "ok": None, "output": "Tests are turned off"}
        _git(tree, "add", "--", *changed)
        message = (f"{preview['title']}\n\n{preview.get('summary') or preview.get('why') or ''}\n\n"
                   f"Proposed by Forge ({preview.get('model')}) and approved in AI LifeOS.")
        _git(tree, "-c", "user.name=LifeOS Forge", "-c", "user.email=forge@lifeos.local", "commit", "-q", "-m", message)
        commit = _git(tree, "rev-parse", "HEAD").strip()
        pushed = False
        push_note = ""
        if cfg()["push"]:
            if tests["ok"] is False:
                push_note = "Not pushed because the tests failed"
            else:
                try:
                    _git(tree, "push", "-u", "origin", branch, timeout=300)
                    pushed = True
                except ValueError as exc:
                    push_note = str(exc)
    except Exception:
        _cleanup(repo, tree, branch, remote=False)
        raise
    verdict = ("tests passed" if tests["ok"] else "TESTS FAILED" if tests["ok"] is False else "tests skipped")
    summary = (f"Applied \"{preview['title']}\" on branch {branch} ({verdict})"
               + (", pushed to GitHub" if pushed else "") + ".")
    common.notify("LifeOS upgrade applied" if tests["ok"] is not False else "LifeOS upgrade: tests failed", summary)
    return {"summary": summary, "branch": branch, "worktree": str(tree), "commit": commit, "tests": tests,
            "pushed": pushed, "push_note": push_note, "repo": str(repo)}


def _cleanup(repo: Path, tree: Path, branch: str, remote: bool) -> None:
    for args in (("worktree", "remove", "--force", str(tree)), ("branch", "-D", branch)):
        try:
            _git(repo, *args)
        except ValueError:
            pass
    if remote:
        try:
            _git(repo, "push", "origin", "--delete", branch, timeout=300)
        except ValueError:
            pass
    shutil.rmtree(tree, ignore_errors=True)


def undo(result: dict[str, Any]) -> dict[str, Any]:
    _cleanup(Path(result["repo"]), Path(result["worktree"]), result["branch"], remote=bool(result.get("pushed")))
    return {"summary": f"Deleted the upgrade branch {result['branch']}" + (" and its copy on GitHub" if result.get("pushed") else "") + "."}


def proposals(limit: int = 20) -> list[dict[str, Any]]:
    return [a for a in actions.list_actions(None, 300) if a["tool"] == TOOL][:limit]


def status() -> dict[str, Any]:
    repo = find_repo()
    try:
        model = _model()
    except KeyError:
        model = None
    return {"config": cfg(), "repo": str(repo) if repo else None, "git": bool(shutil.which("git")), "model": model,
            "last": dict(_last), "busy": _busy.locked(), "proposals": proposals()}
