"""Model access: local Ollama by default, optional cloud providers, usage ledger.

A model reference is either a plain Ollama model name ("llama3.1:8b") or "provider:model"
("openai:gpt-4o-mini", "anthropic:claude-sonnet-4-5", "openrouter:qwen/qwen3-32b", ...).
Cloud providers are refused unless the user enabled cloud AI in Settings, has stored a key,
and today's spend is under the daily cap. Every call's token usage goes to the ledger.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import struct
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

import httpx

from . import db, settings, vault

log = logging.getLogger("lifeos.llm")


class LLMUnavailable(Exception):
    pass


class BadOutput(LLMUnavailable):
    """The model answered, but not with usable JSON."""


class Aborted(Exception):
    """The mission this request belongs to was stopped. Not an LLMUnavailable, so nothing retries or works around it."""


# Set by missions: the stop event for a mission id. Requests made inside track(mission_id) end as soon as it's set.
abort_for: Callable[[int], threading.Event] | None = None


last_abort = {"at": 0.0}  # when a local request was last cut off by Stop


def _abort_event() -> threading.Event | None:
    mission_id = (getattr(_local, "attr", None) or (None, None))[0]
    return abort_for(mission_id) if mission_id and abort_for else None


# Small local models often write these instead of leaving a field out.
NULLISH = {"null", "none", "undefined", "n/a", "na", "nil", "nothing", "(none)", "[]", "{}"}
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean(value: Any) -> Any:
    """Recursively turn null-like strings ("null", "None", "N/A", "") into None and drop them from objects and lists."""
    if isinstance(value, str):
        v = value.strip()
        return None if not v or v.lower() in NULLISH else v
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = clean(v)
            if v is not None:
                out[k] = v
        return out
    if isinstance(value, list):
        return [x for x in (clean(v) for v in value) if x is not None]
    return value


# OpenAI-compatible chat-completions endpoints. Anthropic has its own API.
OPENAI_COMPAT = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "custom": "",
}
PROVIDERS = ("ollama", "anthropic", *OPENAI_COMPAT)
PROVIDER_LABELS = {
    "ollama": "Ollama (local)", "anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter",
    "groq": "Groq", "mistral": "Mistral", "deepseek": "DeepSeek", "xai": "xAI (Grok)", "gemini": "Google Gemini",
    "custom": "Custom OpenAI-compatible (LM Studio, vLLM, llama.cpp)",
}

_status_cache: dict[str, Any] = {"checked": 0.0, "ok": False, "models": [], "url": None}
_local = threading.local()


def _base() -> str:
    return settings.get("ollama_url").rstrip("/")


def parse_ref(ref: str | None) -> tuple[str, str]:
    """Split a model reference into (provider, model)."""
    ref = (ref or "").strip() or settings.get("chat_model")
    head, sep, rest = ref.partition(":")
    if sep and head.lower() in PROVIDERS and rest:
        return head.lower(), rest
    return "ollama", ref


def is_cloud(ref: str | None) -> bool:
    return parse_ref(ref)[0] not in ("ollama", "custom")


def key_name(provider: str) -> str:
    return f"provider:{provider}"


def status(force: bool = False) -> dict[str, Any]:
    """Return {'ok', 'models', 'url'} for the configured Ollama server, cached for 15 s."""
    url = _base()
    if not force and _status_cache["url"] == url and time.time() - _status_cache["checked"] < 15:
        return {k: _status_cache[k] for k in ("ok", "models", "url")}
    try:
        r = httpx.get(f"{url}/api/tags", timeout=2)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        ok = True
    except (httpx.HTTPError, ValueError):
        models, ok = [], False
    _status_cache.update(checked=time.time(), ok=ok, models=models, url=url)
    return {"ok": ok, "models": models, "url": url}


def providers() -> list[dict[str, Any]]:
    out = []
    for p in PROVIDERS:
        needs_key = p not in ("ollama", "custom")
        out.append({"id": p, "label": PROVIDER_LABELS[p], "cloud": p not in ("ollama", "custom"),
                    "needs_key": needs_key, "has_key": needs_key and vault.has_secret(key_name(p))})
    return out


def installed(model: str, models: list[str]) -> bool:
    """Whether an Ollama model name is among the pulled ones ("llama3.1" matches "llama3.1:latest")."""
    if model in models:
        return True
    if ":" not in model:
        return f"{model}:latest" in models
    return model.endswith(":latest") and model[:-7] in models


def resolve(ref: str | None) -> str:
    """The model to actually use: an Ollama model that isn't pulled falls back to the default chat model."""
    ref = (ref or "").strip() or settings.get("chat_model")
    provider, name = parse_ref(ref)
    if provider != "ollama":
        return ref
    st = status()
    if not st["ok"] or not st["models"] or installed(name, st["models"]):
        return ref
    default = settings.get("chat_model")
    return default if installed(default, st["models"]) else ref


