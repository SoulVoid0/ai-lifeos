"""Voice: "Hey LifeOS" wake word, speech to text, and local text to speech with a voice for each agent.

Everything runs on this computer and costs nothing:
  * Speech recognition: Vosk (Kaldi) with a small English model (40 MB, or a 128 MB more accurate one). The wake
    word uses the same model restricted to a tiny grammar, so it listens with almost no CPU and never sends audio
    anywhere. After the wake word it records one command until you stop talking.
  * Speech: Piper neural voices (about 60 MB each, run on the CPU in a fraction of a second per sentence). Without
    Piper the voice built into the OS is used (Windows SAPI, espeak-ng on Linux).

Components are downloaded on demand from Settings (Piper from its GitHub release, voices from the rhasspy/piper-voices
repository on Hugging Face, Vosk models from alphacephei.com) into the LifeOS data folder.

A spoken command is routed like a typed one: "Forge, how's the build going?" chats with that agent in its own
voice, "ask Chrono to plan my week" starts a task for Chrono, "what's this error?" looks at the screen, "what was
that site I looked at Tuesday about GPUs?" searches your history, anything else goes to the assistant.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import activity, common

log = logging.getLogger("lifeos.voice")

PIPER_RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
PIPER_ASSET = {"win": "piper_windows_amd64.zip", "linux": "piper_linux_x86_64.tar.gz"}
VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
VOSK_BASE = "https://alphacephei.com/vosk/models/"
STT_MODELS = {
    "small": {"name": "vosk-model-small-en-us-0.15", "size": "40 MB", "label": "Small (fast, good for commands)"},
    "lgraph": {"name": "vosk-model-en-us-0.22-lgraph", "size": "128 MB", "label": "Large (more accurate)"},
}
SAMPLE_RATE = 16000

# Piper voices offered in Settings: id -> description. Ids follow <lang_REGION>-<name>-<quality>.
CATALOG: dict[str, str] = {
    "en_US-lessac-medium": "US, female, clear narrator",
    "en_US-amy-medium": "US, female, bright",
    "en_US-kristin-medium": "US, female, crisp",
    "en_US-hfc_female-medium": "US, female, upbeat",
    "en_US-ryan-high": "US, male, deep and steady",
    "en_US-joe-medium": "US, male, friendly",
    "en_US-john-medium": "US, male, energetic",
    "en_US-bryce-medium": "US, male, relaxed",
    "en_US-hfc_male-medium": "US, male, neutral",
    "en_US-norman-medium": "US, male, older and warm",
    "en_GB-alan-medium": "British, male, calm",
    "en_GB-northern_english_male-medium": "British (northern), male, dry",
    "en_GB-jenny_dioco-medium": "British, female, gentle",
    "en_GB-cori-high": "British, female, expressive",
    "en_GB-alba-medium": "Scottish, female, warm",
}
# Each built-in agent gets its own voice and pace (Piper length scale: below 1 is faster, above 1 slower).
AGENT_VOICES: dict[str, tuple[str, float]] = {
    "overseer": ("en_GB-alan-medium", 1.0),
    "planner": ("en_US-kristin-medium", 0.92),
    "files": ("en_GB-northern_english_male-medium", 1.0),
    "research": ("en_US-amy-medium", 0.95),
    "coder": ("en_US-ryan-high", 1.05),
    "memory": ("en_GB-jenny_dioco-medium", 1.12),
    "comms": ("en_US-joe-medium", 0.97),
    "creative": ("en_GB-cori-high", 0.95),
    "photo": ("en_US-bryce-medium", 1.05),
    "video": ("en_US-john-medium", 0.9),
    "quick": ("en_US-hfc_female-medium", 0.82),
}
WAKE_ALIASES = {"hey life os": ["hey life os", "hey life o s", "hey life", "hey lifeos", "a life os"]}

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_state: dict[str, Any] = {"state": "off", "heard": "", "reply": "", "agent": None, "error": "", "history": [],
                          "level": 0.0, "since": time.time()}
_speaking = threading.Event()
_ptt = threading.Event()
_listener: dict[str, Any] = {"thread": None, "stop": threading.Event()}
_say_q: queue.Queue[tuple[str, str, float] | None] = queue.Queue()
_player: dict[str, Any] = {"proc": None, "thread": None}


def cfg() -> dict[str, Any]:
    return common.group("voice")


def configure(changes: dict[str, Any]) -> dict[str, Any]:
    def check(c: dict[str, Any]) -> None:
        if c["stt_model"] not in STT_MODELS:
            raise ValueError("stt_model must be small or lgraph")
        if c["engine"] not in ("auto", "piper", "system", "browser"):
            raise ValueError("engine must be auto, piper, system or browser")
        if not 3 <= c["listen_seconds"] <= 30:
            raise ValueError("listen_seconds must be between 3 and 30")
        phrase = re.sub(r"[^a-z ]", "", c["wake_phrase"].lower()).strip()
        if not 1 <= len(phrase.split()) <= 4:
            raise ValueError("The wake phrase must be one to four words, like 'hey life os' or 'computer'")
        c["wake_phrase"] = phrase

    out = common.save_group("voice", changes, check)
    sync_listener()
    return out


# ---------------------------------------------------------------- where things live

def _dir() -> Path:
    return common.feature_dir("voice")


def _platform() -> str:
    return "win" if os.name == "nt" else "linux"


def piper_binary() -> Path | None:
    exe = "piper.exe" if os.name == "nt" else "piper"
    for p in (_dir() / "piper" / "piper" / exe, _dir() / "piper" / exe):
        if p.is_file():
            return p
    found = common.which("piper-tts", "piper") if os.name != "nt" else None
    if found and "piper" in Path(found).name:
        # Skip the unrelated GTK "piper" mouse tool by checking it understands --model.
        try:
            out = subprocess.run([found, "--help"], capture_output=True, text=True, timeout=5, **common.hidden())
            if "--model" in (out.stdout + out.stderr):
                return Path(found)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def voice_file(voice_id: str) -> Path:
    return _dir() / "voices" / f"{voice_id}.onnx"


def voice_url(voice_id: str) -> str:
    region, name, quality = voice_id.split("-", 2)
    return f"{VOICES_BASE}{region.split('_')[0]}/{region}/{name}/{quality}/{voice_id}.onnx"


def installed_voices() -> list[str]:
    folder = _dir() / "voices"
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.onnx") if (folder / f"{p.name}.json").is_file())


def stt_dir(key: str | None = None) -> Path:
    return _dir() / "vosk" / STT_MODELS[key or cfg()["stt_model"]]["name"]


def _module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001 - ImportError, or OSError when a native library (PortAudio) is missing
        return False


def system_voices() -> list[str]:
    if os.name == "nt":
        script = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)."
                  "GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }")
        try:
            out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True,
                                 timeout=20, **common.hidden())
            return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            return []
    return ["en-us", "en-gb", "en-us+m3", "en-us+f3", "en-gb-scotland"] if common.which("espeak-ng", "espeak") else []


def status() -> dict[str, Any]:
    c = cfg()
    voices = installed_voices()
    return {
        "config": c,
        "piper": bool(piper_binary()),
        "voices_installed": voices,
        "catalog": [{"id": k, "label": v, "installed": k in voices} for k, v in CATALOG.items()],
        "stt_models": [{"key": k, **v, "installed": (stt_dir(k) / "conf").is_dir() or (stt_dir(k) / "am").is_dir()}
                       for k, v in STT_MODELS.items()],
        "packages": {"vosk": _module("vosk"), "sounddevice": _module("sounddevice")},
        "system_tts": "windows" if os.name == "nt" else ("espeak-ng" if common.which("espeak-ng", "espeak") else None),
        "jobs": [dict(j) for j in _jobs.values()],
        "listener": {k: v for k, v in _state.items() if k != "history"},
        "history": list(_state["history"])[-10:],
        "agent_voices": {k: {"voice": v, "rate": r} for k, (v, r) in AGENT_VOICES.items()},
    }


def microphones() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Can't list microphones: {exc}") from exc
    return [{"index": i, "name": d["name"], "default": i == sd.default.device[0]}
            for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]


# ---------------------------------------------------------------- downloads

def install(items: list[str]) -> dict[str, Any]:
    """Download components in the background: "piper", "voice:<id>", "voices" (the crew's set), "stt:<small|lgraph>"."""
    wanted: list[str] = []
    for item in items:
        if item == "voices":
            wanted += [f"voice:{v}" for v, _ in AGENT_VOICES.values()] + ["voice:en_US-lessac-medium"]
        elif item == "piper" or (item.startswith("voice:") and item[6:] in CATALOG) or \
                (item.startswith("stt:") and item[4:] in STT_MODELS):
            wanted.append(item)
        else:
            raise ValueError(f"Unknown voice component: {item}")
    started = []
    for item in dict.fromkeys(wanted):
        with _lock:
            job = _jobs.get(item)
            if job and job["state"] == "downloading":
                continue
            if item.startswith("voice:") and item[6:] in installed_voices():
                continue
            job = {"item": item, "state": "downloading", "done": 0, "total": 0, "error": None}
            _jobs[item] = job
        common.background(f"voice-install-{item}", lambda i=item, j=job: _install_one(i, j))
        started.append(item)
    return {"started": started, **status()}


def _install_one(item: str, job: dict[str, Any]) -> None:
    try:
        if item == "piper":
            asset = PIPER_ASSET[_platform()]
            archive = common.download(PIPER_RELEASE + asset, _dir() / asset, job)
            common.extract(archive, _dir() / "piper")
            exe = piper_binary()
            if exe and os.name != "nt":
                exe.chmod(0o755)
        elif item.startswith("voice:"):
            vid = item[6:]
            common.download(voice_url(vid) + ".json", voice_file(vid).with_suffix(".onnx.json"))
            common.download(voice_url(vid), voice_file(vid), job)
        else:
            key = item[4:]
            name = STT_MODELS[key]["name"]
            archive = common.download(f"{VOSK_BASE}{name}.zip", _dir() / f"{name}.zip", job)
            common.extract(archive, _dir() / "vosk")
            _models.pop(key, None)
        job["state"] = "done"
        activity.log("system", f"Voice: installed {item}")
        if item.startswith("stt:"):
            sync_listener()
    except Exception as exc:  # noqa: BLE001 - reported on the job
        job.update(state="failed", error=f"{type(exc).__name__}: {exc}"[:300])
        activity.log("system", f"Voice: couldn't install {item}: {exc}"[:300], status="error")


# ---------------------------------------------------------------- speaking

def clean_text(text: str, limit: int = 700) -> str:
    """Markdown and links out, one paragraph, cut at a sentence end: what a voice should read."""
    t = re.sub(r"```.*?```", " (code omitted) ", text or "", flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_#>|~]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…"
    return t


def voice_for(agent_slug: str | None) -> tuple[str, float]:
    """(voice, rate) for an agent: its own setting, else its default, else the global default."""
    c = cfg()
    rate = 1.0
    voice = ""
    if agent_slug:
        try:
            from . import crew

            a = crew.get(agent_slug)
            voice, rate = a.get("tts_voice") or "", float(a.get("tts_rate") or 1.0)
        except KeyError:
            pass
        if not voice and agent_slug in AGENT_VOICES:
            voice, default_rate = AGENT_VOICES[agent_slug]
            rate = rate if rate != 1.0 else default_rate
    return voice or c["default_voice"], max(0.5, min(rate, 2.0))


def _piper_wav(text: str, voice_id: str, rate: float, out: Path) -> bool:
    exe = piper_binary()
    model = voice_file(voice_id)
    if not exe or not model.is_file():
        return False
    proc = subprocess.run([str(exe), "--model", str(model), "--output_file", str(out), "--length_scale", f"{rate:.2f}"],
                          input=text.encode("utf-8"), capture_output=True, timeout=120, cwd=str(exe.parent),
                          **common.hidden())
    if proc.returncode != 0 or not out.is_file():
        log.warning("Piper failed: %s", proc.stderr[-400:])
        return False
    return True


def _system_wav(text: str, voice: str, rate: float, out: Path) -> bool:
    if os.name == "nt":
        sapi_rate = max(-10, min(10, round((1 / rate - 1) * 10)))
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "if ($env:LIFEOS_VOICE) { try { $s.SelectVoice($env:LIFEOS_VOICE) } catch {} }; "
                  f"$s.Rate = {sapi_rate}; $s.SetOutputToWaveFile($env:LIFEOS_WAV); "
                  "$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
        env = {**os.environ, "LIFEOS_VOICE": voice, "LIFEOS_WAV": str(out)}
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], input=text.encode("utf-8"),
                              capture_output=True, timeout=120, env=env, **common.hidden())
        return proc.returncode == 0 and out.is_file()
    espeak = common.which("espeak-ng", "espeak")
    if not espeak:
        return False
    wpm = int(175 / rate)
    proc = subprocess.run([espeak, "-v", voice or "en-us", "-s", str(wpm), "-w", str(out), "--stdin"],
                          input=text.encode("utf-8"), capture_output=True, timeout=60)
    return proc.returncode == 0 and out.is_file()


