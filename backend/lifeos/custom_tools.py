"""Tools you define without code, usable by the assistant, the crew and workflows.

Two kinds:
- webhook: a web request to an address you set (Home Assistant, IFTTT, Zapier, n8n, a Todoist or Notion
  automation, your own server). The AI only fills in the text you allow ({{input}}); it can't change the address,
  method or headers. Calls ask for your approval by default (Settings → Permissions → custom_webhook).
- prompt: a saved instruction run by your local model ("Rewrite as my weekly report: {{input}}"). Read-only.

A tool can be exported as a small .json "plugin" file and imported on another PC. A secret header value
(an API key) is kept in the vault, never in the database or in exported files.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from . import activity, db, llm, settings, vault

KINDS = ("webhook", "prompt")
METHODS = ("GET", "POST", "PUT", "PATCH")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
MAX_RESPONSE = 4000


def _secret_key(name: str) -> str:
    return f"custom_tool:{name.lower()}"


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not _NAME.match(name):
        raise ValueError("Name: up to 40 letters, digits, spaces, dots, dashes or underscores")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError("kind must be webhook or prompt")
    cfg = data.get("config") or {}
    if kind == "webhook":
        url = str(cfg.get("url") or "").strip()
        if not re.match(r"^https?://[^\s]+$", url, re.I):
            raise ValueError("The address must start with http:// or https://")
        method = str(cfg.get("method") or "POST").upper()
        if method not in METHODS:
            raise ValueError(f"Method must be one of {', '.join(METHODS)}")
        body = str(cfg.get("body") or "")
        if body and method != "GET":
            try:
                json.loads(_fill(body, "x"))
            except ValueError as exc:
                raise ValueError("The body must be JSON, for example {\"text\": \"{{input}}\"}") from exc
        header = str(cfg.get("secret_header") or "").strip()
        if header and not re.match(r"^[A-Za-z0-9-]{1,60}$", header):
            raise ValueError("Header name: letters, digits and dashes, e.g. Authorization")
        clean = {"url": url, "method": method, "body": body[:4000], "secret_header": header}
    else:
        template = str(cfg.get("template") or "").strip()
        if "{{input}}" not in template:
            raise ValueError("The instruction must contain {{input}} where the text goes")
        clean = {"template": template[:4000]}
    return {"name": name, "kind": kind, "description": str(data.get("description") or "")[:300], "config": clean,
            "enabled": int(bool(data.get("enabled", True)))}


def _out(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["config"] = json.loads(d["config"])
    d["enabled"] = bool(d["enabled"])
    d["has_secret"] = d["kind"] == "webhook" and vault.has_secret(_secret_key(d["name"]))
    return d


def list_all(enabled_only: bool = False) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM custom_tools" + (" WHERE enabled = 1" if enabled_only else "")
                            + " ORDER BY name").fetchall()
    return [_out(r) for r in rows]


def get(tid: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM custom_tools WHERE id = ?", (tid,)).fetchone()
    if not row:
        raise KeyError(f"custom tool {tid}")
    return _out(row)


def find(name: str, kind: str | None = None) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM custom_tools WHERE name = ? COLLATE NOCASE AND enabled = 1", (name.strip(),)).fetchone()
    if not row or (kind and row["kind"] != kind):
        names = ", ".join(t["name"] for t in list_all(True) if not kind or t["kind"] == kind) or "none yet"
        raise ValueError(f"No {kind or 'custom'} tool named {name!r}. Available: {names}")
    return _out(row)


def save(data: dict[str, Any], tid: int | None = None) -> dict[str, Any]:
    f = _clean(data)
    with db.session() as conn:
        if tid is None:
            cur = conn.execute("INSERT INTO custom_tools(name, description, kind, config, enabled, created_at, updated_at) "
                               "VALUES (?,?,?,?,?,?,?)", (f["name"], f["description"], f["kind"], json.dumps(f["config"]),
                                                          f["enabled"], db.now(), db.now()))
            tid = int(cur.lastrowid)
        else:
            old = get(tid)
            if old["name"].lower() != f["name"].lower() and vault.has_secret(_secret_key(old["name"])):
                vault.set_secret(_secret_key(f["name"]), vault.get_secret(_secret_key(old["name"])) or "")
                vault.delete_secret(_secret_key(old["name"]))
            conn.execute("UPDATE custom_tools SET name=?, description=?, kind=?, config=?, enabled=?, updated_at=? WHERE id=?",
                         (f["name"], f["description"], f["kind"], json.dumps(f["config"]), f["enabled"], db.now(), tid))
    if "secret" in data and data["secret"] is not None:
        if data["secret"]:
            vault.set_secret(_secret_key(f["name"]), str(data["secret"]))
        else:
            vault.delete_secret(_secret_key(f["name"]))
    activity.log("plugin", f"Saved custom tool \"{f['name']}\" ({f['kind']})")
    return get(tid)


def delete(tid: int) -> dict[str, Any]:
    t = get(tid)
    vault.delete_secret(_secret_key(t["name"]))
    with db.session() as conn:
        conn.execute("DELETE FROM custom_tools WHERE id = ?", (tid,))
    activity.log("plugin", f"Deleted custom tool \"{t['name']}\"")
    return t


def export(tid: int) -> dict[str, Any]:
    t = get(tid)
    return {"lifeos_tool": 1, "name": t["name"], "kind": t["kind"], "description": t["description"], "config": t["config"]}


def import_tool(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("lifeos_tool") != 1:
        raise ValueError("This isn't a LifeOS tool file")
    return save({k: data.get(k) for k in ("name", "kind", "description", "config")} | {"enabled": True})


# ---------------------------------------------------------------- running

def _fill(template: str, text: str, *, json_string: bool = False, url: bool = False) -> str:
    value = text
    if json_string:
        value = json.dumps(text)[1:-1]
    elif url:
        value = quote(text, safe="")
    return template.replace("{{input}}", value)


def describe_request(t: dict[str, Any], text: str) -> dict[str, Any]:
    c = t["config"]
    req = {"method": c["method"], "url": _fill(c["url"], text, url=True)}
    if c["method"] != "GET" and c["body"]:
        req["body"] = json.loads(_fill(c["body"], text, json_string=True))
    return req


def call_webhook(t: dict[str, Any], text: str) -> dict[str, Any]:
    req = describe_request(t, text)
    headers = {"User-Agent": "AI-LifeOS"}
    secret = vault.get_secret(_secret_key(t["name"])) if t["config"].get("secret_header") else None
    if secret:
        headers[t["config"]["secret_header"]] = secret
    try:
        r = httpx.request(req["method"], req["url"], json=req.get("body"), headers=headers, timeout=30,
                          follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ValueError(f"{t['name']}: request failed ({exc})") from exc
    text_out = r.text[:MAX_RESPONSE]
    activity.log("plugin", f"Called {t['name']} → HTTP {r.status_code}", details={"url": req["url"]},
                 status="ok" if r.is_success else "error")
    if not r.is_success:
        raise ValueError(f"{t['name']} answered HTTP {r.status_code}: {text_out[:300]}")
    return {"status": r.status_code, "response": text_out}


def run_prompt(t: dict[str, Any], text: str) -> str:
    cfg = settings.get_all()
    model = cfg.get("fast_model") or cfg["chat_model"]
    prompt = _fill(t["config"]["template"], text)
    return llm.chat_text([{"role": "user", "content": prompt}], model=model, timeout=300)
