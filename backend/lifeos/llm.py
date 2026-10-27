"""Ollama client: chat with structured (JSON-schema) output and embeddings."""
from __future__ import annotations

import json
import math
import struct
import time
from typing import Any

import httpx

from . import settings


class LLMUnavailable(Exception):
    pass


_status_cache: dict[str, Any] = {"checked": 0.0, "ok": False, "models": [], "url": None}


def _base() -> str:
    return settings.get("ollama_url").rstrip("/")


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


def chat_json(messages: list[dict[str, str]], schema: dict[str, Any], *, model: str | None = None,
              timeout: float = 180) -> dict[str, Any]:
    """Run a chat completion constrained to `schema` and return the parsed JSON object."""
    payload = {
        "model": model or settings.get("chat_model"),
        "messages": messages,
        "format": schema,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    try:
        r = httpx.post(f"{_base()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"Ollama request failed: {exc}") from exc
    content = r.json().get("message", {}).get("content", "")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Model returned invalid JSON: {content[:200]}") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("Model returned non-object JSON")
    return data


def chat_text(messages: list[dict[str, str]], *, model: str | None = None, timeout: float = 180) -> str:
    payload = {"model": model or settings.get("chat_model"), "messages": messages, "stream": False}
    try:
        r = httpx.post(f"{_base()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"Ollama request failed: {exc}") from exc
    return r.json().get("message", {}).get("content", "").strip()


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