def model_available(ref: str | None) -> bool:
    """Cheap check used to decide between model planning and the offline fallback."""
    provider, model = parse_ref(ref)
    if provider == "ollama":
        st = status()
        return st["ok"] and (not st["models"] or installed(model, st["models"]))
    if provider == "custom":
        return bool(settings.get("custom_provider_url"))
    return settings.get("cloud_enabled") and vault.has_secret(key_name(provider))


# ---------------------------------------------------------------- usage ledger

@contextmanager
def track(mission_id: int | None = None, agent: str | None = None) -> Iterator[None]:
    """Attribute model usage inside this block (on this thread) to a mission and agent."""
    prev = getattr(_local, "attr", None)
    _local.attr = (mission_id, agent)
    try:
        yield
    finally:
        _local.attr = prev


def _price(ref: str) -> tuple[float, float]:
    prices = settings.get("model_prices")
    p = prices.get(ref) or prices.get(ref.split(":", 1)[-1]) or {}
    return float(p.get("in", 0) or 0), float(p.get("out", 0) or 0)


def _record(provider: str, model: str, tokens_in: int, tokens_out: int) -> None:
    ref = model if provider == "ollama" else f"{provider}:{model}"
    pin, pout = _price(ref)
    cost = (tokens_in * pin + tokens_out * pout) / 1_000_000
    mission_id, agent = getattr(_local, "attr", None) or (None, None)
    with db.session() as conn:
        conn.execute("INSERT INTO ledger(mission_id, agent, model, provider, tokens_in, tokens_out, cost_usd, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (mission_id, agent, ref, provider, tokens_in, tokens_out, cost, db.now()))
        if mission_id:
            conn.execute("UPDATE missions SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?, "
                         "cost_usd = cost_usd + ? WHERE id = ?", (tokens_in, tokens_out, cost, mission_id))


def record_cost(provider: str, model: str, cost_usd: float, mission_id: int | None = None, agent: str | None = None) -> None:
    """Record a priced call that isn't token-based (for example an image)."""
    attr = getattr(_local, "attr", None) or (None, None)
    mission_id, agent = mission_id or attr[0], agent or attr[1]
    with db.session() as conn:
        conn.execute("INSERT INTO ledger(mission_id, agent, model, provider, tokens_in, tokens_out, cost_usd, created_at) "
                     "VALUES (?,?,?,?,0,0,?,?)", (mission_id, agent, f"{provider}:{model}", provider, cost_usd, db.now()))
        if mission_id:
            conn.execute("UPDATE missions SET cost_usd = cost_usd + ? WHERE id = ?", (cost_usd, mission_id))


_ps_cache: dict[str, Any] = {"checked": 0.0, "models": []}


def loaded_models() -> list[dict[str, Any]]:
    """Models Ollama has in memory right now and how much of each sits in VRAM, cached for 5 s."""
    if time.time() - _ps_cache["checked"] < 5:
        return _ps_cache["models"]
    try:
        raw = httpx.get(f"{_base()}/api/ps", timeout=2).json().get("models") or []
        models = [{"name": m.get("name") or m.get("model", "?"), "size": int(m.get("size") or 0),
                   "vram": int(m.get("size_vram") or 0), "until": m.get("expires_at")} for m in raw]
    except (httpx.HTTPError, ValueError, AttributeError):
        models = []
    _ps_cache.update(checked=time.time(), models=models)
    return models


def unload_local_models() -> int:
    """Ask Ollama to free GPU memory (used before image/video generation on the same GPU)."""
    try:
        loaded = httpx.get(f"{_base()}/api/ps", timeout=3).json().get("models") or []
        for m in loaded:
            httpx.post(f"{_base()}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
        return len(loaded)
    except (httpx.HTTPError, ValueError, KeyError):
        return 0


def spend_today() -> dict[str, Any]:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with db.session() as conn:
        row = conn.execute("SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout, "
                           "COALESCE(SUM(cost_usd),0) AS cost, COALESCE(SUM(CASE WHEN provider NOT IN ('ollama','custom') "
                           "THEN cost_usd ELSE 0 END),0) AS cloud FROM ledger WHERE created_at >= ?",
                           (start.isoformat(timespec="seconds"),)).fetchone()
    return {"tokens_in": row["tin"], "tokens_out": row["tout"], "cost_usd": round(row["cost"], 4),
            "cloud_cost_usd": round(row["cloud"], 4), "cloud_budget_usd": settings.get("cloud_daily_budget_usd")}


def ledger(days: int = 7) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.session() as conn:
        by_model = db.rows_to_dicts(conn.execute(
            "SELECT model, provider, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
            "SUM(cost_usd) AS cost_usd FROM ledger WHERE created_at >= ? GROUP BY model ORDER BY cost_usd DESC, calls DESC",
            (since,)).fetchall())
        by_agent = db.rows_to_dicts(conn.execute(
            "SELECT COALESCE(agent, 'assistant') AS agent, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, "
            "SUM(tokens_out) AS tokens_out, SUM(cost_usd) AS cost_usd FROM ledger WHERE created_at >= ? "
            "GROUP BY COALESCE(agent, 'assistant') ORDER BY calls DESC", (since,)).fetchall())
        by_day = db.rows_to_dicts(conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, SUM(tokens_in + tokens_out) AS tokens, SUM(cost_usd) AS cost_usd "
            "FROM ledger WHERE created_at >= ? GROUP BY day ORDER BY day", (since,)).fetchall())
    return {"days": days, "by_model": by_model, "by_agent": by_agent, "by_day": by_day, "today": spend_today()}


def _check_cloud(provider: str) -> str:
    if not settings.get("cloud_enabled"):
        raise LLMUnavailable(f"Cloud AI is turned off (Settings → Cloud AI); {provider} was not called")
    key = vault.get_secret(key_name(provider))
    if not key:
        raise LLMUnavailable(f"No API key stored for {provider}")
    today = spend_today()
    if today["cloud_cost_usd"] >= today["cloud_budget_usd"]:
        raise LLMUnavailable(f"Daily cloud budget of ${today['cloud_budget_usd']:.2f} reached")
    return key


# ---------------------------------------------------------------- completion

def _extract_json(text: str) -> dict[str, Any]:
    text = _THINK.sub("", text).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise BadOutput(f"Model returned invalid JSON: {text[:200]}")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise BadOutput(f"Model returned invalid JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise BadOutput("Model returned non-object JSON")
    return clean(data)


def _schema_hint(schema: dict[str, Any]) -> str:
    return ("Reply with a single JSON object only, no prose, matching this JSON schema:\n"
            + json.dumps(schema, separators=(",", ":")))


class _ModelGate:
    """Lets any number of requests for the loaded model run, but makes a different model wait until they finish.
    On a switch the previous model can be unloaded first, so the next one isn't squeezed into what's left of VRAM."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._model: str | None = None
        self._users = 0

    @contextmanager
    def __call__(self, model: str, enabled: bool, unload_previous: bool = False) -> Iterator[None]:
        if not enabled:
            yield
            return
        abort = _abort_event()
        with self._cond:
            while self._users and self._model != model:
                if abort is not None and abort.is_set():
                    raise Aborted("Stopped")
                self._cond.wait(0.5)
            previous = self._model
            self._model = model
            self._users += 1
        try:
            if unload_previous and previous and previous != model:
                unload_model(previous)
            yield
        finally:
            with self._cond:
                self._users -= 1
                self._cond.notify_all()


    def current(self) -> str | None:
        """The model the gate let through last (it's loaded unless keep_alive ran out)."""
        return self._model


_gate = _ModelGate()
GB = 1024 ** 3
SMALL_MODEL_BYTES = 3.5 * 1e9  # Ollama tags count GB as 10^9 bytes; the 4B fast models are about 2.5 GB
KV_BYTES = {"f16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}  # per cached value, including the quantization scales
_residents: set[str] = set()  # small models running beside the gate's model
_no_think_param: set[str] = set()  # Ollama rejected "think" for these
_think_breaks_json: set[str] = set()  # this Ollama ignores the JSON schema when thinking is off for these
_sent_think: dict[str, Any] = {}

BIG_MODEL = re.compile(r"(?<![\d.])(2[4-9]|[3-9]\d|\d{3})b\b", re.I)  # 24b and up: these don't fit next to a desktop
BIG_MODEL_CTX = 8192
_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def model_options(model: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ollama options for one request: context window, how many layers go on the GPU, and CPU threads."""
    cfg = cfg or settings.get_all()
    own = (cfg.get("ollama_model_options") or {}).get(model) or {}
    out: dict[str, Any] = {}
    ctx = own.get("num_ctx") or cfg["ollama_num_ctx"]
    if ctx and not own.get("num_ctx") and BIG_MODEL.search(model):
        ctx = min(ctx, BIG_MODEL_CTX)  # every 8k of context is VRAM the model's own layers can't use
    if ctx and not own.get("num_ctx") and _is_fast(model, cfg):
        ctx = min(ctx, FAST_MODEL_CTX)  # chats and one-step jobs: a small cache keeps it beside the main model
    if ctx:
        out["num_ctx"] = int(ctx)
    gpu = own.get("num_gpu")
    if gpu is None and cfg.get("ollama_gpu_gb"):
        gpu = gpu_layers(model, float(cfg["ollama_gpu_gb"]), int(ctx or 4096), kv_bytes(cfg))
    if gpu is not None:
        out["num_gpu"] = int(gpu)
    threads = own.get("num_thread") or cfg.get("ollama_cpu_threads") or default_threads()
    if threads:
        out["num_thread"] = int(threads)
    return out


FAST_MODEL_CTX = 8192


def _is_fast(model: str, cfg: dict[str, Any]) -> bool:
    fast = parse_ref(cfg.get("fast_model") or "")
    return bool(cfg.get("fast_model")) and fast[0] == "ollama" and fast[1] == model


def kv_bytes(cfg: dict[str, Any] | None = None) -> float:
    """Bytes per value in Ollama's context cache, as it actually runs (q8_0 only takes effect with flash attention)."""
    from . import services

    return KV_BYTES.get(services.kv_cache_type(cfg), 2.0)


def default_threads() -> int:
    """All but two physical cores (assumes two threads per core), so Windows keeps some when a model spills over."""
    logical = os.cpu_count() or 4
    return max(1, logical // 2 - 2) if logical >= 8 else max(1, logical - 1)


def _model_info(model: str) -> dict[str, Any]:
    """Size on disk and layer shape of a pulled model, from Ollama (cached for an hour)."""
    hit = _info_cache.get(model)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    info: dict[str, Any] = {}
    try:
        tags = httpx.get(f"{_base()}/api/tags", timeout=5).json().get("models") or []
        size = next((m.get("size") for m in tags if m.get("name") == model or m.get("model") == model), None)
        shown = httpx.post(f"{_base()}/api/show", json={"model": model}, timeout=10).json()
        show = shown.get("model_info") or {}
        arch = show.get("general.architecture", "")
        caps = shown.get("capabilities")
        info = {"size": int(size or 0), "layers": int(show.get(f"{arch}.block_count") or 0),
                "capabilities": [str(c) for c in caps] if isinstance(caps, list) else None,
                "kv_heads": int(show.get(f"{arch}.attention.head_count_kv") or 0),
                "key_len": int(show.get(f"{arch}.attention.key_length") or 0)
                or (int(show.get(f"{arch}.embedding_length") or 0) // max(1, int(show.get(f"{arch}.attention.head_count") or 1)))}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        info = {}
    _info_cache[model] = (time.time(), info)
    return info


def gpu_layers(model: str, gpu_gb: float, ctx: int, kv: float = 2.0) -> int | None:
    """How many of the model's layers fit in gpu_gb of VRAM next to its context cache. None = all of them fit
    (or the model's shape is unknown), so Ollama decides as usual."""
    info = _model_info(model)
    layers, size = info.get("layers") or 0, info.get("size") or 0
    if not layers or not size:
        return None
    kv_heads, key_len = info.get("kv_heads") or 8, info.get("key_len") or 128
    cache = 2 * layers * ctx * kv_heads * key_len * kv  # keys and values
    budget = gpu_gb * 1024 ** 3 - cache - 0.75 * 1024 ** 3  # and room for the compute buffers
    per_layer = size / (layers + 1)  # the output layer is about one more
    fit = int(budget // per_layer)
    return None if fit > layers else max(0, fit)


def _loaded_now(model: str) -> bool:
    return any(m["name"] == model or m["name"] == f"{model}:latest" for m in loaded_models())


def light_model(fallback: str) -> str:
    """The model for light work like chatting with an agent: the fast model when it can run without swapping anything
    out, else the local model that's loaded right now, else fallback."""
    cfg = settings.get_all()
    fast = (cfg.get("fast_model") or "").strip()
    current = _gate.current()
    if fast and parse_ref(fast)[0] == "ollama" and model_available(fast) and installed(fast, status()["models"]):
        if not current or current == fast or not _loaded_now(current) or _fits_together(current, fast, cfg):
            return fast
    if cfg.get("stay_on_loaded_model") and current and parse_ref(fallback)[0] == "ollama" and _loaded_now(current):
        return current
    return fallback


def loaded_or(model: str) -> str:
    """With "stay on the loaded model": the local model already in VRAM instead of `model`, so a review or report
    doesn't swap 10+ GB of weights in and out. Small residents (the fast model) don't count."""
    if not settings.get("stay_on_loaded_model") or parse_ref(model)[0] != "ollama":
        return model
    current = _gate.current()
    if current and current != model and current not in _residents and _loaded_now(current):
        return current
    return model


def unload_model(model: str) -> None:
    try:
        httpx.post(f"{_base()}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except Exception:  # noqa: BLE001 (best effort: Ollama evicts it anyway when it needs the room)
        log.warning("Couldn't unload %s", model)


_inflight = 0
_inflight_lock = threading.Lock()
_watcher: threading.Thread | None = None


@contextmanager
def _busy(cfg: dict[str, Any]) -> Iterator[None]:
    """While a local request runs, keep Ollama's processes (the runner starts when a model loads) at low CPU priority."""
    global _inflight, _watcher
    if not (cfg.get("ollama_low_priority") and os.name == "nt"):
        yield
        return
    with _inflight_lock:
        _inflight += 1
        if _watcher is None or not _watcher.is_alive():
            _watcher = threading.Thread(target=_priority_watch, daemon=True, name="lifeos-ollama-priority")
            _watcher.start()
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight -= 1


def _priority_watch() -> None:
    from . import services

    global _watcher
    while True:
        with _inflight_lock:
            if _inflight <= 0:
                _watcher = None
                return
        services.lower_priority(("ollama", "llama"))
        time.sleep(5)


def _think_option(model: str, cfg: dict[str, Any], schema: dict[str, Any] | None) -> bool | None:
    """False to skip a thinking model's hidden reasoning pass (most of a step's tokens), None to leave it to Ollama."""
    if cfg.get("ollama_think") or model in _no_think_param or (schema is not None and model in _think_breaks_json):
        return None
    caps = _model_info(model).get("capabilities")
    if caps is not None and "thinking" not in caps:
        return None  # nothing to turn off
    return False


def _fits_together(a: str, b: str, cfg: dict[str, Any]) -> bool:
    sa, sb = (_model_info(a).get("size") or 0), (_model_info(b).get("size") or 0)
    if not sa or not sb:
        return False
    budget = float(cfg.get("ollama_gpu_gb") or 12) * GB
    return sa + sb + 1.75 * GB <= budget  # both context caches and compute buffers


def _beside(model: str, cfg: dict[str, Any]) -> bool:
    """Whether a small model can run next to the model that's loaded now, without either being swapped out."""
    size = _model_info(model).get("size") or 0
    current = _gate.current()
    if not size or size > SMALL_MODEL_BYTES or not current or current == model:
        return False
    return _fits_together(current, model, cfg)


def _make_room(model: str, cfg: dict[str, Any]) -> None:
    """Before a bigger model takes over the GPU: unload small residents it doesn't fit next to."""
    for r in list(_residents):
        if r != model and not _fits_together(model, r, cfg):
            unload_model(r)
            _residents.discard(r)


def _recover(model: str, payload: dict[str, Any], exc: httpx.HTTPStatusError, cfg: dict[str, Any]) -> bool:
    """Fix what an Ollama error says is wrong with the request or server setup. True = send it again."""
    try:
        body = exc.response.text.lower()
    except Exception:  # noqa: BLE001 (a streamed body that wasn't read)
        body = ""
    if "think" in payload and exc.response.status_code == 400 and "think" in body:
        _no_think_param.add(model)
        payload.pop("think")
        return True
    if any(k in body for k in ("flash_attn", "flash attention", "llama context", "kv cache", "cache type")):
        from . import services

        return services.kv_fallback(f"{model}: {body[:160]}")
    return False


def _ollama(model: str, messages: list[dict[str, str]], schema: dict[str, Any] | None, timeout: float,
            temperature: float) -> str:
    cfg = settings.get_all()
    abortable = _abort_event() is not None  # inside a mission: stream, so Stop can cut it off
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": abortable,
                               "options": {"temperature": temperature}, "keep_alive": cfg["ollama_keep_alive"]}
    payload["options"].update(model_options(model, cfg))
    think = _think_option(model, cfg, schema)
    if think is not None:
        payload["think"] = think
    _sent_think[model] = think
    if schema is not None:
        payload["format"] = schema
    # Loading a large model (or one that spills into RAM) can take minutes, so the configured limit wins.
    limit = httpx.Timeout(max(timeout, float(cfg["ollama_timeout_s"])), connect=10)
    beside = bool(cfg["ollama_one_model"]) and _beside(model, cfg)
    if beside:
        _residents.add(model)
    elif cfg["ollama_unload_on_switch"] and _residents:
        _make_room(model, cfg)
    with _gate(model, bool(cfg["ollama_one_model"]) and not beside, bool(cfg["ollama_unload_on_switch"])), _busy(cfg):
        timed_out = fixed = False
        while True:
            try:
                if abortable:
                    data, text = _ollama_stream(f"{_base()}/api/chat", payload, limit)
                else:
                    r = httpx.post(f"{_base()}/api/chat", json=payload, timeout=limit)
                    r.raise_for_status()
                    data = r.json()
                    text = data.get("message", {}).get("content", "")
                break
            except httpx.TimeoutException as exc:
                if timed_out:
                    raise LLMUnavailable(f"Ollama request failed: timed out after {int(limit.read or 0)}s twice "
                                         f"(model {model}). Raise the time limit in Settings → Local AI, or use a "
                                         "smaller model.") from exc
                timed_out = True
                log.warning("Ollama timed out on %s; retrying once", model)
            except httpx.HTTPStatusError as exc:
                if not fixed and _recover(model, payload, exc, cfg):
                    fixed = True
                    continue
                try:
                    detail = exc.response.text[:240]
                except Exception:  # noqa: BLE001
                    detail = ""
                raise LLMUnavailable(f"Ollama request failed: {exc.response.status_code} {detail}".strip()) from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable(f"Ollama request failed: {exc}") from exc
    _record("ollama", model, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0))
    return text


def _ollama_stream(url: str, payload: dict[str, Any], limit: httpx.Timeout) -> tuple[dict[str, Any], str]:
    """POST a streaming chat and collect it. Streaming is what makes Stop work: when the mission is stopped the
    connection is cut, and Ollama stops generating (a plain request keeps the GPU busy until the answer is done)."""
    abort = _abort_event()
    if abort is not None and abort.is_set():
        raise _aborted()
    parts: list[str] = []
    last: dict[str, Any] = {}
    done = threading.Event()
    with httpx.Client(timeout=limit) as client, client.stream("POST", url, json=payload) as r:
        if r.status_code >= 400:
            r.read()
            r.raise_for_status()
        if abort is not None:  # also cut it while Ollama is still loading the model or reading the prompt
            sock = r.extensions.get("network_stream")
            sock = sock.get_extra_info("socket") if sock is not None else None

            def watch() -> None:
                while not done.wait(0.25):
                    if abort.is_set():
                        try:
                            if sock is not None:
                                sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return

            threading.Thread(target=watch, daemon=True, name="lifeos-ollama-abort").start()
        try:
            for line in r.iter_lines():
                if abort is not None and abort.is_set():
                    raise _aborted()
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LLMUnavailable(f"Ollama: {chunk['error']}")
                parts.append((chunk.get("message") or {}).get("content") or "")
                last = chunk
            if abort is not None and abort.is_set():  # the cut connection can also read as a normal end
                raise _aborted()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            if abort is not None and abort.is_set():
                raise _aborted() from None
            if isinstance(exc, httpx.HTTPError):
                raise
            raise LLMUnavailable(f"Ollama sent a broken reply: {exc}") from exc
        finally:
            done.set()
    return last, "".join(parts)


def _aborted() -> Aborted:
    last_abort["at"] = time.time()
    return Aborted("Stopped")


def _openai_compat(provider: str, model: str, messages: list[dict[str, str]], schema: dict[str, Any] | None,
                   timeout: float, temperature: float) -> str:
    if provider == "custom":
        base = settings.get("custom_provider_url").rstrip("/")
        if not base:
            raise LLMUnavailable("No URL set for the custom provider")
        key = vault.get_secret(key_name("custom")) or "none"
    else:
        base = OPENAI_COMPAT[provider]
        key = _check_cloud(provider)
    msgs = list(messages)
    if schema is not None:
        msgs = [{"role": "system", "content": _schema_hint(schema)}, *msgs]
    payload: dict[str, Any] = {"model": model, "messages": msgs, "temperature": temperature}
    if schema is not None:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/SoulVoid0/ai-lifeos", "X-Title": "AI LifeOS"})
    try:
        r = httpx.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
        if r.status_code == 400 and "response_format" in payload:
            payload.pop("response_format")  # some servers don't support JSON mode; the prompt still asks for JSON
            r = httpx.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(f"{provider} returned {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"{provider} request failed: {exc}") from exc
    data = r.json()
    usage = data.get("usage") or {}
    _record(provider, model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
    choices = data.get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""


def _anthropic(model: str, messages: list[dict[str, str]], schema: dict[str, Any] | None, timeout: float,
               temperature: float) -> str:
    key = _check_cloud("anthropic")
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    if schema is not None:
        system = (system + "\n\n" if system else "") + _schema_hint(schema)
    turns: list[dict[str, str]] = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + m["content"]
        else:
            turns.append({"role": role, "content": m["content"]})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "(start)"})
    payload: dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": turns, "temperature": temperature}
    if system:
        payload["system"] = system
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", json=payload, timeout=timeout,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(f"anthropic returned {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"anthropic request failed: {exc}") from exc
    data = r.json()
    usage = data.get("usage") or {}
    _record("anthropic", model, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def complete(messages: list[dict[str, str]], *, model: str | None = None, schema: dict[str, Any] | None = None,
             timeout: float = 180, temperature: float = 0.1) -> str:
    provider, name = parse_ref(model)
    if provider == "ollama":
        return _ollama(name, messages, schema, timeout, temperature)
    if provider == "anthropic":
        return _anthropic(name, messages, schema, timeout, temperature)
    return _openai_compat(provider, name, messages, schema, timeout, temperature)


def chat_json(messages: list[dict[str, str]], schema: dict[str, Any], *, model: str | None = None,
              timeout: float = 180) -> dict[str, Any]:
    """Run a chat completion constrained to `schema` and return the parsed JSON object (null-like values removed).

    Invalid JSON gets one retry with the error shown to the model."""
    text = complete(messages, model=model, schema=schema, timeout=timeout)
    try:
        return _extract_json(text)
    except BadOutput as exc:
        provider, name = parse_ref(model)
        if provider == "ollama" and _sent_think.get(name) is False:
            _think_breaks_json.add(name)  # some Ollama versions drop the schema with thinking off: let it think
            log.warning("%s ignored the JSON schema with thinking off; letting it think for JSON replies", name)
        retry = [*messages, {"role": "assistant", "content": text[:2000]},
                 {"role": "user", "content": f"That reply was not valid JSON ({exc}). Reply again with exactly one JSON "
                  "object that matches the schema. Leave out fields you have no value for instead of writing null."}]
        return _extract_json(complete(retry, model=model, schema=schema, timeout=timeout))


def chat_text(messages: list[dict[str, str]], *, model: str | None = None, timeout: float = 180) -> str:
    return _THINK.sub("", complete(messages, model=model, timeout=timeout, temperature=0.3)).strip()


def test_model(ref: str) -> dict[str, Any]:
    """Send a tiny request to check a model reference works end to end."""
    started = time.monotonic()
    try:
        text = chat_text([{"role": "user", "content": "Reply with the single word: ready"}], model=ref, timeout=60)
    except LLMUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "reply": text[:80], "seconds": round(time.monotonic() - started, 2)}


# ---------------------------------------------------------------- embeddings (local)

def embed(texts: list[str]) -> list[list[float]] | None:
    """Embed texts with the configured embedding model. Returns None when unavailable."""
    if not texts or settings.get("planner") == "rules" or not status()["ok"]:
        return None
    try:
        r = httpx.post(f"{_base()}/api/embed", json={"model": settings.get("embed_model"), "input": texts}, timeout=60)
        r.raise_for_status()
        vectors = r.json().get("embeddings")
    except (httpx.HTTPError, ValueError):
        return None
    if not vectors or len(vectors) != len(texts):
        return None
    return vectors


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