def synthesize(text: str, agent_slug: str | None = None, voice: str | None = None, rate: float | None = None) -> Path:
    """Render speech to a WAV file. voice: "piper:<id>", "system:<name>" or a bare Piper id."""
    text = clean_text(text)
    if not text:
        raise ValueError("Nothing to say")
    v, r = voice_for(agent_slug)
    v, r = voice or v, rate or r
    engine = cfg()["engine"]
    out = Path(tempfile.gettempdir()) / f"lifeos-say-{os.getpid()}-{threading.get_ident()}-{int(time.time() * 1000)}.wav"
    kind, _, name = v.partition(":") if ":" in v else ("piper", "", v)
    ok = False
    if engine in ("auto", "piper") and kind == "piper":
        ok = _piper_wav(text, name, r, out)
        if (not ok and piper_binary() and name in CATALOG and name not in installed_voices()
                and _jobs.get(f"voice:{name}", {}).get("state") != "failed"):
            install([f"voice:{name}"])  # fetch it in the background; this line uses another voice, the next one this
        if not ok and engine == "auto" and piper_binary():
            installed = installed_voices()
            if installed:  # its own voice isn't downloaded: any Piper voice beats the robot voice
                default = cfg()["default_voice"].split(":")[-1]
                ok = _piper_wav(text, default if default in installed else installed[0], r, out)
    if not ok and engine in ("auto", "system"):
        ok = _system_wav(text, name if kind == "system" else "", r, out)
    if not ok:
        raise ValueError("No voice is available. Install Piper and a voice in Settings → Voice"
                         + ("" if os.name == "nt" else ", or install espeak-ng"))
    return out


