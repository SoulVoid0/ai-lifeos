"""User settings stored as JSON values in the settings table."""
from __future__ import annotations

import copy
import json
from typing import Any

from . import db

# Tool policies: "auto" runs immediately, "confirm" queues an action for approval, "deny" blocks it.
DEFAULTS: dict[str, Any] = {
    "ollama_url": "http://127.0.0.1:11434",
    # Start `ollama serve` hidden when LifeOS starts and Ollama isn't running.
    "auto_start_ollama": True,
    "chat_model": "llama3.1:8b",
    "embed_model": "nomic-embed-text",
    # "auto": use the local model when reachable and fall back to the built-in rule planner.
    # "llm": always use the model. "rules": never call a model.
    "planner": "auto",
    "automation_paused": False,
    "proactive_level": "normal",  # off | low | normal
    "large_file_mb": 100,
    "stale_days": 180,
    "downloads_clutter_threshold": 25,
    "tool_policies": {},
    "theme_accent": "violet",
    "user_name": "",
    "dismissed_suggestions": [],
    # Crew (multi-agent) runtime.
    "max_parallel_agents": 3,
    "mission_token_budget": 60000,
    "agent_max_steps": 6,
    # Cloud models are off until the user turns them on; spend is capped per day.
    "cloud_enabled": False,
    "cloud_daily_budget_usd": 5.0,
    # USD per million tokens, keyed by model reference such as "openai:gpt-4o-mini": {"in": 0.15, "out": 0.6}.
    "model_prices": {},
    # Base URL for the OpenAI-compatible "custom" provider (LM Studio, vLLM, llama.cpp server, ...).
    "custom_provider_url": "",
    "web_access": False,
    # Learning: a teammate reviews each result (and the agent revises once if it scores low), the Overseer
    # turns finished missions into lessons, and auto-routing sends each agent's work to its best-scoring model.
    # Image/video generation backend (see media.py for the keys).
    "media": {},
    "peer_review": True,
    "reflection": True,
    "auto_route": False,
    "lessons_per_task": 5,
    "notification_prefs": {"reminders": True, "workflows": True, "missions": True, "suggestions": False},
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "email": {"host": "", "port": 993, "username": "", "folder": "INBOX"},
}


def get_all() -> dict[str, Any]:
    with db.session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    return {**copy.deepcopy(DEFAULTS), **{k: v for k, v in stored.items() if k in DEFAULTS}}


def get(key: str) -> Any:
    return get_all()[key]


def update(changes: dict[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        expected = type(DEFAULTS[key])
        if expected is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ValueError(f"Setting {key} must be {expected.__name__}")
        changes[key] = value
        if key == "planner" and value not in ("auto", "llm", "rules"):
            raise ValueError("planner must be auto, llm or rules")
        if key == "proactive_level" and value not in ("off", "low", "normal"):
            raise ValueError("proactive_level must be off, low or normal")
        if key in ("max_parallel_agents", "agent_max_steps") and not 1 <= value <= 12:
            raise ValueError(f"{key} must be between 1 and 12")
        if key in ("mission_token_budget",) and value < 1000:
            raise ValueError("mission_token_budget must be at least 1000")
        if key == "lessons_per_task" and not 0 <= value <= 20:
            raise ValueError("lessons_per_task must be between 0 and 20")
        if key == "cloud_daily_budget_usd" and value < 0:
            raise ValueError("cloud_daily_budget_usd can't be negative")
        if key == "tool_policies":
            for tool, policy in value.items():
                if policy not in ("auto", "confirm", "deny"):
                    raise ValueError(f"Policy for {tool} must be auto, confirm or deny")
    with db.session() as conn:
        for key, value in changes.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
    return get_all()
