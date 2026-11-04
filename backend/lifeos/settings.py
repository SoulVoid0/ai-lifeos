"""User settings stored as JSON values in the settings table."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import db

# Tool policies: "auto" runs immediately, "confirm" queues an action for approval, "deny" blocks it.
MAX_STEPS = 60  # per task, for one agent's setting and the crew-wide one

DEFAULTS: dict[str, Any] = {
    "ollama_url": "http://127.0.0.1:11434",
    # Start `ollama serve` hidden when LifeOS starts and Ollama isn't running.
    "auto_start_ollama": True,
    # Backups (backup.py): a zip of the data folder, e.g. into a OneDrive folder to move to another PC.
    "backup_folder": "",
    "backup_daily": False,
    "backup_keep": 7,
    "backup_include_media": False,
    "backup_last_at": "",
    # Read the assistant's replies aloud (browser speech, no extra model).
    "speak_replies": False,
    # Seconds a local model may take to answer, including loading it into VRAM (large models spill to RAM).
    "ollama_timeout_s": 600,
    # Run one local model at a time: agents on different models take turns instead of swapping them in and out
    # of VRAM at the same time (which is what makes requests time out on a single GPU).
    "ollama_one_model": True,
    # How long Ollama keeps a model loaded after a request, so the next step doesn't reload it.
    "ollama_keep_alive": "30m",
    # Context window (tokens) for local models. Ollama's own default (2048-4096) silently cuts the start of a long
    # task, including the agent's instructions; 0 leaves it to Ollama.
    "ollama_num_ctx": 16384,
    # Keeping the desktop usable while a model bigger than the GPU runs (it spills onto the CPU and RAM):
    # GB of VRAM local models may use (0 = Ollama decides, which fills the GPU), CPU threads for the part that runs on
    # the CPU (0 = all but two cores), and a lower CPU priority for Ollama so Windows comes first.
    "ollama_gpu_gb": 0.0,
    "ollama_cpu_threads": 0,
    "ollama_low_priority": True,
    # Per model, e.g. {"qwen3-coder:30b": {"num_ctx": 8192, "num_gpu": 30}}; overrides the settings above.
    "ollama_model_options": {},
    # When agents switch to a different local model, unload the previous one first so the new one gets all the VRAM.
    "ollama_unload_on_switch": True,
    # If Ollama keeps computing a few seconds after Stop (it can, while reading a long prompt with part of the model
    # on the CPU), restart it. Only when no other mission is still running.
    "ollama_hard_stop": True,
    # Resource savers (see lean.py). Thinking models (qwen3, ...) write a long hidden "thinking" pass before every
    # answer; off skips it, which is most of the tokens an agent step costs. On = let the model think.
    "ollama_think": False,
    # Server settings LifeOS gives Ollama when it starts it (and saves for the Ollama app): flash attention, a
    # quantized context cache (q8_0 halves its VRAM; f16 = Ollama's default), one request slot per model (so the
    # prompt cache is reused step after step) and at most two models in memory.
    "ollama_tuning": True,
    "ollama_flash_attention": True,
    "ollama_kv_cache": "q8_0",
    # A small, fast model for chat with agents and the Spark micro agent. It stays loaded beside a mid-size model
    # instead of swapping it out. Empty = don't use one.
    "fast_model": "",
    # The Overseer's reviews and final report use the local model that's already loaded instead of loading its own.
    "stay_on_loaded_model": False,
    # GitHub repository whose releases the desktop app updates from (owner/name). A read token for a private
    # repository is kept in the vault (see updates.py).
    "update_repo": "SoulVoid0/ai-lifeos",
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
    # Local models count too, so this is sized for long coding tasks (every step resends the conversation).
    "mission_token_budget": 500000,
    # Steps every agent gets per task at least; an agent's own setting can be higher (Forge's is).
    "agent_max_steps": 10,
    # An agent still making progress at its step limit gets one automatic extension, then asks you to continue.
    "agent_auto_extend": True,
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
    "notification_prefs": {"reminders": True, "workflows": True, "missions": True, "suggestions": False, "apps": True},
    # Linux: receive other apps' desktop notifications in the LifeOS bell (notify_bus.py).
    "os_notifications": True,
    # The first-start setup screen (name, Wi-Fi, models, folders) was finished or skipped.
    "setup_done": False,
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "email": {"host": "", "port": 993, "username": "", "folder": "INBOX"},
    # Email alerts over SMTP (mailer.py); the password is in the vault.
    "email_alerts": {"enabled": False, "to": "", "host": "smtp.gmail.com", "port": 465, "username": "",
                     "security": "ssl", "buttons": True, "events": {"mission_done": True, "mission_failed": True, "approval": True,
                                                   "question": True, "workflow": False}},
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
        if key == "max_parallel_agents" and not 1 <= value <= 12:
            raise ValueError(f"{key} must be between 1 and 12")
        if key == "agent_max_steps" and not 1 <= value <= MAX_STEPS:
            raise ValueError(f"{key} must be between 1 and {MAX_STEPS}")
        if key == "ollama_gpu_gb" and not 0 <= value <= 256:
            raise ValueError("ollama_gpu_gb must be between 0 and 256")
        if key == "ollama_cpu_threads" and not 0 <= value <= 256:
            raise ValueError("ollama_cpu_threads must be between 0 and 256")
        if key == "ollama_model_options":
            for name, opts in value.items():
                if not isinstance(opts, dict) or set(opts) - {"num_ctx", "num_gpu", "num_thread"} or not all(
                        isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 131072 for v in opts.values()):
                    raise ValueError(f"Options for {name} must be num_ctx, num_gpu and num_thread as whole numbers")
        if key == "ollama_num_ctx" and value and not 2048 <= value <= 131072:
            raise ValueError("ollama_num_ctx must be 0 (Ollama's default) or between 2048 and 131072")
        if key in ("mission_token_budget",) and value < 1000:
            raise ValueError("mission_token_budget must be at least 1000")
        if key == "ollama_timeout_s" and not 30 <= value <= 3600:
            raise ValueError("ollama_timeout_s must be between 30 and 3600 seconds")
        if key == "ollama_keep_alive" and not re.fullmatch(r"-?\d+[smh]?", value):
            raise ValueError("ollama_keep_alive must look like 30m, 2h or 0")
        if key == "ollama_kv_cache" and value not in ("f16", "q8_0", "q4_0"):
            raise ValueError("ollama_kv_cache must be f16, q8_0 or q4_0")
        if key == "backup_keep" and not 1 <= value <= 100:
            raise ValueError("backup_keep must be between 1 and 100")
        if key == "backup_folder" and value.strip():
            from pathlib import Path

            if not Path(value.strip()).expanduser().is_absolute():
                raise ValueError("Backup folder must be a full path, like A:\\Backups or your OneDrive folder")
        if key == "fast_model" and len(value) > 120:
            raise ValueError("fast_model is too long")
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