def _play(path: Path) -> None:
    if os.name == "nt":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    player = common.which("pw-play", "paplay", "aplay")
    if not player:
        raise ValueError("No audio player found (pw-play, paplay or aplay)")
    proc = subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _player["proc"] = proc
    proc.wait(timeout=300)


def _speaker() -> None:
    while True:
        item = _say_q.get()
        if item is None:
            return
        text, agent_slug, _ = item
        path = None
        try:
            path = synthesize(text, agent_slug)
            _speaking.set()
            prev = _state["state"]
            _set(state="speaking")
            _play(path)
            _set(state=prev if prev != "speaking" else ("listening" if _listening() else "off"))
        except Exception as exc:  # noqa: BLE001 - speech is best effort
            _set(error=f"Speaking failed: {exc}"[:300])
            log.warning("Speaking failed: %s", exc)
        finally:
            time.sleep(0.3)  # let the room echo die down before listening again
            _speaking.clear()
            if path:
                try:
                    path.unlink()
                except OSError:
                    pass


def say(text: str, agent_slug: str | None = None) -> dict[str, Any]:
    """Speak through this computer's speakers (queued, so agents don't talk over each other)."""
    if cfg()["engine"] == "browser":
        return {"queued": False, "engine": "browser", "text": clean_text(text)}
    with _lock:
        t = _player["thread"]
        if t is None or not t.is_alive():
            _player["thread"] = threading.Thread(target=_speaker, daemon=True, name="lifeos-speaker")
            _player["thread"].start()
    _say_q.put((text, agent_slug or "", time.time()))
    return {"queued": True, "engine": cfg()["engine"]}


