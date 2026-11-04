"""Code-to-app: describe a small tool ("a pomodoro timer", "a unit converter for recipes") and Forge builds it as a
single self-contained HTML file you can open in its own window.

Safety: a mini app is untrusted code. The app shows it in an <iframe sandbox="allow-scripts ..."> without
allow-same-origin (so it can't call the LifeOS API, read the app's storage or reach your files), and the page carries
a CSP with `connect-src 'none'` (no network at all). It runs scripts and forms only. It keeps its own data through a tiny postMessage store the host page provides (window.lifeosStore), kept
per app in LifeOS's database, because the sandbox has no localStorage.
Each change is a new version; the previous one is kept so "undo" is one click.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from . import activity, common, crew, db, llm

BUILDER = "coder"
MAX_HTML = 400_000
# Put in the page itself (a <meta> CSP), on top of the host's <iframe sandbox="allow-scripts ..."> (no same origin).
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
       "media-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'")
CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
ICONS = ["wrench", "timer", "calculator", "list", "note", "chart", "dice", "palette", "music", "clock", "globe", "game"]
META_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "icon": {"type": "string"}},
               "required": ["name", "icon"]}
STORE_JS = """<script>
/* LifeOS: a small key-value store for this app (the sandbox has no localStorage). */
(function () {
  var cache = {}, pending = {}, n = 0, ready;
  function call(op, key, value) {
    return new Promise(function (resolve) { var id = ++n; pending[id] = resolve;
      parent.postMessage({ lifeosStore: true, id: id, op: op, key: key, value: value }, "*"); });
  }
  window.addEventListener("message", function (e) {
    var d = e.data || {}; if (!d.lifeosStoreReply) return;
    if (d.all) cache = d.all; var r = pending[d.id]; if (r) { delete pending[d.id]; r(d.value); }
  });
  ready = call("all").then(function (all) { cache = all || {}; return cache; });
  window.lifeosStore = {
    ready: ready,
    get: function (k, dflt) { return k in cache ? cache[k] : dflt; },
    set: function (k, v) { cache[k] = v; return call("set", k, v); },
    remove: function (k) { delete cache[k]; return call("remove", k); },
    all: function () { return Object.assign({}, cache); }
  };
})();
</script>"""
_busy: set[int] = set()


def _builder_model() -> str:
    try:
        return crew.get(BUILDER)["effective_model"]
    except KeyError:
        return crew.get(crew.OVERSEER)["effective_model"]


def _row(r: Any, html: bool = False) -> dict[str, Any]:
    d = dict(r)
    if not html:
        d.pop("html", None)
    d["busy"] = d["id"] in _busy
    return d


def get(app_id: int, html: bool = False) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM mini_apps WHERE id = ?", (app_id,)).fetchone()
    if not r:
        raise KeyError(f"App {app_id} not found")
    return _row(r, html)


def list_apps() -> list[dict[str, Any]]:
    with db.session() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM mini_apps ORDER BY updated_at DESC").fetchall()]


def _dir(app_id: int):
    return common.feature_dir("mini-apps") / str(app_id)


def delete(app_id: int) -> dict[str, Any]:
    a = get(app_id)
    with db.session() as conn:
        conn.execute("DELETE FROM mini_apps WHERE id = ?", (app_id,))
    import shutil

    shutil.rmtree(_dir(app_id), ignore_errors=True)
    return a


def extract_html(text: str) -> str:
    """The HTML document out of a model reply (fenced or bare)."""
    m = re.search(r"```(?:html)?\s*\n(.*?)```", text, re.S | re.I)
    body = (m.group(1) if m else text).strip()
    start = re.search(r"<!doctype html|<html", body, re.I)
    if start:
        body = body[start.start():]
    end = body.lower().rfind("</html>")
    if end != -1:
        body = body[:end + 7]
    if "<" not in body or not re.search(r"<(html|body|div|script|main|canvas)", body, re.I):
        raise ValueError("The model didn't return an HTML page")
    if len(body) > MAX_HTML:
        raise ValueError("The app is too big")
    return body


def sanitize(html: str) -> str:
    """Drop external resources (they'd be blocked anyway), add the CSP and the store shim."""
    html = re.sub(r"<meta[^>]+http-equiv=[\"']?content-security-policy[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<script[^>]+src=[\"']?https?:[^>]*>\s*</script>", "<!-- external script removed -->", html, flags=re.I)
    html = re.sub(r"<link[^>]+href=[\"']?https?:[^>]*>", "<!-- external stylesheet removed -->", html, flags=re.I)
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + CSP_META + "\n" + STORE_JS, html, count=1, flags=re.I)
    return CSP_META + "\n" + STORE_JS + "\n" + html


BUILD_RULES = ("Build it as ONE complete, self-contained HTML file: inline <style> and <script>, no external files, "
               "libraries, fonts, images or network requests (they're blocked), and no localStorage/sessionStorage/"
               "cookies (blocked too). To save data, use the provided async store: `await lifeosStore.ready;` then "
               "`lifeosStore.get(key, default)` (sync, after ready) and `lifeosStore.set(key, value)` (JSON values). "
               "Make it polished and usable: a clear title, a dark theme (background #0b0d12, text #e5e7eb, accent "
               "#8b5cf6) that fits a modern desktop app, readable 15px+ text, keyboard-friendly, responsive from 360px "
               "wide, sensible empty states, and no placeholder 'TODO' features. Reply with only the HTML in one ```html "
               "block.")


