"""Email alerts over SMTP: tell the user when a mission finishes or fails, or when the crew needs them.

Settings live under "email_alerts"; the SMTP password is in the vault. When no SMTP password is stored and the
SMTP username matches the read-only IMAP account, that account's password is used (the same Gmail app password
works for both). Mail is sent on a background thread so a slow server never holds up a mission.
"""
from __future__ import annotations

import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from . import activity, integrations, settings, vault

SECRET = "smtp:password"
EVENTS = ("mission_done", "mission_failed", "approval", "question", "workflow")
# "Needs you" emails (approvals and questions) for one mission are sent at most this often, so a mission that queues
# several approvals in a row sends one email, not five.
NEEDS_YOU_COOLDOWN_S = 600

_lock = threading.Lock()
_last_needs_you: dict[int, float] = {}
_last: dict[str, Any] = {"sent_at": None, "error": None}


def _cfg() -> dict[str, Any]:
    cfg = {**settings.DEFAULTS["email_alerts"], **settings.get("email_alerts")}
    cfg["events"] = {**settings.DEFAULTS["email_alerts"]["events"], **(cfg.get("events") or {})}
    return cfg


def _password(cfg: dict[str, Any]) -> str | None:
    own = vault.get_secret(SECRET)
    if own:
        return own
    imap = settings.get("email")
    if cfg.get("username") and cfg["username"].strip().lower() == (imap.get("username") or "").strip().lower():
        return vault.get_secret(integrations.EMAIL_SECRET)
    return None


def status() -> dict[str, Any]:
    cfg = _cfg()
    own = vault.has_secret(SECRET)
    pw = own or bool(_password(cfg))
    return {**cfg, "password_stored": own, "password_from_imap": pw and not own,
            "configured": bool(cfg["host"] and cfg["username"] and cfg["to"] and pw),
            "last_sent_at": _last["sent_at"], "last_error": _last["error"]}


def configure(changes: dict[str, Any], password: str | None = None) -> dict[str, Any]:
    cfg = _cfg()
    for key in ("enabled", "to", "host", "port", "username", "security"):
        if key in changes and changes[key] is not None:
            cfg[key] = changes[key].strip() if isinstance(changes[key], str) else changes[key]
    if changes.get("events"):
        cfg["events"] = {**cfg["events"], **{k: bool(v) for k, v in changes["events"].items() if k in EVENTS}}
    if cfg["security"] not in ("ssl", "starttls"):
        raise ValueError("security must be ssl or starttls")
    if not 1 <= int(cfg["port"]) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if cfg["to"] and "@" not in cfg["to"]:
        raise ValueError("Send-to address doesn't look like an email address")
    settings.update({"email_alerts": cfg})
    if password is not None:
        if password:
            vault.set_secret(SECRET, password.replace(" ", ""))  # Gmail shows app passwords in groups of four
        else:
            vault.delete_secret(SECRET)
    return status()


def _send_now(subject: str, body: str) -> None:
    cfg = _cfg()
    password = _password(cfg)
    if not (cfg["host"] and cfg["username"] and cfg["to"] and password):
        raise ValueError("Email alerts aren't set up: fill in the server, username, password and send-to address")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"AI LifeOS <{cfg['username']}>" if "@" in cfg["username"] else cfg["username"]
    msg["To"] = cfg["to"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="ai-lifeos.local")
    msg.set_content(body)
    context = ssl.create_default_context()
    port = int(cfg["port"])
    if cfg["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=30)
    try:
        if cfg["security"] == "starttls":
            server.starttls(context=context)
        server.login(cfg["username"], password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _deliver(subject: str, body: str) -> None:
    try:
        _send_now(subject, body)
        _last.update(sent_at=time.strftime("%Y-%m-%dT%H:%M:%S"), error=None)
    except Exception as exc:  # never let an email problem break a mission; record it instead
        _last["error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            activity.log("email", f"Couldn't send email alert \"{subject[:80]}\": {exc}"[:300], status="error")
        except Exception:
            pass


def test() -> dict[str, Any]:
    try:
        _send_now("[LifeOS] Test email", "Email alerts from AI LifeOS work.\n\nYou'll get an email like this when a "
                  "mission finishes or fails, and when an agent needs your approval or an answer.\n")
    except Exception as exc:
        _last["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return {"ok": False, "error": _hint(exc)}
    _last.update(sent_at=time.strftime("%Y-%m-%dT%H:%M:%S"), error=None)
    return {"ok": True, "to": _cfg()["to"]}


def _hint(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("The server rejected the username or password. For Gmail, use an app password "
                "(myaccount.google.com/apppasswords), not your normal password. " + text)[:400]
    if isinstance(exc, (TimeoutError, ConnectionRefusedError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return f"Couldn't reach the mail server ({text}). Check the server and port (Gmail: smtp.gmail.com, 465, SSL)."
    return text[:400]


def notify(event: str, subject: str, body: str, *, mission_id: int | None = None) -> bool:
    """Queue an email for an event if alerts are on and that event is enabled. Returns whether one was queued."""
    try:
        cfg = _cfg()
    except Exception:
        return False
    if not cfg["enabled"] or not cfg["events"].get(event):
        return False
    if event in ("approval", "question") and mission_id is not None:
        with _lock:
            now = time.monotonic()
            if now - _last_needs_you.get(mission_id, -NEEDS_YOU_COOLDOWN_S) < NEEDS_YOU_COOLDOWN_S:
                return False
            _last_needs_you[mission_id] = now
    elif mission_id is not None:
        with _lock:
            _last_needs_you.pop(mission_id, None)
    footer = "\n\n—\nOpen AI LifeOS to review. Turn these emails off in Settings → Notifications → Email alerts.\n"
    threading.Thread(target=_deliver, args=(f"[LifeOS] {subject}"[:200], body.strip() + footer),
                     daemon=True, name="lifeos-mail").start()
    return True
