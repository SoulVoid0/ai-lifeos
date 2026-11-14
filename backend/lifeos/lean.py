"""Low-resource mode: one click that makes the crew use far less GPU, CPU and RAM while keeping answer quality.

What it changes, biggest saving first:
- Thinking off (settings.ollama_think): qwen3 writes a hidden reasoning pass before every answer, often longer than
  the answer itself. The agents already reason in their JSON "thought" field, so the pass is pure cost.
- Fewer models: every general agent shares one model (so agents never swap 5-10 GB models in and out), Forge uses a
  30B coder squeezed to fit fully in a 16 GB GPU (no CPU spill, which is what made the PC lag), and the Spark micro
  agent and agent chats use a small fast model that stays loaded beside the main one.
- Ollama server tuning (services.tuning_env): flash attention plus a q8_0 context cache (half the VRAM), one request
  slot (keeps the prompt cache warm, so each step only reads its new part), at most two models in memory.
- Stay on the loaded model: the Overseer's reviews and report use whatever model is already in VRAM.
- One agent at a time, a 12k context and models released after 10 idle minutes.

Models are downloaded inside the app (no terminal), with progress, and can be removed to free disk space.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any

import httpx

from . import activity, crew, llm, settings

log = logging.getLogger("lifeos.lean")

MAIN_MODEL = "qwen3:14b"
GENERAL = ("overseer", "planner", "files", "research", "memory", "comms", "creative", "photo", "video")
PLAN: dict[str, str] = {**{slug: MAIN_MODEL for slug in GENERAL}, "coder": crew.LEAN_CODER, "quick": crew.FAST_MODEL}
LEAN_SETTINGS: dict[str, Any] = {
    "ollama_think": False, "ollama_tuning": True, "ollama_flash_attention": True, "ollama_kv_cache": "q8_0",
    "stay_on_loaded_model": True, "fast_model": crew.FAST_MODEL, "max_parallel_agents": 1, "ollama_num_ctx": 12288,
    "ollama_keep_alive": "10m", "ollama_one_model": True, "ollama_unload_on_switch": True, "ollama_low_priority": True,
    "chat_model": MAIN_MODEL,
}
# Models the old setup used that Low-resource mode no longer needs (offered for removal, never removed on its own).
REPLACED = ("qwen3-coder:30b", "qwen3:8b")

_pulls: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _base() -> str:
    return settings.get("ollama_url").rstrip("/")


# ---------------------------------------------------------------- downloads

def pull(model: str, then: Any = None) -> dict[str, Any]:
    """Download a model with Ollama in the background. `then` runs once it's installed."""
    model = model.strip()
    if not model or len(model) > 200 or llm.parse_ref(model)[0] != "ollama":
        raise ValueError("Give an Ollama model name, like qwen3:14b or hf.co/user/repo:quant")
    with _lock:
        job = _pulls.get(model)
        if job and job["state"] == "pulling":
            return dict(job)
        job = {"model": model, "state": "pulling", "status": "starting", "completed": 0, "total": 0, "error": None,
               "started_at": time.time()}
        _pulls[model] = job
    threading.Thread(target=_pull, args=(model, then), daemon=True, name=f"lifeos-pull-{model}").start()
    return dict(job)


STALL_S = 90  # no new bytes for this long while downloading = stalled (Ollama's HF downloads sometimes freeze)
QUIET_S = 900  # no message at all for this long (verifying a 13 GB file can take a while) = stalled
RETRIES = 10


def _backoff(attempt: int) -> float:
    return min(30, 2 + 3 * attempt)


class _Stalled(Exception):
    pass


def _pull(model: str, then: Any) -> None:
    """Pull with a stall watchdog: a download that stops moving is cut and started again. Ollama keeps the parts it
    already has, so each retry resumes where the last one stopped instead of starting over."""
    job = _pulls[model]
    try:
        for attempt in range(RETRIES + 1):
            try:
                _pull_once(model, job)
                break
            except (_Stalled, httpx.TransportError) as exc:
                if attempt == RETRIES:
                    raise RuntimeError(f"the download kept stalling ({exc}); try again later") from exc
                job.update(status=f"stalled, resuming (try {attempt + 2} of {RETRIES + 1})", retries=attempt + 1)
                log.warning("Pull of %s stalled (%s); resuming", model, exc)
                time.sleep(_backoff(attempt))
        llm.status(force=True)
        if then:
            then()
        job["state"] = "done"
        activity.log("system", f"Downloaded model {model}")
    except Exception as exc:  # noqa: BLE001 (reported on the job)
        job.update(state="failed", error=str(exc)[:300])
        activity.log("system", f"Couldn't download {model}: {exc}"[:300], status="error")


