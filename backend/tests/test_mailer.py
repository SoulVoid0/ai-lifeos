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
    sent: list[dict] = []

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
                         "to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()})

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


def test_mission_emails_for_approval_and_completion(client, approved, outbox):
    client.put("/api/email-alerts", json={"enabled": True, "username": "me@gmail.com", "to": "me@gmail.com",
                                          "password": "abcdabcdabcdabcd"})
    goal = "Find all PDFs in my Downloads folder, organize them by category"
    mid = client.post("/api/missions", json={"goal": goal}).json()["id"]
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["pending_actions"] and r)
    mail = wait_for(lambda: [m for m in outbox if "approval" in m["subject"]])
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