def stop_speaking() -> None:
    while True:
        try:
            _say_q.get_nowait()
        except queue.Empty:
            break
    if os.name == "nt":
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:  # noqa: BLE001
            pass
    proc = _player.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()


# ---------------------------------------------------------------- listening

_models: dict[str, Any] = {}


def _load_model() -> Any:
    import vosk

    key = cfg()["stt_model"]
    if key not in _models:
        path = stt_dir(key)
        if not path.is_dir():
            other = next((k for k in STT_MODELS if stt_dir(k).is_dir()), None)
            if not other:
                raise ValueError("No speech model is installed. Download one in Settings → Voice.")
            key, path = other, stt_dir(other)
        vosk.SetLogLevel(-1)
        _models[key] = vosk.Model(str(path))
    return _models[key]


def wake_grammar(phrase: str) -> list[str]:
    return list(dict.fromkeys(WAKE_ALIASES.get(phrase, [phrase]) + ["[unk]"]))


def is_wake(text: str, phrase: str) -> bool:
    t = " ".join(re.sub(r"[^a-z ]", " ", (text or "").lower()).split())
    if not t:
        return False
    aliases = [a for a in WAKE_ALIASES.get(phrase, [phrase])]
    return any(t == a or t.startswith(a + " ") or t.endswith(" " + a) for a in aliases)


