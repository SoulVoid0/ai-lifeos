"""Email alerts: settings, the test email, and mails for approvals and finished missions."""
from __future__ import annotations

import smtplib
import time

import pytest


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture()
def outbox(monkeypatch):
    """Replace the SMTP connection with one that records what would be sent."""
    from lifeos import mailer

    sent: list[dict] = []
    for items in mailer._emailed.values():
        items.clear()
    mailer._seen_codes.clear()

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.host, self.port, self.tls = host, port, False

        def starttls(self, context=None):
            self.tls = True

        def login(self, user, password):
            if password != "abcdabcdabcdabcd":
                raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
            self.user = user

        def send_message(self, msg):
            sent.append({"host": self.host, "port": self.port, "tls": self.tls, "user": self.user,
                         "to": msg["To"], "subject": msg["Subject"],
                         "body": msg.get_body(("plain",)).get_content(),
                         "html": (h.get_content() if (h := msg.get_body(("html",))) else None)})

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return sent


def test_email_alerts_settings_and_test(client, outbox):
    st = client.get("/api/email-alerts").json()
    assert st["enabled"] is False and st["configured"] is False and st["host"] == "smtp.gmail.com"
    assert client.post("/api/email-alerts/test").json()["ok"] is False
    assert client.put("/api/email-alerts", json={"security": "plain"}).status_code == 400
    assert client.put("/api/email-alerts", json={"to": "not-an-address"}).status_code == 400

    st = client.put("/api/email-alerts", json={"enabled": True, "username": "me@gmail.com", "to": "me@gmail.com",
                                               "password": "wrong"}).json()
    assert st["configured"] and st["password_stored"]
    assert "password" not in st
    bad = client.post("/api/email-alerts/test").json()
    assert bad["ok"] is False and "app password" in bad["error"]

    client.put("/api/email-alerts", json={"password": "abcd abcd abcd abcd"})  # Gmail's grouped display
    assert client.post("/api/email-alerts/test").json() == {"ok": True, "to": "me@gmail.com"}
    assert outbox[-1]["subject"] == "[LifeOS] Test email" and outbox[-1]["port"] == 465

    client.put("/api/email-alerts", json={"security": "starttls", "port": 587, "events": {"approval": False, "bogus": True}})
    st = client.get("/api/email-alerts").json()
    assert st["events"]["approval"] is False and "bogus" not in st["events"]
    client.post("/api/email-alerts/test")
    assert outbox[-1]["tls"] is True and outbox[-1]["port"] == 587


def test_imap_password_is_reused_for_the_same_account(client, outbox):
    client.put("/api/email", json={"host": "imap.gmail.com", "username": "me@gmail.com", "password": "abcdabcdabcdabcd"})
    st = client.put("/api/email-alerts", json={"enabled": True, "username": "me@gmail.com", "to": "me@gmail.com"}).json()
    assert st["configured"] and st["password_from_imap"] and not st["password_stored"]
    assert client.post("/api/email-alerts/test").json()["ok"] is True


@pytest.fixture()
def inbox(monkeypatch):
    """A fake IMAP mailbox; tests drop raw messages into it."""
    import imaplib

    messages: list[bytes] = []

    class FakeIMAP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def login(self, user, password):
            pass

        def select(self, folder, readonly=False):
            return ("OK", [b"1"]) if folder == "INBOX" else ("NO", [b""])

        def search(self, charset, *criteria):
            return "OK", [" ".join(str(i + 1) for i in range(len(messages))).encode()]

        def fetch(self, num, what):
            return "OK", [(num, messages[int(num) - 1])]

        def logout(self):
            pass

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return messages


def _reply(subject: str, sender: str = "me@gmail.com", body: str = "") -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, "me@gmail.com"
    msg.set_content(body or "sent from my phone")
    return msg.as_bytes()


def _setup(client):
    from lifeos import mailer

    mailer.DIGEST_DELAY_S = 0.05
    client.put("/api/email-alerts", json={"enabled": True, "username": "me@gmail.com", "to": "me@gmail.com",
                                          "password": "abcdabcdabcdabcd"})