def build(description: str) -> dict[str, Any]:
    description = re.sub(r"\s+", " ", description or "").strip()
    if len(description) < 8:
        raise ValueError("Describe the tool you want in a sentence or two")
    if len(description) > 3000:
        raise ValueError("Keep the description under 3000 characters")
    now = db.now()
    with db.session() as conn:
        cur = conn.execute("INSERT INTO mini_apps(name, description, created_at, updated_at) VALUES (?,?,?,?)",
                           (description[:40], description, now, now))
        app_id = int(cur.lastrowid)
    _spawn(app_id, description, None)
    activity.log("crew", f"Forge is building a mini app: {description[:100]}")
    return get(app_id)


def revise(app_id: int, change: str) -> dict[str, Any]:
    change = re.sub(r"\s+", " ", change or "").strip()
    if len(change) < 3:
        raise ValueError("Say what to change")
    a = get(app_id, html=True)
    if a["id"] in _busy:
        raise ValueError("Forge is still working on this app")
    if not a["html"]:
        raise ValueError("The app hasn't been built yet")
    _spawn(app_id, a["description"], change)
    return get(app_id)


def _spawn(app_id: int, description: str, change: str | None) -> None:
    _busy.add(app_id)
    with db.session() as conn:
        conn.execute("UPDATE mini_apps SET status = 'building', error = NULL WHERE id = ?", (app_id,))
    threading.Thread(target=_work, args=(app_id, description, change), daemon=True, name=f"lifeos-miniapp-{app_id}").start()


def _work(app_id: int, description: str, change: str | None) -> None:
    try:
        model = _builder_model()
        if change:
            current = get(app_id, html=True)["html"]
            prompt = (f"Here is a small single-file web app:\n```html\n{current[:120_000]}\n```\n\nMake this change: {change}\n\n"
                      f"Keep everything else working. {BUILD_RULES}")
        else:
            prompt = f"Build this small desktop tool for the user: {description}\n\n{BUILD_RULES}"
        msgs = [{"role": "system", "content": "You are Forge, an expert front-end engineer. You write complete, working, "
                                              "well-structured code with no omissions."},
                {"role": "user", "content": prompt}]
        with llm.track(None, BUILDER):
            reply = llm.chat_text(msgs, model=model, timeout=1500)
        html = sanitize(extract_html(reply.replace(STORE_JS, "").replace(CSP_META, "")))
        meta = {"name": "", "icon": "wrench"}
        if not change:
            try:
                meta = llm.chat_json([{"role": "user", "content": f"Name this app in 1-3 words, and pick an icon from "
                                       f"{ICONS}: {description[:500]}"}], META_SCHEMA, model=llm.light_model(model), timeout=90)
            except Exception:  # noqa: BLE001 - a default name is fine
                pass
        folder = _dir(app_id)
        folder.mkdir(parents=True, exist_ok=True)
        with db.session() as conn:
            r = conn.execute("SELECT html, version, name, notes FROM mini_apps WHERE id = ?", (app_id,)).fetchone()
            if r["html"]:
                (folder / f"v{r['version']}.html").write_text(r["html"], encoding="utf-8")
            notes = (r["notes"] + "\n" if r["notes"] else "") + (f"v{r['version'] + 1}: {change}" if change else "v1: built")
            name = str(meta.get("name") or "").strip()[:40] or r["name"]
            icon = meta.get("icon") if meta.get("icon") in ICONS else "wrench"
            conn.execute("UPDATE mini_apps SET html = ?, version = version + 1, status = 'ready', notes = ?, updated_at = ?"
                         + (", name = ?, icon = ?" if not change else "") + " WHERE id = ?",
                         (html, notes[-4000:], db.now(), *((name, icon) if not change else ()), app_id))
        common.notify(f"Forge finished {'updating' if change else 'building'} a mini app", name, kind="crew")
    except Exception as exc:  # noqa: BLE001 - shown on the app
        with db.session() as conn:
            conn.execute("UPDATE mini_apps SET status = ?, error = ? WHERE id = ?",
                         ("ready" if get(app_id, html=True)["html"] else "failed", f"{type(exc).__name__}: {exc}"[:400], app_id))
    finally:
        _busy.discard(app_id)


def undo(app_id: int) -> dict[str, Any]:
    a = get(app_id)
    prev = _dir(app_id) / f"v{a['version'] - 1}.html"
    if a["version"] <= 1 or not prev.is_file():
        raise ValueError("There's no earlier version")
    with db.session() as conn:
        conn.execute("UPDATE mini_apps SET html = ?, version = version - 1, updated_at = ? WHERE id = ?",
                     (prev.read_text(encoding="utf-8"), db.now(), app_id))
    prev.unlink(missing_ok=True)
    return get(app_id)


# Per-app data for window.lifeosStore.
def store_get(app_id: int) -> dict[str, Any]:
    p = _dir(app_id) / "store.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def store_set(app_id: int, key: str, value: Any = None, remove: bool = False) -> dict[str, Any]:
    get(app_id)
    data = store_get(app_id)
    if remove:
        data.pop(key, None)
    else:
        data[str(key)[:200]] = value
    raw = json.dumps(data)
    if len(raw) > 2_000_000:
        raise ValueError("This app's saved data is over 2 MB")
    folder = _dir(app_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "store.json").write_text(raw, encoding="utf-8")
    return data