def _set(**kw: Any) -> None:
    _state.update(kw, since=time.time())


def _listening() -> bool:
    t = _listener["thread"]
    return bool(t and t.is_alive())


def _paused() -> bool:
    if _speaking.is_set():
        return True
    try:
        from . import gamemode

        return bool(cfg()["pause_in_games"] and gamemode.active())
    except Exception:  # noqa: BLE001
        return False


def sync_listener() -> None:
    """Start or stop the wake word listener to match the settings."""
    want = bool(cfg()["wake_word"]) and not os.environ.get("LIFEOS_NO_SERVICES")
    if want and not _listening():
        stop = threading.Event()
        _listener["stop"] = stop
        _listener["thread"] = threading.Thread(target=_listen_loop, args=(stop,), daemon=True, name="lifeos-wake")
        _listener["thread"].start()
    elif not want and _listening():
        _listener["stop"].set()


def _open_stream(callback: Any) -> tuple[Any, int]:
    import sounddevice as sd

    mic = cfg()["mic"]
    device: Any = int(mic) if mic.isdigit() else (mic or None)
    try:
        stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, device=device, dtype="int16", channels=1,
                                   callback=callback)
        return stream, SAMPLE_RATE
    except Exception:  # noqa: BLE001 - the device can't do 16 kHz itself: record at its own rate, Vosk resamples
        info = sd.query_devices(device, "input")
        rate = int(info["default_samplerate"])
        stream = sd.RawInputStream(samplerate=rate, blocksize=rate // 4, device=device, dtype="int16", channels=1,
                                   callback=callback)
        return stream, rate


def _level(data: bytes) -> float:
    if not data:
        return 0.0
    import array

    a = array.array("h", data[: 2 * 800])
    return min(1.0, (sum(abs(x) for x in a) / max(1, len(a))) / 4000)


def _listen_loop(stop: threading.Event) -> None:
    try:
        import vosk

        model = _load_model()
        q: queue.Queue[bytes] = queue.Queue(maxsize=200)

        def cb(indata: Any, frames: int, t: Any, flags: Any) -> None:
            if not _paused():
                try:
                    q.put_nowait(bytes(indata))
                except queue.Full:
                    pass

        stream, rate = _open_stream(cb)
        phrase = cfg()["wake_phrase"]
        wake = vosk.KaldiRecognizer(model, rate, json.dumps(wake_grammar(phrase)))
        _set(state="listening", error="")
        activity.log("system", f"Voice: listening for \"{phrase}\"")
        with stream:
            while not stop.is_set():
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    if _paused():
                        _set(state="paused" if not _speaking.is_set() else _state["state"])
                    continue
                if _state["state"] == "paused":
                    _set(state="listening")
                _state["level"] = _level(data)
                if _ptt.is_set():
                    _ptt.clear()
                    _command(q, model, rate)
                    wake.Reset()
                    continue
                text = json.loads(wake.Result())["text"] if wake.AcceptWaveform(data) else \
                    json.loads(wake.PartialResult()).get("partial", "")
                if is_wake(text, phrase):
                    wake.Reset()
                    _command(q, model, rate)
                    while not q.empty():  # drop what was said while we were busy
                        q.get_nowait()
    except Exception as exc:  # noqa: BLE001
        _set(state="error", error=f"{type(exc).__name__}: {exc}"[:300])
        log.warning("Wake word listener stopped: %s", exc)
        return
    _set(state="off")


