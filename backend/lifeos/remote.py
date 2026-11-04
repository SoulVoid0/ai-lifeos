"""Phone remote: a web page on your phone to start missions, watch progress and approve things, over your Wi-Fi.

The main LifeOS API only listens on 127.0.0.1. The remote is a second, much smaller server on your local network
(port 8766 by default, off until you turn it on). It can only do what the page needs: see the crew and missions,
start and stop missions, approve or deny waiting actions, answer agents' questions, chat with an agent, toggle
game mode and ask about the PC's screen. It can't read or change files.

Pairing: the app shows a QR code (and a 6-digit code that changes every 10 minutes). Scanning it opens the page and
pairs the phone, which then keeps a random device key (stored on the PC only as a hash; remove a phone in Settings
to log it out). Wrong codes are rate limited, and only devices on private network addresses are answered, so
the remote stays unreachable from the internet even if a router forwards the port.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import socket
import threading
import time
from typing import Any

import psutil
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__, activity, actions, common, crew, db, missions

log = logging.getLogger("lifeos.remote")
PIN_TTL_S = 600
MAX_FAILS = 5
LOCK_S = 600

_pin: dict[str, Any] = {"code": "", "at": 0.0}
_fails: dict[str, list[float]] = {}
_server: dict[str, Any] = {"server": None, "thread": None, "error": "", "port": None}
_lock = threading.Lock()


def cfg() -> dict[str, Any]:
    return common.group("remote")


def configure(changes: dict[str, Any]) -> dict[str, Any]:
    def check(c: dict[str, Any]) -> None:
        if not 1024 <= c["port"] <= 65535 or c["port"] == 8765:
            raise ValueError("Pick a port between 1024 and 65535 (not 8765, the app's own)")

    changes.pop("devices", None)
    out = common.save_group("remote", changes, check)
    sync()
    return out


# ---------------------------------------------------------------- pairing

def pin() -> str:
    if not _pin["code"] or time.time() - _pin["at"] > PIN_TTL_S:
        _pin.update(code=f"{secrets.randbelow(1_000_000):06d}", at=time.time())
    return _pin["code"]


def new_pin() -> str:
    _pin.update(code="", at=0.0)
    return pin()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _devices() -> list[dict[str, Any]]:
    return list(cfg()["devices"])


def _save_devices(devices: list[dict[str, Any]]) -> None:
    common.save_group("remote", {"devices": devices[-20:]})


def pair(code: str, name: str, ip: str) -> dict[str, Any]:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_S]
    _fails[ip] = recent
    if len(recent) >= MAX_FAILS:
        raise PermissionError("Too many wrong codes. Wait 10 minutes, or scan the QR code in the app again.")
    if not code or not secrets.compare_digest(code.strip(), pin()):
        recent.append(now)
        if sum(len(v) for v in _fails.values()) > 4 * MAX_FAILS:
            new_pin()  # someone is guessing: the code changes
        raise PermissionError("That code is wrong or has expired. Check the code in LifeOS → Settings → Phone remote.")
    token = secrets.token_urlsafe(32)
    device = {"id": secrets.token_hex(4), "name": (name or "Phone").strip()[:40] or "Phone", "hash": _hash(token),
              "created_at": db.now(), "last_seen": db.now(), "ip": ip}
    _save_devices(_devices() + [device])
    new_pin()  # one code, one phone
    activity.log("permission", f"Phone remote: paired {device['name']} ({ip})")
    common.notify("A phone was paired with the remote", f"{device['name']} ({ip}). Remove it in Settings → Phone remote.")
    return {"token": token, "device": {k: v for k, v in device.items() if k != "hash"}}


def remove_device(device_id: str) -> list[dict[str, Any]]:
    devices = [d for d in _devices() if d["id"] != device_id]
    _save_devices(devices)
    return public_devices()


def public_devices() -> list[dict[str, Any]]:
    return [{k: v for k, v in d.items() if k != "hash"} for d in _devices()]


_seen_write: dict[str, float] = {}


def _device_for(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    h = _hash(token)
    devices = _devices()
    for d in devices:
        if secrets.compare_digest(d["hash"], h):
            if time.time() - _seen_write.get(d["id"], 0) > 300:  # don't write settings on every poll
                _seen_write[d["id"]] = time.time()
                d["last_seen"] = db.now()
                _save_devices(devices)
            return d
    return None


# ---------------------------------------------------------------- addresses

def lan_addresses() -> list[str]:
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; this picks the interface with the default route
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET:
                try:
                    ip = ipaddress.ip_address(a.address)
                except ValueError:
                    continue
                if ip.is_private and not ip.is_loopback and not str(ip).startswith("169.254."):
                    out.append(str(ip))
    return list(dict.fromkeys(out))


def _allowed_client(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def urls() -> list[str]:
    port = cfg()["port"]
    return [f"http://{ip}:{port}/" for ip in lan_addresses()]


def qr_svg(data: str) -> str:
    import io

    import segno

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111827", light="#ffffff")
    return buf.getvalue().decode()


def status() -> dict[str, Any]:
    c = cfg()
    running = bool(_server["thread"] and _server["thread"].is_alive())
    links = urls()
    pair_url = f"{links[0]}#pair={pin()}" if links else None
    return {"enabled": c["enabled"], "running": running, "port": c["port"], "urls": links, "pin": pin() if c["enabled"] else None,
            "pin_expires_in": int(PIN_TTL_S - (time.time() - _pin["at"])) if c["enabled"] else None,
            "pair_url": pair_url if c["enabled"] else None, "devices": public_devices(), "error": _server["error"]}


# ---------------------------------------------------------------- the server

def sync() -> None:
    want = cfg()["enabled"]
    running = bool(_server["thread"] and _server["thread"].is_alive())
    if want and running and _server["port"] != cfg()["port"]:
        stop()
        running = False
    if want and not running:
        start()
    elif not want and running:
        stop()


def start() -> None:
    import uvicorn

    with _lock:
        port = cfg()["port"]
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        _server.update(server=server, port=port, error="")

        def run() -> None:
            try:
                server.run()
            except (OSError, SystemExit) as exc:
                _server["error"] = f"Couldn't open port {port}: {exc}"
            if not server.started and not _server["error"]:
                _server["error"] = f"Couldn't open port {port}; another program may use it"

        t = threading.Thread(target=run, daemon=True, name="lifeos-remote")
        _server["thread"] = t
        t.start()
    activity.log("system", f"Phone remote on, port {port}")


def stop() -> None:
    server = _server.get("server")
    if server is not None:
        server.should_exit = True
    t = _server.get("thread")
    if t is not None:
        t.join(timeout=5)
    _server.update(server=None, thread=None)


def open_firewall() -> dict[str, Any]:
    """Windows: allow the remote's port on private networks (asks for administrator permission once)."""
    import os
    import subprocess

    port = cfg()["port"]
    if os.name != "nt":
        return {"ok": True, "note": "No firewall change is needed on this system"}
    rule = (f"netsh advfirewall firewall delete rule name=\"AI LifeOS phone remote\" >nul & "
            f"netsh advfirewall firewall add rule name=\"AI LifeOS phone remote\" dir=in action=allow protocol=TCP "
            f"localport={port} profile=private")
    ps = f"Start-Process -FilePath cmd.exe -ArgumentList '/c {rule}' -Verb RunAs -WindowStyle Hidden -Wait"
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120,
                          **common.hidden())
    ok = proc.returncode == 0
    return {"ok": ok, "note": "Allowed on private (home) networks" if ok else
            (proc.stderr.strip()[:300] or "Windows didn't allow the change")}