def _pull_once(model: str, job: dict[str, Any]) -> None:
    seen = {"line": time.monotonic(), "bytes": time.monotonic(), "completed": -1}
    stalled: list[str] = []
    done = threading.Event()
    with httpx.Client(timeout=httpx.Timeout(None, connect=10)) as client, \
            client.stream("POST", f"{_base()}/api/pull", json={"model": model, "stream": True}) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(r.text[:200] or f"HTTP {r.status_code}")

        sock = r.extensions.get("network_stream")
        sock = sock.get_extra_info("socket") if sock is not None else None

        def cut() -> None:
            try:
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)  # unblocks the read; closing the response alone may not
            except OSError:
                pass
            r.close()

        def watch() -> None:
            while not done.wait(2):
                now = time.monotonic()
                downloading = str(job.get("status", "")).startswith("pulling")
                if (downloading and now - seen["bytes"] > STALL_S) or now - seen["line"] > QUIET_S:
                    stalled.append(f"no progress for {int(now - min(seen['bytes'], seen['line']))} s")
                    cut()
                    return

        threading.Thread(target=watch, daemon=True, name=f"lifeos-pull-watch-{model}").start()
        try:
            for line in r.iter_lines():
                seen["line"] = time.monotonic()
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                status = chunk.get("status") or job["status"]
                if status != job["status"]:
                    seen["bytes"] = time.monotonic()  # a new file or step starts its own clock
                job["status"] = status
                if chunk.get("total"):
                    completed = int(chunk.get("completed") or 0)
                    if completed != seen["completed"]:
                        seen["completed"], seen["bytes"] = completed, time.monotonic()
                    job["total"], job["completed"] = int(chunk["total"]), completed
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if stalled:
                raise _Stalled(stalled[0]) from exc
            if isinstance(exc, httpx.TransportError):
                raise
            raise RuntimeError(f"broken reply from Ollama: {exc}") from exc
        finally:
            done.set()
        if stalled:
            raise _Stalled(stalled[0])
        if not str(job["status"]).lower().startswith("success"):
            raise _Stalled(f"Ollama ended the download at \"{job['status']}\"")


def pulls() -> list[dict[str, Any]]:
    return [dict(j) for j in _pulls.values()]


def installed() -> list[dict[str, Any]]:
    try:
        tags = httpx.get(f"{_base()}/api/tags", timeout=5).json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return []
    used = {a["wanted_model"] for a in crew.list_agents()} | {settings.get("chat_model"), settings.get("embed_model"),
                                                               settings.get("fast_model")}
    return [{"name": m.get("name"), "size": int(m.get("size") or 0),
             "in_use": any(llm.installed(u, [m.get("name")]) for u in used if u)} for m in tags]


def remove(model: str) -> dict[str, Any]:
    if any(m["name"] == model and m["in_use"] for m in installed()):
        raise ValueError(f"{model} is still assigned to an agent or setting")
    try:
        r = httpx.request("DELETE", f"{_base()}/api/delete", json={"model": model}, timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Ollama couldn't remove {model}: {exc}") from exc
    llm.status(force=True)
    activity.log("system", f"Removed model {model} to free disk space")
    return {"removed": model}


# ---------------------------------------------------------------- the preset

def _assign(slug: str, model: str) -> None:
    try:
        agent = crew.get(slug)
    except KeyError:
        return
    if agent["builtin"] and agent["model"] != model:
        crew.update(slug, {"model": model})


def apply(download: bool = True) -> dict[str, Any]:
    """Turn on Low-resource mode: settings, model plan, Ollama tuning. Missing models download in the background;
    an agent whose model isn't there yet keeps using the main model until it is."""
    from . import services

    have = llm.status(force=True)["models"]
    changes = dict(LEAN_SETTINGS)
    if not llm.installed(MAIN_MODEL, have) and have:
        changes.pop("chat_model")  # keep what works until the main model is downloaded
    gpu = settings.get("ollama_gpu_gb")
    settings.update(changes)
    for slug, model in PLAN.items():
        _assign(slug, model)
    started = []
    if download:
        for model in dict.fromkeys(PLAN.values()):
            if not llm.installed(model, have):
                then = (lambda: settings.update({"chat_model": MAIN_MODEL})) if model == MAIN_MODEL else None
                pull(model, then)
                started.append(model)
    tuning = services.apply_tuning(restart=True)
    activity.log("system", "Low-resource mode on: thinking off, shared models, compressed context cache"
                 + (f"; downloading {', '.join(started)}" if started else ""))
    return {"settings": changes, "downloading": started, "tuning": tuning,
            "gpu_hint": "Set GPU memory for models to about 13.5 GB on a 16 GB card" if not gpu else None,
            **status()}


def status() -> dict[str, Any]:
    from . import services

    cfg = settings.get_all()
    on = all(cfg.get(k) == v for k, v in LEAN_SETTINGS.items() if k != "chat_model")
    have = llm.status()["models"]
    agents = {a["slug"]: a for a in crew.list_agents()}
    plan = [{"agent": agents[s]["name"] if s in agents else s, "slug": s, "model": m,
             "installed": llm.installed(m, have), "assigned": s in agents and agents[s]["model"] == m}
            for s, m in PLAN.items()]
    removable = [m for m in REPLACED if llm.installed(m, have) and not any(
        a["wanted_model"] == m for a in agents.values())]
    return {"on": on, "plan": plan, "pulls": pulls(), "tuning": services.tuning_status(), "removable": removable,
            "think": cfg.get("ollama_think"), "fast_model": cfg.get("fast_model")}