def _chime() -> None:
    if not cfg()["chime"]:
        return
    if os.name == "nt":
        try:
            import winsound

            winsound.Beep(880, 90)
            winsound.Beep(1320, 90)
        except Exception:  # noqa: BLE001
            pass


def _capture(q: queue.Queue[bytes], model: Any, rate: int) -> str:
    import vosk

    rec = vosk.KaldiRecognizer(model, rate)
    limit = time.monotonic() + cfg()["listen_seconds"]
    silent_limit = time.monotonic() + 5
    heard = ""
    while time.monotonic() < limit:
        try:
            data = q.get(timeout=0.5)
        except queue.Empty:
            continue
        _state["level"] = _level(data)
        if rec.AcceptWaveform(data):
            text = json.loads(rec.Result()).get("text", "")
            if text:
                heard = (heard + " " + text).strip()
                break
        else:
            partial = json.loads(rec.PartialResult()).get("partial", "")
            if partial:
                _set(heard=partial)
                silent_limit = limit
            elif time.monotonic() > silent_limit:
                break
    if not heard:
        heard = json.loads(rec.FinalResult()).get("text", "")
    return heard.strip()


def _command(q: queue.Queue[bytes], model: Any, rate: int) -> None:
    _set(state="heard", heard="", reply="", agent=None)
    _speaking.set()
    _chime()
    time.sleep(0.15)
    _speaking.clear()
    while not q.empty():
        q.get_nowait()
    text = _capture(q, model, rate)
    if not text:
        _set(state="listening", heard="")
        return
    handle(text)
    _set(state="listening" if _listening() else "off")


def push_to_talk() -> dict[str, Any]:
    """Listen for one command now, without the wake word (the mic button)."""
    if _listening():
        _ptt.set()
        return {"ok": True}

    def once() -> None:
        try:
            model = _load_model()
            q: queue.Queue[bytes] = queue.Queue(maxsize=200)
            stream, rate = _open_stream(lambda d, f, t, s: q.put_nowait(bytes(d)) if not q.full() else None)
            with stream:
                _command(q, model, rate)
        except Exception as exc:  # noqa: BLE001
            _set(state="error", error=f"{type(exc).__name__}: {exc}"[:300])
        else:
            _set(state="off")

    if not _module("vosk") or not _module("sounddevice"):
        raise ValueError("Speech recognition isn't available in this build (vosk and sounddevice are missing)")
    common.background("voice-ptt", once)
    return {"ok": True}


# ---------------------------------------------------------------- what a command does

SCREEN_RE = re.compile(r"\b(on (my|the) screen|this (error|window|message|page|code|dialog|popup)|what('?s| is) this|"
                       r"look at (this|my screen)|what am i (looking at|seeing)|read (this|my screen))\b")
RECALL_RE = re.compile(r"\b(what|which|where|when)\b.*\b(was|were|did)\b.*\b(i|we)\b.*\b(look(ed)? at|saw|see|visit(ed)?|"
                       r"read|open(ed)?|download(ed)?|watch(ed)?|brows(e|ed)|talk(ed)? about|said|tell you)\b")
STOP_RE = re.compile(r"^(stop|cancel|abort) (everything|all|all missions|the crew|all tasks)\b")
HUSH_RE = re.compile(r"^(stop talking|be quiet|shut up|silence|quiet)\b")


def _agents_by_name() -> dict[str, str]:
    from . import crew

    return {a["name"].lower(): a["slug"] for a in crew.list_agents(enabled_only=True)}