# ---------------------------------------------------------------- the remote's own API

app = FastAPI(title="AI LifeOS remote", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next: Any) -> Any:
    if not _allowed_client(request.client.host if request.client else None):
        return JSONResponse({"detail": "The remote only answers devices on your home network"}, status_code=403)
    path = request.url.path
    if path.startswith("/r/") and path != "/r/pair":
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        device = _device_for(token)
        if not device:
            return JSONResponse({"detail": "Not paired"}, status_code=401)
        request.state.device = device
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(KeyError)
async def _nf(_: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=404)


@app.exception_handler(ValueError)
async def _bad(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(PermissionError)
async def _denied(_: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=403)


class PairIn(BaseModel):
    code: str = Field(..., max_length=12)
    name: str = Field("", max_length=60)


class GoalIn(BaseModel):
    goal: str = Field(..., min_length=1, max_length=4000)
    agent: str | None = None


class TextIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict[str, str]] = []


class OnIn(BaseModel):
    on: bool | None = None


@app.get("/", response_class=HTMLResponse)
def page() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; "
                                       "style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"})


@app.get("/manifest.webmanifest")
def manifest() -> Response:
    import json

    body = {"name": "AI LifeOS", "short_name": "LifeOS", "start_url": "/", "display": "standalone",
            "background_color": "#0b0d12", "theme_color": "#0b0d12"}
    return Response(json.dumps(body), media_type="application/manifest+json")


