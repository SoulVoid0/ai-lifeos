"""Email alerts over SMTP: tell the user when a mission finishes or fails, or when the crew needs them.

Settings live under "email_alerts"; the SMTP password is in the vault. When no SMTP password is stored and the
SMTP username matches the read-only IMAP account, that account's password is used (the same Gmail app password
works for both). Mail is sent on a background thread so a slow server never holds up a mission.

Approval and question emails carry buttons. A button is a mailto: link that opens a pre-filled email to the user's
own address; when they press Send, the reply poller finds it over IMAP and approves, rejects or answers. Each
subject carries a code signed with a key from the vault, so only a button from a LifeOS email can decide anything,
and the PC never has to be reachable from the phone.
"""
from __future__ import annotations

import email
import email.utils
import hashlib
import hmac
import html
import imaplib
import re
import secrets
import smtplib
import ssl
import threading
import time
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from urllib.parse import quote

from . import activity, actions, crew, db, integrations, settings, vault

SECRET = "smtp:password"
EVENTS = ("mission_done", "mission_failed", "approval", "question", "workflow")
REPLY_KEY = "mailer:reply-key"
# Approvals and questions that arrive within this many seconds of each other go out as one email.
DIGEST_DELAY_S = 20.0
# How often the inbox is checked for button replies while something is waiting on one.
POLL_INTERVAL_S = 30.0
# Subject code: LOS-A<action id>-<approve|reject>-<sig> or LOS-B<brief id>-<option index|text>-<sig>.
CODE_RE = re.compile(r"LOS-([AB])(\d+)-(approve|reject|text|\d{1,2})-([0-9a-f]{12})")
ANSWER_MARK = "Write your answer above this line"

_lock = threading.Lock()
_last: dict[str, Any] = {"sent_at": None, "error": None}
_digests: dict[int, threading.Timer] = {}   # mission id -> pending digest
_emailed: dict[str, set[int]] = {"A": set(), "B": set()}   # items already sent with buttons, still awaiting a reply
_seen_codes: set[str] = set()
_ignored: set[tuple[str, str]] = set()
_poller: dict[str, Any] = {"thread": None, "stop": threading.Event(), "last_error": None, "last_poll": None}


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
            "last_sent_at": _last["sent_at"], "last_error": _last["error"],
            "buttons_inbox": (_inbox_cfg() or {}).get("username"), "replies": poller_status()}


def configure(changes: dict[str, Any], password: str | None = None) -> dict[str, Any]:
    cfg = _cfg()
    for key in ("enabled", "to", "host", "port", "username", "security", "buttons"):
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


def _send_now(subject: str, body: str, html_body: str | None = None) -> None:
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
    if html_body:
        msg.add_alternative(html_body, subtype="html")
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


def _deliver(subject: str, body: str, html_body: str | None = None) -> None:
    try:
        _send_now(subject, body, html_body)
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
    threading.Thread(target=_deliver, args=(f"[LifeOS] {subject}"[:200], body.strip() + FOOTER),
                     daemon=True, name="lifeos-mail").start()
    return True


FOOTER = "\n\n—\nOpen AI LifeOS to review. Turn these emails off in Settings → Notifications → Email alerts.\n"


# ---------------------------------------------------------------- approve and answer from the email

def _key() -> bytes:
    key = vault.get_secret(REPLY_KEY)
    if not key:
        key = secrets.token_hex(32)
        vault.set_secret(REPLY_KEY, key)
    return key.encode()


def _sign(kind: str, item: int, choice: str) -> str:
    return hmac.new(_key(), f"{kind}:{item}:{choice}".encode(), hashlib.sha256).hexdigest()[:12]


def code(kind: str, item: int, choice: str) -> str:
    return f"LOS-{kind}{item}-{choice}-{_sign(kind, item, choice)}"


def _inbox_cfg() -> dict[str, Any] | None:
    """The mailbox the button replies land in: the read-only IMAP account, or the alert account's own inbox when
    its provider is known (same login and app password)."""
    imap = settings.get("email")
    password = vault.get_secret(integrations.EMAIL_SECRET)
    if imap.get("host") and imap.get("username") and password:
        return {"host": imap["host"], "port": int(imap.get("port") or 993), "username": imap["username"],
                "password": password}
    cfg = _cfg()
    host = {"smtp.gmail.com": "imap.gmail.com", "smtp.office365.com": "outlook.office365.com",
            "smtp-mail.outlook.com": "outlook.office365.com"}.get((cfg.get("host") or "").lower())
    password = _password(cfg)
    if host and cfg.get("username") and password:
        return {"host": host, "port": 993, "username": cfg["username"], "password": password}
    return None