def route(text: str) -> dict[str, Any]:
    """Decide what a spoken (or typed) command is: returns {kind, agent?, text}."""
    t = " ".join((text or "").lower().replace(",", " , ").split()).replace(" , ", ", ").strip(" ,.!?")
    if not t:
        return {"kind": "none", "text": ""}
    if HUSH_RE.match(t):
        return {"kind": "hush", "text": t}
    if re.fullmatch(r"(?:life ?os,? )?(?:panic|panic button|emergency stop|code red)", t):
        return {"kind": "panic", "text": t}
    if STOP_RE.match(t):
        return {"kind": "stop", "text": t}
    if re.search(r"\bgame mode (on|off)\b|\b(i'?m|i am) (gaming|playing)\b|\b(done|finished) (gaming|playing)\b", t):
        on = bool(re.search(r"\bon\b|gaming|playing", t)) and not re.search(r"\b(off|done|finished)\b", t)
        return {"kind": "game", "on": on, "text": t}
    if SCREEN_RE.search(t):
        return {"kind": "screen", "text": text.strip()}
    if RECALL_RE.search(t):
        return {"kind": "recall", "text": text.strip()}
    names = _agents_by_name()
    o = " ".join((text or "").split()).strip(" ,.!")
    m = re.match(r"^(?:ask|tell|have|get|let)\s+(\w+)\s+(?:to\s+)?(.+)$", o, re.I)
    if m and m.group(1).lower() in names:
        return {"kind": "task", "agent": names[m.group(1).lower()], "text": m.group(2)}
    m = re.match(r"^(?:hey\s+)?(\w+)[,:]?\s+(.+)$", o, re.I)
    if m and m.group(1).lower() in names:
        return {"kind": "talk", "agent": names[m.group(1).lower()], "text": m.group(2)}
    m = re.match(r"^(?:crew|team|everyone)[,:]?\s+(.+)$|^start a mission (?:to\s+)?(.+)$", o, re.I)
    if m:
        return {"kind": "mission", "text": m.group(1) or m.group(2)}
    return {"kind": "assistant", "text": text.strip()}


def handle(text: str, speak: bool = True) -> dict[str, Any]:
    """Run one command and (optionally) speak the reply in the answering agent's voice."""
    from . import missions

    _set(state="thinking", heard=text)
    r = route(text)
    agent_slug: str | None = "overseer"
    reply = ""
    try:
        if r["kind"] == "none":
            return {"route": r, "reply": ""}
        if r["kind"] == "hush":
            stop_speaking()
            return {"route": r, "reply": ""}
        if r["kind"] == "panic":
            from . import panic

            panic.trigger("voice")
            return {"route": r, "reply": "", "agent": None}
        if r["kind"] == "stop":
            n = missions.stop_all()
            reply = f"Stopped {n} mission{'s' if n != 1 else ''}." if n else "Nothing was running."
        elif r["kind"] == "game":
            from . import gamemode

            gamemode.set_manual(r["on"])
            reply = "Game mode on. The GPU is all yours." if r["on"] else "Game mode off. The crew is back."
        elif r["kind"] == "screen":
            from . import screen

            agent_slug = "research"
            reply = screen.ask(r["text"])["answer"]
        elif r["kind"] == "recall":
            from . import recall

            agent_slug = "memory"
            reply = recall.answer(r["text"])["answer"]
        elif r["kind"] == "task":
            agent_slug = r["agent"]
            m = missions.start(r["text"], agent=r["agent"], source="voice")
            from . import crew

            reply = f"{crew.get(r['agent'])['name']} is on it. Mission {m['id']}."
        elif r["kind"] == "talk":
            agent_slug = r["agent"]
            reply = missions.talk(r["agent"], r["text"])["reply"]
        elif r["kind"] == "mission":
            m = missions.start(r["text"], source="voice")
            reply = f"Crew, stations. Mission {m['id']} is underway."
        else:
            from . import agent

            out = agent.handle(r["text"])
            reply = out["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - say what went wrong instead of going silent
        reply = f"Sorry, that didn't work: {exc}"[:300]
    entry = {"heard": text, "reply": reply, "agent": agent_slug, "kind": r["kind"], "at": time.strftime("%H:%M:%S")}
    _state["history"] = (_state["history"] + [entry])[-20:]
    _set(state="speaking" if speak and reply else _state["state"], reply=reply, agent=agent_slug)
    activity.log("voice", f"Heard \"{text[:120]}\"", details={"route": r["kind"], "reply": reply[:300]})
    if speak and reply:
        say(reply, agent_slug)
    return {"route": r, "reply": reply, "agent": agent_slug}


def shutdown() -> None:
    _listener["stop"].set()
    stop_speaking()