@app.post("/r/pair")
def r_pair(body: PairIn, request: Request) -> dict[str, Any]:
    return pair(body.code, body.name, request.client.host if request.client else "?")


def _brief_state() -> dict[str, Any]:
    from . import gamemode

    agents = missions.crew_status()
    active = missions.list_missions(20, "active")
    recent = [m for m in missions.list_missions(8) if m["status"] not in missions.ACTIVE]
    for m in active:
        try:
            full = missions.get(m["id"])
            tasks = full.get("tasks") or []
            m["tasks"] = [{"title": t["title"], "agent": t["agent"], "status": t["status"]} for t in tasks]
            m["progress"] = round(sum(1 for t in tasks if t["status"] == "done") / len(tasks), 2) if tasks else 0
        except KeyError:
            pass
    pending = [{"id": a["id"], "summary": a["summary"], "tool": a["tool"], "created_at": a["created_at"]}
               for a in actions.list_actions("pending", 30)]
    briefs = [{"id": b["id"], "agent": b["agent"], "question": b["question"], "options": b["options"], "goal": b["goal"]}
              for b in missions.open_briefs()]
    events = missions.events(0, None, 25)
    return {"agents": [{"slug": a["slug"], "name": a["name"], "role": a.get("role"), "state": a.get("state"),
                        "color": a.get("color"), "current": a.get("current")} for a in agents],
            "active": active, "recent": recent, "approvals": pending, "questions": briefs,
            "events": [{"id": e["id"], "agent": e["agent"], "kind": e["kind"], "summary": e["summary"],
                        "at": e["created_at"], "mission_id": e["mission_id"]} for e in events[-25:]],
            "game_mode": gamemode.status(), "version": __version__}


@app.get("/r/state")
def r_state() -> dict[str, Any]:
    return _brief_state()


@app.post("/r/missions")
def r_start(body: GoalIn, request: Request) -> dict[str, Any]:
    m = missions.start(body.goal, agent=body.agent or None, source="phone")
    activity.log("crew", f"Mission {m['id']} started from {request.state.device['name']}")
    return m


@app.get("/r/missions/{mid}")
def r_mission(mid: int) -> dict[str, Any]:
    return missions.get(mid)


@app.post("/r/missions/{mid}/cancel")
def r_cancel(mid: int) -> dict[str, Any]:
    return missions.cancel(mid)


@app.post("/r/stop-all")
def r_stop_all() -> dict[str, Any]:
    return {"stopped": missions.stop_all()}


@app.post("/r/actions/{aid}/{decision}")
def r_decide(aid: int, decision: str, request: Request) -> dict[str, Any]:
    if decision not in ("approve", "reject"):
        raise KeyError("No such decision")
    out = actions.approve(aid) if decision == "approve" else actions.reject(aid)
    activity.log("decision", f"{'Approved' if decision == 'approve' else 'Denied'} from "
                 f"{request.state.device['name']}: {out['summary']}"[:300], tool=out["tool"], action_id=aid)
    return {"id": out["id"], "status": out["status"], "summary": out["summary"], "error": out.get("error")}


@app.post("/r/briefs/{bid}/answer")
def r_answer(bid: int, body: TextIn) -> dict[str, Any]:
    return missions.answer_brief(bid, body.text)


@app.post("/r/agents/{slug}/chat")
def r_chat(slug: str, body: ChatIn) -> dict[str, Any]:
    crew.get(slug)
    return missions.talk(slug, body.message, body.history[-10:])


@app.post("/r/game-mode")
def r_game(body: OnIn) -> dict[str, Any]:
    from . import gamemode

    return gamemode.set_manual(body.on)


@app.post("/r/screen")
def r_screen(body: TextIn) -> dict[str, Any]:
    from . import screen

    out = screen.ask(body.text, fresh=True)
    return {"answer": out["answer"], "model": out["model"], "seconds": out["seconds"]}


@app.post("/r/say")
def r_say(body: TextIn) -> dict[str, Any]:
    from . import voice

    return voice.handle(body.text, speak=False)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0d12"><link rel="manifest" href="/manifest.webmanifest">
