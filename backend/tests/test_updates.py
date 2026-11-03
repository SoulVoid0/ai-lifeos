"""App updates relayed from GitHub Releases, and importing data written by Microsoft Store Python."""
from __future__ import annotations

import json
import sqlite3

import httpx


def github(assets_ok=True, status=200):
    manifest = {"version": "0.1.7", "notes": "x", "platforms": {"windows-x86_64": {
        "signature": "sig", "url": "https://github.com/o/r/releases/download/v0.1.7/AI.LifeOS_0.1.7_x64-setup.exe"}}}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/repos/o/r/releases/latest":
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, json={"assets": [
                {"id": 11, "name": "latest.json", "url": "https://api.github.com/repos/o/r/releases/assets/11"},
                {"id": 12, "name": "AI.LifeOS_0.1.7_x64-setup.exe", "url": "https://api.github.com/repos/o/r/releases/assets/12"},
            ] if assets_ok else []})
        if req.url.path == "/repos/o/r/releases/assets/11":
            assert req.headers["accept"] == "application/octet-stream"
            return httpx.Response(200, content=json.dumps(manifest).encode())
        return httpx.Response(404)

    seen: list[httpx.Request] = []
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_manifest_points_downloads_at_the_backend(env):
    from lifeos import updates

    updates.set_cfg(repo="https://github.com/o/r", token="tok")
    assert updates.cfg() == {"repo": "o/r", "has_token": True}
    client, seen = github()
    data = updates.manifest(client)
    assert data["version"] == "0.1.7"
    assert data["platforms"]["windows-x86_64"]["url"].endswith("/api/system/update/asset/12")
    assert data["platforms"]["windows-x86_64"]["url"].startswith("http://127.0.0.1:")
    assert all(r.headers["authorization"] == "Bearer tok" for r in seen)

    client, _ = github(assets_ok=False)
    assert updates.manifest(client) is None


def test_private_repo_without_token_explains(env):
    import pytest

    from lifeos import updates

    updates.set_cfg(repo="o/r", token="")
    client, seen = github(status=404)
    with pytest.raises(updates.UpdateError, match="read token"):
        updates.manifest(client)
    assert "authorization" not in seen[0].headers
    with pytest.raises(ValueError):
        updates.set_cfg(repo="not a repo")


def test_update_endpoints(client):
    r = client.patch("/api/system/update/config", json={"repo": "", "token": ""})
    assert r.json() == {"repo": "", "has_token": False}
    r = client.get("/api/system/update/manifest")
    assert r.status_code == 502 and "No update repository" in r.json()["detail"]


def _db(path, models=0, missions=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE agents (slug TEXT, model TEXT)")
    con.execute("CREATE TABLE missions (id INTEGER)")
    con.executemany("INSERT INTO agents VALUES (?, ?)", [(f"a{i}", "qwen3:8b") for i in range(models)] + [("x", "")])
    con.executemany("INSERT INTO missions VALUES (?)", [(i,) for i in range(missions)])
    con.commit()
    con.close()


def test_imports_store_python_data_once(tmp_path, monkeypatch):
    from lifeos import config

    real, store = tmp_path / "real", tmp_path / "store" / "AI-LifeOS"
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(real))
    _db(real / "lifeos.db", models=1)
    _db(store / "lifeos.db", models=10, missions=3)
    (store / "media").mkdir()
    (store / "media" / "a.png").write_bytes(b"png")
    (store / "backend.log").write_text("old")

    assert config.import_store_python_data([store]) == store
    con = sqlite3.connect(real / "lifeos.db")
    assert con.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 3
    con.close()
    assert (real / "media" / "a.png").read_bytes() == b"png"
    assert not (real / "backend.log").exists()
    backups = list(real.glob("backup-before-import-*"))
    assert len(backups) == 1 and (backups[0] / "lifeos.db").is_file()
    # Only once.
    assert config.import_store_python_data([store]) is None


def test_keeps_real_data_when_it_has_more(tmp_path, monkeypatch):
    from lifeos import config

    real, store = tmp_path / "real", tmp_path / "store"
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(real))
    _db(real / "lifeos.db", models=5, missions=9)
    _db(store / "lifeos.db", models=10)
    assert config.import_store_python_data([store]) is None
    assert not list(real.glob("backup-*"))
    # Asked for in Settings: imports on the next start anyway.
    (real / config._REQUEST).write_text("x")
    assert config.import_store_python_data([store]) == store
    assert not (real / config._REQUEST).exists()


def test_model_edits_in_the_app_dont_block_the_import(tmp_path, monkeypatch):
    from lifeos import config

    real, store = tmp_path / "real", tmp_path / "store"
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(real))
    _db(real / "lifeos.db", models=10)
    _db(store / "lifeos.db", models=10)
    assert config.import_store_python_data([store]) == store


def test_ollama_one_model_at_a_time_and_retry(env, monkeypatch):
    import threading
    import time

    from lifeos import llm, settings

    settings.update({"ollama_url": "http://127.0.0.1:11434", "ollama_timeout_s": 900})
    active: list[str] = []
    overlap: list[tuple[str, str]] = []
    calls = {"n": 0}

    unloaded: list[str] = []

    def fake_post(url, json, timeout):
        if url.endswith("/api/generate"):  # switching models unloads the previous one first
            assert json["keep_alive"] == 0
            unloaded.append(json["model"])
            return httpx.Response(200, json={}, request=httpx.Request("POST", url))
        assert timeout.read == 900 and json["keep_alive"] == "30m"
        if json["model"] == "flaky" and calls["n"] == 0:
            calls["n"] += 1
            raise httpx.ReadTimeout("slow")
        for other in active:
            if other != json["model"]:
                overlap.append((other, json["model"]))
        active.append(json["model"])
        time.sleep(0.05)
        active.remove(json["model"])
        return httpx.Response(200, json={"message": {"content": "ok"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    threads = [threading.Thread(target=llm.complete, args=([{"role": "user", "content": "hi"}],),
                                kwargs={"model": m}) for m in ("a", "b", "a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlap == [] and {"a", "b"} & set(unloaded)
    assert llm.complete([{"role": "user", "content": "hi"}], model="flaky") == "ok"


def test_missing_certificate_file_is_repaired(env, monkeypatch, tmp_path):
    """A deleted CA bundle (temp cleanup, or a stale SSL_CERT_FILE) used to break update checks with [Errno 2]."""
    import os

    import httpx

    from lifeos import config

    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "gone.pem"))
    with __import__("pytest").raises(FileNotFoundError):
        httpx.Client()
    pinned = config.pin_ca_bundle()
    assert pinned and os.environ["SSL_CERT_FILE"] == pinned and pinned.startswith(str(config.data_dir()))
    httpx.Client().close()
    assert config.pin_ca_bundle() == pinned  # stable on the next start
