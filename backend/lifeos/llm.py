"""Model access: local Ollama by default, optional cloud providers, usage ledger.

A model reference is either a plain Ollama model name ("llama3.1:8b") or "provider:model"
("openai:gpt-4o-mini", "anthropic:claude-sonnet-4-5", "openrouter:qwen/qwen3-32b", ...).
Cloud providers are refused unless the user enabled cloud AI in Settings, has stored a key,
and today's spend is under the daily cap. Every call's token usage goes to the ledger.
"""
from __future__ import annotations

import json
import math
import re
import struct
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx

from . import db, settings, vault


class LLMUnavailable(Exception):
    pass


class BadOutput(LLMUnavailable):
    """The model answered, but not with usable JSON."""


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


def _ollama(model: str, messages: list[dict[str, str]], schema: dict[str, Any] | None, timeout: float,
            temperature: float) -> str:
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                               "options": {"temperature": temperature}}
    if schema is not None:
        payload["format"] = schema
    try:
        r = httpx.post(f"{_base()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"Ollama request failed: {exc}") from exc
    data = r.json()
    _record("ollama", model, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0))
    return data.get("message", {}).get("content", "")


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