def test_approve_from_the_email(client, approved, outbox, inbox):
    from lifeos import mailer

    _setup(client)
    assert client.get("/api/email-alerts").json()["buttons_inbox"] == "me@gmail.com"  # Gmail's IMAP, same login
    mid = client.post("/api/missions", json={"goal": "Find all PDFs in my Downloads folder, organize them by category"}).json()["id"]
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["pending_actions"] and r)
    aid = cur["pending_actions"][0]["id"]
    mail = wait_for(lambda: [m for m in outbox if "Approve?" in m["subject"]])[0]
    assert "Approve:" in mail["body"] and "LOS-A" in mail["body"]

    approve = mailer.code("A", aid, "approve")
    forged = approve[:-12] + "0" * 12
    inbox.append(_reply(f"Approve: something [{forged}]"))
    inbox.append(_reply(f"Approve: something [{approve}]", sender="stranger@example.com"))
    assert mailer.poll_replies() == 0
    assert client.get(f"/api/missions/{mid}").json()["pending_actions"]

    inbox.append(_reply(f"Approve: organize [{approve}]"))
    inbox.append(_reply(f"Deny: organize [{mailer.code('A', aid, 'reject')}]"))  # too late: already decided
    assert mailer.poll_replies() == 1
    done = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["status"] == "done" and r)
    assert all(t["status"] == "done" for t in done["tasks"])
    assert (approved["downloads"] / "Finance" / "invoice_march.pdf").exists()
    assert wait_for(lambda: [m for m in outbox if m["subject"].startswith("[LifeOS] Approved:")])
    assert mailer.poll_replies() == 0  # the same email isn't acted on twice


def test_answer_a_question_from_the_email(client, outbox, inbox):
    from lifeos import db, mailer, missions

    _setup(client)
    with db.session() as conn:
        mid = conn.execute("INSERT INTO missions(goal, status, lead, source, created_at) VALUES (?,?,?,?,?)",
                           ("Plan a trip", "waiting", "overseer", "user", db.now())).lastrowid
        b1 = conn.execute("INSERT INTO briefs(mission_id, agent, question, options, created_at) VALUES (?,?,?,?,?)",
                          (mid, "planner", "Which city?", '["Paris", "Rome"]', db.now())).lastrowid
        b2 = conn.execute("INSERT INTO briefs(mission_id, agent, question, options, created_at) VALUES (?,?,?,?,?)",
                          (mid, "planner", "Any budget?", "[]", db.now())).lastrowid
    mailer.needs_you(mid)
    mail = wait_for(lambda: [m for m in outbox if "2 things are waiting" in m["subject"]])[0]
    assert "Rome" in mail["body"] and "Which city?" in mail["body"]

    inbox.append(_reply(f"Answer: Rome [{mailer.code('B', b1, '1')}]"))
    inbox.append(_reply(f"Answer [{mailer.code('B', b2, 'text')}]",
                        body=f"About 800 euros\n\n{mailer.ANSWER_MARK}. (x)\n> quoted"))
    assert mailer.poll_replies() == 2
    briefs = {b["id"]: b for b in missions.open_briefs()}
    assert not briefs
    with db.session() as conn:
        answers = dict(conn.execute("SELECT id, answer FROM briefs").fetchall())
    assert answers == {b1: "Rome", b2: "About 800 euros"}


def test_mission_emails_for_approval_and_completion(client, approved, outbox):
    from lifeos import mailer

    mailer.DIGEST_DELAY_S = 0.05
    client.put("/api/email-alerts", json={"enabled": True, "username": "me@gmail.com", "to": "me@gmail.com",
                                          "password": "abcdabcdabcdabcd"})
    goal = "Find all PDFs in my Downloads folder, organize them by category"
    mid = client.post("/api/missions", json={"goal": goal}).json()["id"]
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["pending_actions"] and r)
    mail = wait_for(lambda: [m for m in outbox if "Approve?" in m["subject"]])
    assert len(mail) == 1 and cur["pending_actions"][0]["summary"] in mail[0]["body"]

    client.post(f"/api/actions/{cur['pending_actions'][0]['id']}/approve", json={})
    wait_for(lambda: client.get(f"/api/missions/{mid}").json()["status"] == "done")
    done = wait_for(lambda: [m for m in outbox if m["subject"].startswith("[LifeOS] Mission complete")])
    assert goal[:40] in done[0]["subject"] and "Full report:" in done[0]["body"]


def test_no_email_when_alerts_are_off(client, approved, outbox):
    client.put("/api/email-alerts", json={"enabled": False, "username": "me@gmail.com", "to": "me@gmail.com",
                                          "password": "abcdabcdabcdabcd"})
    mid = client.post("/api/missions", json={"goal": "Find all PDFs in my Downloads folder, organize them by category"}).json()["id"]
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["pending_actions"] and r)
    client.post(f"/api/actions/{cur['pending_actions'][0]['id']}/approve", json={})
    wait_for(lambda: client.get(f"/api/missions/{mid}").json()["status"] == "done")
    time.sleep(0.3)
    assert outbox == []