<title>LifeOS remote</title>
<style>
:root{--bg:#0b0d12;--card:#141821;--line:#232937;--text:#e6e8ee;--muted:#8b93a7;--accent:#8b5cf6;--ok:#10b981;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:14px 16px 8px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
header b{font-size:17px}header .dot{width:9px;height:9px;border-radius:50%;background:var(--ok)}header .grow{flex:1}
nav{display:flex;gap:6px;padding:8px 16px;overflow-x:auto;border-bottom:1px solid var(--line)}
nav button{flex:none;background:var(--card);border:1px solid var(--line);color:var(--muted);border-radius:999px;padding:7px 14px;font:inherit}
nav button.on{color:#fff;border-color:var(--accent);background:#2a2142}
main{padding:12px 16px 90px;max-width:720px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:0 0 10px}
.row{display:flex;gap:8px;align-items:center}.grow{flex:1;min-width:0}.muted{color:var(--muted);font-size:13px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:1px 8px;font-size:12px;color:var(--muted)}
.btn{background:var(--accent);color:#fff;border:0;border-radius:10px;padding:10px 14px;font:inherit;font-weight:600}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}.btn.ok{background:var(--ok)}.btn.bad{background:var(--bad)}
textarea,input,select{width:100%;background:#0f131b;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px;font:inherit}
textarea{min-height:84px}.bar{height:6px;background:#0f131b;border-radius:9px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:var(--accent)}
.ag{width:10px;height:10px;border-radius:50%;flex:none}.ev{font-size:13px;padding:6px 0;border-top:1px solid var(--line)}
.badge{background:var(--bad);color:#fff;border-radius:999px;font-size:11px;padding:0 6px;margin-left:4px}
.msg{padding:8px 10px;border-radius:10px;margin:6px 0;white-space:pre-wrap}.me{background:#2a2142;margin-left:30px}.them{background:#0f131b;margin-right:30px}
#toast{position:fixed;left:16px;right:16px;bottom:18px;background:#1f2533;border:1px solid var(--line);border-radius:12px;padding:12px;display:none}
h3{margin:14px 0 8px;font-size:14px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
</style></head><body>
<div id="pairing" style="display:none;padding:28px 18px;max-width:420px;margin:0 auto">
  <h2>Pair this phone with AI LifeOS</h2>
  <p class="muted">Open LifeOS on your PC → Settings → Phone remote, and type the 6-digit code (or scan the QR code).</p>
  <input id="code" inputmode="numeric" maxlength="6" placeholder="6-digit code" style="font-size:22px;letter-spacing:6px;text-align:center">
  <input id="dname" placeholder="Name for this phone" style="margin-top:8px">
  <button class="btn" style="width:100%;margin-top:10px" onclick="doPair()">Pair</button>
  <p id="perr" style="color:var(--bad)"></p>
</div>
<div id="app" style="display:none">
<header><span class="dot" id="dot"></span><b>AI LifeOS</b><span class="grow"></span><span class="chip" id="gm"></span></header>
<nav id="tabs"></nav>
<main id="view"></main>
</div>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token=localStorage.getItem("lifeos.remote.token")||"";let tab=localStorage.getItem("lifeos.remote.tab")||"crew";let S=null;let chat={agent:"overseer",log:[]};
const COLORS={violet:"#8b5cf6",amber:"#f59e0b",cyan:"#06b6d4",emerald:"#10b981",rose:"#f43f5e",blue:"#3b82f6",orange:"#f97316",pink:"#ec4899",lime:"#84cc16",teal:"#14b8a6",slate:"#64748b"};
function toast(t){const e=$("#toast");e.textContent=t;e.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display="none",3500)}
async function api(m,p,b){const r=await fetch(p,{method:m,headers:{"Content-Type":"application/json","Authorization":"Bearer "+token},body:b===undefined?undefined:JSON.stringify(b)});
 const d=await r.json().catch(()=>({}));if(r.status===401){token="";localStorage.removeItem("lifeos.remote.token");showPair();throw new Error("Not paired")}
 if(!r.ok)throw new Error(d.detail||r.statusText);return d}
function showPair(){$("#app").style.display="none";$("#pairing").style.display="block";const m=location.hash.match(/pair=(\d{6})/);if(m){$("#code").value=m[1]}}
async function doPair(){try{const r=await fetch("/r/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:$("#code").value,name:$("#dname").value||navigator.platform||"Phone"})});
 const d=await r.json();if(!r.ok)throw new Error(d.detail);token=d.token;localStorage.setItem("lifeos.remote.token",token);history.replaceState(null,"","/");start()}catch(e){$("#perr").textContent=e.message}}
const TABS=[["crew","Crew"],["missions","Missions"],["inbox","Needs you"],["chat","Chat"],["pc","PC"]];
function tabs(){const n=(S?.approvals.length||0)+(S?.questions.length||0);$("#tabs").innerHTML=TABS.map(([k,l])=>`<button class="${k===tab?"on":""}" onclick="go('${k}')">${l}${k==="inbox"&&n?`<span class="badge">${n}</span>`:""}</button>`).join("")}
function go(k){tab=k;localStorage.setItem("lifeos.remote.tab",k);render()}
function agentName(s){return S?.agents.find(a=>a.slug===s)?.name||s||""}
function render(){tabs();if(!S)return;const v=$("#view");$("#gm").textContent=S.game_mode.active?"🎮 "+(S.game_mode.game||"Game mode"):"v"+S.version;
 if(tab==="crew"){v.innerHTML=S.agents.map(a=>`<div class="card row"><span class="ag" style="background:${COLORS[a.color]||"#888"}"></span><div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.role||"")}</span><div class="muted">${a.current?esc(a.state+": "+a.current.title):"idle"}</div></div></div>`).join("")+
  `<h3>Live</h3><div class="card">${S.events.slice().reverse().map(e=>`<div class="ev"><b>${esc(agentName(e.agent))}</b> ${esc(e.summary)}</div>`).join("")||'<span class="muted">Nothing yet</span>'}</div>`}
 else if(tab==="missions"){v.innerHTML=`<div class="card"><textarea id="goal" placeholder="Give the crew a goal…"></textarea><div class="row" style="margin-top:8px"><select id="who" class="grow"><option value="">Whole crew (Atlas plans)</option>${S.agents.filter(a=>a.slug!=="overseer").map(a=>`<option value="${a.slug}">${esc(a.name)} only</option>`).join("")}</select><button class="btn" onclick="startMission()">Start</button></div></div>`+
  `<h3>Running</h3>`+(S.active.map(m=>`<div class="card"><div class="row"><div class="grow"><b>#${m.id}</b> ${esc(m.goal.slice(0,160))}</div><button class="btn bad" onclick="cancelM(${m.id})">Stop</button></div><div class="muted">${esc(m.status)}</div><div class="bar"><i style="width:${Math.round((m.progress||0)*100)}%"></i></div>${(m.tasks||[]).map(t=>`<div class="muted">• ${esc(agentName(t.agent))}: ${esc(t.title)} <span class="chip">${esc(t.status)}</span></div>`).join("")}</div>`).join("")||'<div class="muted">Nothing running.</div>')+
  `<h3>Recent</h3>`+S.recent.map(m=>`<div class="card"><b>#${m.id}</b> ${esc(m.goal.slice(0,140))} <span class="chip">${esc(m.status)}</span>${m.result?`<div class="muted" style="margin-top:6px">${esc(m.result.slice(0,400))}</div>`:""}</div>`).join("")}
 else if(tab==="inbox"){v.innerHTML=(S.approvals.map(a=>`<div class="card"><div>${esc(a.summary)}</div><div class="muted">${esc(a.tool)}</div><div class="row" style="margin-top:8px"><button class="btn ok grow" onclick="decide(${a.id},'approve')">Approve</button><button class="btn bad grow" onclick="decide(${a.id},'reject')">Deny</button></div></div>`).join("")+
  S.questions.map(b=>`<div class="card"><b>${esc(agentName(b.agent))} asks</b><div style="margin:6px 0">${esc(b.question)}</div>${(b.options||[]).map((o,i)=>`<button class="btn ghost" style="margin:3px" onclick="answer(${b.id},${esc(JSON.stringify(o))})">${esc(o)}</button>`).join("")}<div class="row" style="margin-top:6px"><input id="ans${b.id}" placeholder="Your answer"><button class="btn" onclick="answer(${b.id},$('#ans${b.id}').value)">Send</button></div></div>`).join(""))||'<div class="muted">Nothing is waiting for you. 🎉</div>'}
 else if(tab==="chat"){v.innerHTML=`<div class="card"><select id="cagent" onchange="chat.agent=this.value;chat.log=[];render()">${S.agents.map(a=>`<option value="${a.slug}" ${a.slug===chat.agent?"selected":""}>${esc(a.name)}</option>`).join("")}</select><div id="clog">${chat.log.map(m=>`<div class="msg ${m.role==="user"?"me":"them"}">${esc(m.content)}</div>`).join("")}</div><div class="row"><input id="cmsg" placeholder="Say something…" onkeydown="if(event.key==='Enter')sendChat()"><button class="btn" onclick="sendChat()">Send</button></div></div>`}
 else if(tab==="pc"){v.innerHTML=`<div class="card"><b>Game mode</b><div class="muted">${S.game_mode.active?"On: "+esc(S.game_mode.game||"")+". The crew is paused and the GPU is free.":"Off. Turns on by itself when a game starts."}</div><div class="row" style="margin-top:8px"><button class="btn grow" onclick="game(true)">On</button><button class="btn ghost grow" onclick="game(false)">Off</button><button class="btn ghost grow" onclick="game(null)">Auto</button></div></div>`+
  `<div class="card"><b>Ask about the PC's screen</b><input id="sq" placeholder="What's this error?" style="margin-top:6px"><button class="btn" style="margin-top:8px" onclick="askScreen()">Look</button><div id="sans" class="muted" style="margin-top:8px;white-space:pre-wrap"></div></div>`+
  `<div class="card"><b>Tell LifeOS</b><input id="cmd" placeholder="e.g. ask Chrono to plan my week" style="margin-top:6px"><button class="btn" style="margin-top:8px" onclick="sayCmd()">Send</button><div id="cans" class="muted" style="margin-top:8px;white-space:pre-wrap"></div></div>`+
  `<div class="card"><button class="btn bad" style="width:100%" onclick="stopAll()">Stop all missions</button></div>`}}
async function refresh(){try{S=await api("GET","/r/state");$("#dot").style.background="var(--ok)";if(!["chat","pc"].includes(tab)||!document.activeElement||document.activeElement===document.body)render();else tabs()}catch(e){$("#dot").style.background="var(--bad)"}}
async function startMission(){const g=$("#goal").value.trim();if(!g)return;try{const m=await api("POST","/r/missions",{goal:g,agent:$("#who").value||null});toast("Mission #"+m.id+" started");refresh()}catch(e){toast(e.message)}}
async function cancelM(id){await api("POST",`/r/missions/${id}/cancel`).catch(e=>toast(e.message));refresh()}
async function stopAll(){const r=await api("POST","/r/stop-all").catch(e=>toast(e.message));if(r)toast("Stopped "+r.stopped);refresh()}
async function decide(id,d){try{const r=await api("POST",`/r/actions/${id}/${d}`);toast((d==="approve"?"Approved: ":"Denied: ")+r.summary)}catch(e){toast(e.message)}refresh()}
async function answer(id,t){if(!t)return;try{await api("POST",`/r/briefs/${id}/answer`,{text:t});toast("Answer sent")}catch(e){toast(e.message)}refresh()}
async function sendChat(){const t=$("#cmsg").value.trim();if(!t)return;chat.log.push({role:"user",content:t});render();try{const r=await api("POST",`/r/agents/${chat.agent}/chat`,{message:t,history:chat.log.slice(-10,-1)});chat.log.push({role:"assistant",content:r.reply})}catch(e){chat.log.push({role:"assistant",content:"("+e.message+")"})}render()}
async function game(on){await api("POST","/r/game-mode",{on}).catch(e=>toast(e.message));refresh()}
async function askScreen(){$("#sans").textContent="Looking…";try{const r=await api("POST","/r/screen",{text:$("#sq").value||"What's on the screen? Explain any error."});$("#sans").textContent=r.answer}catch(e){$("#sans").textContent=e.message}}
async function sayCmd(){const t=$("#cmd").value.trim();if(!t)return;$("#cans").textContent="…";try{const r=await api("POST","/r/say",{text:t});$("#cans").textContent=r.reply||"Done"}catch(e){$("#cans").textContent=e.message}refresh()}
function start(){$("#pairing").style.display="none";$("#app").style.display="block";render();refresh();clearInterval(start.t);start.t=setInterval(()=>{if(!document.hidden)refresh()},3000)}
if(!token||/pair=/.test(location.hash))showPair();else start();
</script></body></html>
"""
