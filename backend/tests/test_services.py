"""Starting helper programs hidden: ComfyUI through its launcher, and Ollama."""
from __future__ import annotations

import socket
import sys
import time

import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script launcher")
def test_comfy_launcher_starts_and_stops(env, tmp_path):
    from lifeos import media, services

    port = free_port()
    server = tmp_path / "fake_comfy.py"
    server.write_text(
        "import http.server, sys\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def log_message(self, *a): pass\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
        f"http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n")
    launcher = tmp_path / "comfyui-user.sh"
    launcher.write_text(f'exec "{sys.executable}" "{server}"\n')
    with pytest.raises(ValueError):
        media.update_cfg({"comfy_launcher": str(tmp_path / "nope.bat")})
    media.update_cfg({"comfy_url": f"http://127.0.0.1:{port}", "comfy_launcher": str(launcher)})
    assert not services.comfy_up()
    try:
        assert services.ensure_comfy(wait=15)
        st = services.status()["comfyui"]
        assert st["up"] and st["started_by_lifeos"] and st["launcher"] == str(launcher)
        assert services.ensure_comfy(wait=1)  # already running: nothing new is started
    finally:
        services.stop_all()
    deadline = time.monotonic() + 5
    while services.comfy_up() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not services.comfy_up()


def test_comfy_without_launcher_explains(env, monkeypatch):
    from lifeos import media, services

    monkeypatch.setattr(services, "COMFY_CANDIDATES", [])
    media.update_cfg({"comfy_url": f"http://127.0.0.1:{free_port()}"})
    assert services.ensure_comfy(wait=1) is False
    assert "launcher" in services.status()["comfyui"]["note"]
    m = media.generate("image", "a test image")
    assert m["status"] == "failed" and "isn't running" in m["error"]


def test_ollama_auto_start(env, monkeypatch):
    from lifeos import services, settings

    spawned = []
    up = iter([False, True])
    monkeypatch.setattr(services, "ollama_up", lambda: next(up, True))
    monkeypatch.setattr(services, "ollama_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(services, "_spawn", lambda name, args, cwd=None, extra_env=None: spawned.append(args))
    assert services.ensure_ollama(wait=3) and spawned == [["/usr/bin/ollama", "serve"]]
    settings.update({"auto_start_ollama": False})
    monkeypatch.setattr(services, "ollama_up", lambda: False)
    assert services.ensure_ollama(wait=1) is False and len(spawned) == 1