def buttons_available() -> bool:
    cfg = _cfg()
    return bool(cfg["enabled"] and cfg.get("buttons", True) and _inbox_cfg())


def _mailto(to: str, subject: str, body: str) -> str:
    return f"mailto:{quote(to, safe='@')}?subject={quote(subject)}&body={quote(body)}"


def _button(href: str, label: str, color: str) -> str:
    return (f'<a href="{html.escape(href)}" style="display:inline-block;margin:4px 8px 4px 0;padding:10px 18px;'
            f'border-radius:8px;background:{color};color:#ffffff;font-weight:600;text-decoration:none;'
            f'font-family:Segoe UI,Arial,sans-serif;font-size:14px">{html.escape(label)}</a>')


def needs_you(mission_id: int) -> None:
    """An approval or question is waiting on a mission: send one email for everything waiting on it, shortly."""
    cfg = _cfg()
    if not cfg["enabled"] or not (cfg["events"].get("approval") or cfg["events"].get("question")):
        return
    with _lock:
        if mission_id in _digests:
            return
        timer = threading.Timer(DIGEST_DELAY_S, _send_digest, args=(mission_id,))
        timer.daemon = True
        _digests[mission_id] = timer
    timer.start()


def _waiting(mission_id: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    with db.session() as conn:
        m = dict(conn.execute("SELECT id, goal FROM missions WHERE id = ?", (mission_id,)).fetchone())
        acts = [dict(r) for r in conn.execute(
            "SELECT id, tool, summary FROM actions WHERE mission_id = ? AND status = 'pending' ORDER BY id", (mission_id,))]
        briefs = db.rows_to_dicts(conn.execute(
            "SELECT id, agent, question, options FROM briefs WHERE mission_id = ? AND status = 'open' ORDER BY id",
            (mission_id,)).fetchall(), ("options",))
    return m, acts, briefs


def _send_digest(mission_id: int) -> None:
    with _lock:
        _digests.pop(mission_id, None)
    try:
        cfg = _cfg()
        m, acts, briefs = _waiting(mission_id)
        acts = [a for a in acts if cfg["events"].get("approval") and a["id"] not in _emailed["A"]]
        briefs = [b for b in briefs if cfg["events"].get("question") and b["id"] not in _emailed["B"]]
        if not acts and not briefs:
            return
        subject, text, html_body = _compose(cfg, m, acts, briefs)
    except Exception as exc:
        _last["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return
    with _lock:
        _emailed["A"].update(a["id"] for a in acts)
        _emailed["B"].update(b["id"] for b in briefs)
    _deliver(subject, text, html_body)
    if html_body:
        start_poller()


def _compose(cfg: dict[str, Any], m: dict[str, Any], acts: list[dict[str, Any]],
             briefs: list[dict[str, Any]]) -> tuple[str, str, str | None]:
    names = {a["slug"]: a["name"] for a in crew.list_agents()}
    n = len(acts) + len(briefs)
    if n == 1 and acts:
        subject = f"Approve? {acts[0]['summary'][:90]}"
    elif n == 1:
        subject = f"{names.get(briefs[0]['agent'], 'An agent')} has a question"
    else:
        subject = f"{n} things are waiting for you"
    inbox = _inbox_cfg() if cfg.get("buttons", True) else None
    to = inbox["username"] if inbox else ""
    goal = m["goal"][:200]
    head = f"Mission {m['id']}: {goal}" if m.get("id") else goal
    text = [head, ""]
    parts = [f'<p style="color:#6b7280;margin:0 0 12px">{html.escape(head)}</p>']
    for a in acts:
        text += [f"Waiting for your approval: {a['summary']}", ""]
        parts.append(f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:0 0 12px">'
                     f'<div style="font-weight:600;margin-bottom:8px">Approve this?</div>'
                     f'<div style="margin-bottom:10px">{html.escape(a["summary"])}</div>')
        if inbox:
            for choice, label, color in (("approve", "Approve", "#16a34a"), ("reject", "Deny", "#dc2626")):
                c = code("A", a["id"], choice)
                parts.append(_button(_mailto(to, f"{label}: {a['summary'][:60]} [{c}]",
                                             f"Press Send to {label.lower()} this. ({c})"), label, color))
                text.append(f"  {label}: send an email to {to} with this subject: {c}")
            text.append("")
        parts.append("</div>")
    for b in briefs:
        who = names.get(b["agent"], b["agent"])
        text += [f"{who} asks: {b['question']}", ""]
        parts.append(f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:0 0 12px">'
                     f'<div style="font-weight:600;margin-bottom:8px">{html.escape(who)} asks</div>'
                     f'<div style="margin-bottom:10px;white-space:pre-wrap">{html.escape(b["question"])}</div>')
        for i, opt in enumerate((b["options"] or [])[:10]):
            if inbox:
                c = code("B", b["id"], str(i))
                parts.append(_button(_mailto(to, f"Answer: {opt[:60]} [{c}]", f"Press Send to answer \"{opt}\". ({c})"),
                                     opt[:60], "#7c3aed"))
                text.append(f"  {opt}: send an email to {to} with this subject: {c}")
            else:
                text.append(f"  - {opt}")
        if inbox:
            c = code("B", b["id"], "text")
            parts.append(_button(_mailto(to, f"Answer [{c}]", f"\n\n{ANSWER_MARK}. ({c})"), "Write an answer", "#4b5563"))
            text.append(f"  Your own answer: send it to {to} with this subject: {c}")
        text.append("")
        parts.append("</div>")
    if inbox:
        note = ("Tapping a button opens a ready-made email to yourself. Press Send and LifeOS picks it up within a "
                "minute while your PC is on.")
        text.append(note)
        parts.append(f'<p style="color:#6b7280;font-size:12px">{note} You can also answer in AI LifeOS. '
                     'Turn these emails off in Settings → Notifications → Email alerts.</p>')
        html_body = ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827;max-width:560px">'
                     + "".join(parts) + "</div>")
    else:
        text.append("Approve or answer in AI LifeOS. The task is paused until you do.")
        html_body = None
    return f"[LifeOS] {subject}"[:200], "\n".join(text).strip() + FOOTER, html_body


def request_approval(action_id: int, heading: str, details: str = "") -> bool:
    """Email one approval that isn't part of a mission (auto-tidy, a self-upgrade), with Approve / Deny buttons."""
    try:
        cfg = _cfg()
    except Exception:
        return False
    if not cfg["enabled"] or not cfg["events"].get("approval"):
        return False
    a = actions.get(action_id)
    if a["status"] != "pending":
        return False
    with _lock:
        if action_id in _emailed["A"]:
            return False
        _emailed["A"].add(action_id)
    subject, text, html_body = _compose(cfg, {"id": None, "goal": heading}, [a], [])
    if details:
        text = text.removesuffix(FOOTER) + "\n\n" + details[:8000] + FOOTER
        if html_body:
            html_body = (html_body.removesuffix("</div>") + '<pre style="white-space:pre-wrap;font-size:12px;'
                         f'background:#f3f4f6;padding:10px;border-radius:8px">{html.escape(details[:8000])}</pre></div>')
    threading.Thread(target=_deliver_and_poll, args=(subject, text, html_body), daemon=True, name="lifeos-mail").start()
    return True


def _deliver_and_poll(subject: str, text: str, html_body: str | None) -> None:
    _deliver(subject, text, html_body)
    if html_body:
        start_poller()


# ---------------------------------------------------------------- the reply poller

def _outstanding() -> bool:
    with _lock:
        a, b = set(_emailed["A"]), set(_emailed["B"])
    if not a and not b:
        return False
    with db.session() as conn:
        live_a = {r[0] for r in conn.execute(f"SELECT id FROM actions WHERE status = 'pending' AND id IN "
                                              f"({','.join('?' * len(a))})", tuple(a))} if a else set()
        live_b = {r[0] for r in conn.execute(f"SELECT id FROM briefs WHERE status = 'open' AND id IN "
                                              f"({','.join('?' * len(b))})", tuple(b))} if b else set()
    with _lock:
        _emailed["A"] &= live_a | (_emailed["A"] - a)
        _emailed["B"] &= live_b | (_emailed["B"] - b)
    return bool(live_a or live_b)


def start_poller() -> None:
    with _lock:
        t = _poller["thread"]
        if t is not None and t.is_alive():
            return
        _poller["stop"] = threading.Event()
        t = threading.Thread(target=_poll_loop, args=(_poller["stop"],), daemon=True, name="lifeos-mail-replies")
        _poller["thread"] = t
    t.start()


def stop_poller() -> None:
    _poller["stop"].set()


def _poll_loop(stop: threading.Event) -> None:
    while not stop.wait(POLL_INTERVAL_S):
        try:
            if not _outstanding():
                return  # nothing is waiting on a reply; the next button email starts the poller again
            poll_replies()
        except Exception as exc:
            _poller["last_error"] = f"{type(exc).__name__}: {exc}"[:300]


def _body(msg: email.message.Message) -> str:
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "text/plain":
            try:
                return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
            except LookupError:
                continue
    return ""


def _answer_text(body: str) -> str:
    text = body.split(ANSWER_MARK)[0]
    lines = [ln for ln in text.splitlines() if not ln.startswith(">")]
    text = "\n".join(lines)
    text = re.split(r"\n\s*On .{0,200}wrote:\s*$", text, flags=re.M)[0]  # a quoted reply, if the client added one
    return text.strip()


def poll_replies() -> int:
    """Check the inbox for button emails and act on them. Returns how many decisions were made."""
    inbox = _inbox_cfg()
    if not inbox:
        return 0
    allowed = {a.strip().lower() for a in (_cfg().get("to") or "").split(",") if a.strip()} | {inbox["username"].lower()}
    since = (date.today() - timedelta(days=2)).strftime("%d-%b-%Y")
    folders = ["INBOX"] + (['"[Gmail]/Sent Mail"'] if "gmail" in inbox["host"] else [])
    found: list[tuple[str, str, str]] = []
    conn = imaplib.IMAP4_SSL(inbox["host"], inbox["port"], timeout=30)
    try:
        conn.login(inbox["username"], inbox["password"])
        for folder in folders:
            typ, _ = conn.select(folder, readonly=True)
            if typ != "OK":
                continue
            typ, data = conn.search(None, "SINCE", since, "SUBJECT", "LOS")
            if typ != "OK":
                continue
            for num in data[0].split()[-50:]:
                typ, msg_data = conn.fetch(num, "(BODY.PEEK[]<0.20000>)")
                raw = next((p[1] for p in msg_data if isinstance(p, tuple)), b"")
                msg = email.message_from_bytes(raw)
                subject = integrations._decode(msg.get("Subject"))
                sender = email.utils.parseaddr(msg.get("From") or "")[1].lower()
                found.append((subject, sender, _body(msg)))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    _poller["last_poll"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    done = 0
    for subject, sender, body in found:
        mt = CODE_RE.search(subject)
        if not mt or mt.group(0) in _seen_codes:
            continue
        kind, item, choice, sig = mt.group(1), int(mt.group(2)), mt.group(3), mt.group(4)
        if not hmac.compare_digest(sig, _sign(kind, item, choice)) or sender not in allowed:
            if (mt.group(0), sender) not in _ignored:
                _ignored.add((mt.group(0), sender))
                activity.log("email", f"Ignored an email decision that wasn't from a LifeOS button ({sender})",
                             status="error")
            continue
        _seen_codes.add(mt.group(0))
        try:
            done += _apply(kind, item, choice, body)
        except (KeyError, ValueError) as exc:  # already decided in the app, or gone
            activity.log("email", f"Email decision skipped: {exc}"[:300])
    return done


def _apply(kind: str, item: int, choice: str, body: str) -> int:
    from . import missions

    if kind == "A":
        current = actions.get(item)
        if current["status"] != "pending":
            return 0
        out = actions.approve(item) if choice == "approve" else actions.reject(item)
        with _lock:
            _emailed["A"].discard(item)
        verb = "Approved" if choice == "approve" else "Denied"
        activity.log("decision", f"{verb} by email: {out['summary']}"[:300], tool=out["tool"], action_id=item)
        _confirm(f"{verb}: {out['summary'][:90]}", f"{verb} from your email: {out['summary']}\n\nResult: {out['status']}"
                 + (f" ({out.get('error')})" if out.get("error") else ""))
        return 1
    with db.session() as conn:
        row = conn.execute("SELECT options, status FROM briefs WHERE id = ?", (item,)).fetchone()
    if not row or row["status"] != "open":
        return 0
    if choice == "text":
        answer = _answer_text(body)
        if not answer:
            raise ValueError(f"The emailed answer to question {item} was empty")
    else:
        import json

        opts = json.loads(row["options"] or "[]")
        if int(choice) >= len(opts):
            raise ValueError(f"Question {item} has no option {choice}")
        answer = opts[int(choice)]
    missions.answer_brief(item, answer)
    with _lock:
        _emailed["B"].discard(item)
    activity.log("decision", f"Answered by email: {answer[:200]}")
    _confirm(f"Answered: {answer[:90]}", f"Your answer was passed on: {answer}")
    return 1


def _confirm(subject: str, body: str) -> None:
    threading.Thread(target=_deliver, args=(f"[LifeOS] {subject}"[:200], body + FOOTER), daemon=True,
                     name="lifeos-mail").start()


def poller_status() -> dict[str, Any]:
    t = _poller["thread"]
    return {"running": bool(t and t.is_alive()), "last_poll": _poller["last_poll"], "last_error": _poller["last_error"],
            "inbox": (_inbox_cfg() or {}).get("username")}
