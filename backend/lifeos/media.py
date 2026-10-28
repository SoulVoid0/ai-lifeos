"""Image and video generation.

Local first: a ComfyUI server (SDXL or Flux-schnell checkpoints for images, LTX-Video for short clips) or an
Automatic1111/Forge WebUI (images). Optionally OpenAI's image API, which only runs when cloud AI is on, a key is
stored and today's spend is under the daily cap. Every result is saved as a file (LifeOS's media folder by
default, or an approved folder you pick) and recorded in the media table for the gallery.

ComfyUI jobs use built-in API-format workflows. To use any other model or graph, export your own workflow from
ComfyUI with "Save (API)" and point the setting at it: the placeholders {{prompt}}, {{negative}}, {{seed}},
{{width}}, {{height}}, {{frames}}, {{steps}} and {{checkpoint}} are filled in.
"""
from __future__ import annotations

import base64
import copy
import json
import random
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import activity, config, db, llm, permissions, settings, vault

KINDS = ("image", "video")
BACKENDS = ("comfyui", "a1111", "openai", "off")
IMAGE_TIMEOUT_S = 600
VIDEO_TIMEOUT_S = 3600
SIZES = {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832), "wide": (1344, 768)}
VIDEO_SIZES = {"landscape": (768, 512), "portrait": (512, 768), "square": (640, 640), "wide": (768, 432)}
_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
        "mp4": "video/mp4", "webm": "video/webm"}


class MediaError(Exception):
    pass


def cfg() -> dict[str, Any]:
    return {**DEFAULTS, **(settings.get("media") or {})}


DEFAULTS: dict[str, Any] = {
    "backend": "comfyui",
    "comfy_url": "http://127.0.0.1:8188",
    "a1111_url": "http://127.0.0.1:7860",
    "image_checkpoint": "",          # empty: first checkpoint ComfyUI reports
    "video_checkpoint": "ltx-video-2b-v0.9.5.safetensors",
    "video_text_encoder": "t5xxl_fp8_e4m3fn.safetensors",
    "image_workflow": "",            # path to a ComfyUI API-format workflow (optional)
    "video_workflow": "",
    "output_folder": "",             # empty: <LifeOS data>/media; otherwise an approved folder
    "cloud_image_model": "gpt-image-1",
    "cloud_price_usd": 0.04,         # per image, for the ledger and the daily cap
    "free_vram": True,               # unload Ollama models first when both share one GPU
}


def update_cfg(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate and store media settings; returns the merged config."""
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown media settings: {', '.join(sorted(unknown))}")
    merged = cfg()
    for key, value in changes.items():
        expected = type(DEFAULTS[key])
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected):
            raise ValueError(f"Media setting {key} must be {expected.__name__}")
        merged[key] = value.strip() if isinstance(value, str) else value
    if merged["backend"] not in BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(BACKENDS)}")
    for key in ("comfy_url", "a1111_url"):
        if not re.match(r"^https?://[^\s]+$", merged[key]):
            raise ValueError(f"{key} must be an http(s) URL")
    if merged["cloud_price_usd"] < 0:
        raise ValueError("cloud_price_usd can't be negative")
    if merged["output_folder"]:
        permissions.resolve_allowed(merged["output_folder"])
    for key in ("image_workflow", "video_workflow"):
        if merged[key] and not Path(merged[key]).expanduser().is_file():
            raise ValueError(f"{key}: file not found")
    settings.update({"media": {k: v for k, v in merged.items() if v != DEFAULTS[k]}})
    return cfg()


# ---------------------------------------------------------------- storage

def output_dir() -> Path:
    folder = cfg()["output_folder"].strip()
    if folder:
        target = permissions.resolve_allowed(folder)[1]
    else:
        target = config.data_dir() / "media"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _row(r: Any) -> dict[str, Any]:
    out = db.row_to_dict(r, ("params",))
    assert out is not None
    out["name"] = Path(out["path"]).name if out.get("path") else None
    return out


def list_media(limit: int = 60, kind: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM media WHERE 1=1", []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    with db.session() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    return [_row(r) for r in rows]


def get(media_id: int) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    if not r:
        raise KeyError(f"Media {media_id} not found")
    return _row(r)


def file_for(media_id: int) -> tuple[Path, str]:
    m = get(media_id)
    if m["status"] != "done" or not m["path"]:
        raise FileNotFoundError(f"media {media_id} has no file yet")
    path = Path(m["path"])
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path, _EXT.get(path.suffix.lstrip(".").lower(), "application/octet-stream")


def delete(media_id: int, remove_file: bool = True) -> dict[str, Any]:
    m = get(media_id)
    if remove_file and m.get("path"):
        p = Path(m["path"])
        # Only files LifeOS wrote into its media folders are deleted.
        if p.is_file() and p.name.startswith("lifeos-"):
            p.unlink()
    with db.session() as conn:
        conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
    activity.log("media", f"Deleted {m['kind']} #{media_id}")
    return {"ok": True}


def _set(media_id: int, **fields: Any) -> None:
    with db.session() as conn:
        conn.execute(f"UPDATE media SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), media_id))


def _save(data: bytes, ext: str, prompt: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or "media"
    path = output_dir() / f"lifeos-{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{uuid.uuid4().hex[:6]}.{ext}"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- status

def status() -> dict[str, Any]:
    c = cfg()
    out: dict[str, Any] = {"backend": c["backend"], "ok": False, "checkpoints": [], "video": False, "detail": ""}
    try:
        if c["backend"] == "comfyui":
            r = httpx.get(f"{c['comfy_url'].rstrip('/')}/object_info/CheckpointLoaderSimple", timeout=4)
            r.raise_for_status()
            info = r.json().get("CheckpointLoaderSimple", {})
            out["checkpoints"] = list(((info.get("input") or {}).get("required") or {}).get("ckpt_name", [[]])[0])
            out["ok"] = True
            out["video"] = bool(c["video_workflow"]) or any("ltx" in x.lower() for x in out["checkpoints"])
            out["detail"] = f"ComfyUI with {len(out['checkpoints'])} checkpoint(s)"
        elif c["backend"] == "a1111":
            r = httpx.get(f"{c['a1111_url'].rstrip('/')}/sdapi/v1/sd-models", timeout=4)
            r.raise_for_status()
            out["checkpoints"] = [m.get("model_name") or m.get("title") for m in r.json()]
            out["ok"] = True
            out["detail"] = "Automatic1111 WebUI (images only)"
        elif c["backend"] == "openai":
            out["ok"] = bool(settings.get("cloud_enabled") and vault.has_secret(llm.key_name("openai")))
            out["detail"] = (f"OpenAI {c['cloud_image_model']} (≈${c['cloud_price_usd']}/image)" if out["ok"]
                             else "Turn on cloud AI and add an OpenAI key in Settings")
        else:
            out["detail"] = "Generation is turned off"
    except (httpx.HTTPError, ValueError) as exc:
        out["detail"] = f"Can't reach {c['backend']}: {str(exc)[:160]}"
    return out


# ---------------------------------------------------------------- generation

def generate(kind: str, prompt: str, *, negative: str = "", size: str = "square", seed: int | None = None,
             frames: int = 97, steps: int | None = None, mission_id: int | None = None, agent: str | None = None,
             wait: bool = True) -> dict[str, Any]:
    """Create a media job. With wait=True (agents), block until it finishes; otherwise run it in the background."""
    if kind not in KINDS:
        raise ValueError("kind must be image or video")
    prompt = re.sub(r"\s+", " ", prompt or "").strip()
    if len(prompt) < 3:
        raise ValueError("Describe what to generate")
    c = cfg()
    if c["backend"] == "off":
        raise MediaError("Image and video generation is turned off (Settings → Media)")
    if kind == "video" and c["backend"] != "comfyui":
        raise MediaError("Video needs a ComfyUI backend with LTX-Video (Settings → Media)")
    sizes = VIDEO_SIZES if kind == "video" else SIZES
    width, height = sizes.get(size, next(iter(sizes.values())))
    params = {"negative": negative, "size": size, "width": width, "height": height,
              "seed": seed if seed is not None else random.randint(1, 2**31 - 1),
              "frames": max(9, min(int(frames), 257)), "steps": steps}
    with db.session() as conn:
        cur = conn.execute("INSERT INTO media(kind, prompt, backend, status, params, mission_id, agent, created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)", (kind, prompt[:2000], c["backend"], "queued", json.dumps(params),
                                                         mission_id, agent, db.now()))
        media_id = int(cur.lastrowid)
    if wait:
        _run(media_id)
    else:
        threading.Thread(target=_run, args=(media_id,), daemon=True, name=f"lifeos-media-{media_id}").start()
    return get(media_id)


def _run(media_id: int) -> None:
    m = get(media_id)
    started = time.monotonic()
    _set(media_id, status="running")
    try:
        c = cfg()
        p = m["params"]
        if c["backend"] in ("comfyui", "a1111") and c["free_vram"]:
            llm.unload_local_models()
        if c["backend"] == "comfyui":
            data, ext, model = _comfy(m["kind"], m["prompt"], p, c)
        elif c["backend"] == "a1111":
            data, ext, model = _a1111(m["prompt"], p, c)
        elif c["backend"] == "openai":
            data, ext, model = _openai(m["prompt"], p, c, m.get("mission_id"), m.get("agent"))
        else:
            raise MediaError("Generation is turned off")
        path = _save(data, ext, m["prompt"])
        _set(media_id, status="done", path=str(path), model=model, seconds=round(time.monotonic() - started, 1),
             finished_at=db.now())
        activity.log("media", f"Generated {m['kind']}: {m['prompt'][:120]}", details={"path": str(path), "model": model})
    except (MediaError, llm.LLMUnavailable, httpx.HTTPError, ValueError, KeyError, OSError, permissions.PermissionDenied) as exc:
        msg = str(exc)[:500]
        _set(media_id, status="failed", error=msg, seconds=round(time.monotonic() - started, 1), finished_at=db.now())
        activity.log("media", f"{m['kind'].capitalize()} generation failed: {msg}"[:300], status="error")


# ---------------------------------------------------------------- ComfyUI

def image_workflow(checkpoint: str, prompt: str, negative: str, width: int, height: int, seed: int,
                   steps: int | None) -> dict[str, Any]:
    """Text-to-image with any single-file checkpoint. Flux-schnell gets its own fast settings."""
    flux = "flux" in checkpoint.lower()
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "" if flux else negative or "blurry, low quality, watermark",
                                                          "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps or (4 if flux else 28), "cfg": 1.0 if flux else 6.5,
            "sampler_name": "euler" if flux else "dpmpp_2m", "scheduler": "simple" if flux else "karras", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lifeos", "images": ["8", 0]}},
    }


def video_workflow(checkpoint: str, text_encoder: str, prompt: str, negative: str, width: int, height: int, frames: int,
                   seed: int, steps: int | None) -> dict[str, Any]:
    """LTX-Video text-to-video (ComfyUI's built-in LTXV nodes), saved as an animated WebP."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "low quality, worst quality, deformed, distorted, "
                                                          "motion smear, blurry", "clip": ["2", 0]}},
        "5": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": 24}},
        "6": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": width, "height": height, "length": frames,
                                                               "batch_size": 1}},
        "7": {"class_type": "LTXVScheduler", "inputs": {"steps": steps or 30, "max_shift": 2.05, "base_shift": 0.95,
                                                        "stretch": True, "terminal": 0.1, "latent": ["6", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["5", 1], "cfg": 3.0}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["8", 0],
                                                                 "sigmas": ["7", 0], "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["12", 0], "filename_prefix": "lifeos", "fps": 24.0,
                                                            "lossless": False, "quality": 90, "method": "default"}},
    }


def fill_workflow(workflow: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Replace {{placeholders}} in a user-exported workflow. A string that is only a placeholder takes the raw value."""
    def sub(v: Any) -> Any:
        if isinstance(v, str):
            whole = re.fullmatch(r"\{\{(\w+)\}\}", v.strip())
            if whole and whole.group(1) in values:
                return values[whole.group(1)]
            return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), v)
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v
    return sub(copy.deepcopy(workflow))


def _comfy(kind: str, prompt: str, p: dict[str, Any], c: dict[str, Any]) -> tuple[bytes, str, str]:
    base = c["comfy_url"].rstrip("/")
    custom = c["video_workflow" if kind == "video" else "image_workflow"].strip()
    if kind == "image":
        checkpoint = c["image_checkpoint"].strip()
        if not checkpoint and not custom:
            ckpts = status().get("checkpoints") or []
            checkpoint = next((x for x in ckpts if "ltx" not in x.lower()), "")
            if not checkpoint:
                raise MediaError("ComfyUI has no image checkpoint. Put an SDXL or Flux-schnell .safetensors file in "
                                 "ComfyUI/models/checkpoints")
    else:
        checkpoint = c["video_checkpoint"].strip()
    values = {"prompt": prompt, "negative": p.get("negative") or "", "seed": p["seed"], "width": p["width"],
              "height": p["height"], "frames": p["frames"], "steps": p.get("steps") or (30 if kind == "video" else 28),
              "checkpoint": checkpoint}
    if custom:
        try:
            workflow = fill_workflow(json.loads(Path(custom).expanduser().read_text(encoding="utf-8")), values)
        except (OSError, ValueError) as exc:
            raise MediaError(f"Couldn't read the custom workflow {custom}: {exc}") from exc
    elif kind == "image":
        workflow = image_workflow(checkpoint, prompt, values["negative"], p["width"], p["height"], p["seed"], p.get("steps"))
    else:
        workflow = video_workflow(checkpoint, c["video_text_encoder"], prompt, values["negative"], p["width"], p["height"],
                                  p["frames"], p["seed"], p.get("steps"))
    r = httpx.post(f"{base}/prompt", json={"prompt": workflow, "client_id": "lifeos"}, timeout=30)
    if r.status_code >= 400:
        detail = r.text[:400]
        raise MediaError(f"ComfyUI rejected the workflow: {detail}")
    prompt_id = r.json()["prompt_id"]
    deadline = time.monotonic() + (VIDEO_TIMEOUT_S if kind == "video" else IMAGE_TIMEOUT_S)
    while time.monotonic() < deadline:
        h = httpx.get(f"{base}/history/{prompt_id}", timeout=15)
        h.raise_for_status()
        entry = h.json().get(prompt_id)
        if entry:
            st = entry.get("status") or {}
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                raise MediaError(f"ComfyUI error: {json.dumps(msgs)[:400] if msgs else 'execution failed'}")
            for out in (entry.get("outputs") or {}).values():
                for key in ("images", "gifs", "videos"):
                    for f in out.get(key) or []:
                        v = httpx.get(f"{base}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                                             "type": f.get("type", "output")}, timeout=120)
                        v.raise_for_status()
                        ext = f["filename"].rsplit(".", 1)[-1].lower()
                        return v.content, ext, checkpoint or Path(custom).name
            if st.get("completed"):
                raise MediaError("ComfyUI finished but produced no image or video")
        time.sleep(1.0)
    raise MediaError("Timed out waiting for ComfyUI")


# ---------------------------------------------------------------- Automatic1111 / Forge

def _a1111(prompt: str, p: dict[str, Any], c: dict[str, Any]) -> tuple[bytes, str, str]:
    base = c["a1111_url"].rstrip("/")
    body = {"prompt": prompt, "negative_prompt": p.get("negative") or "blurry, low quality", "width": p["width"],
            "height": p["height"], "steps": p.get("steps") or 28, "seed": p["seed"], "cfg_scale": 6.5,
            "sampler_name": "DPM++ 2M"}
    if c["image_checkpoint"].strip():
        body["override_settings"] = {"sd_model_checkpoint": c["image_checkpoint"].strip()}
    r = httpx.post(f"{base}/sdapi/v1/txt2img", json=body, timeout=IMAGE_TIMEOUT_S)
    r.raise_for_status()
    images = r.json().get("images") or []
    if not images:
        raise MediaError("Automatic1111 returned no image")
    return base64.b64decode(images[0].split(",", 1)[-1]), "png", c["image_checkpoint"] or "a1111"


# ---------------------------------------------------------------- OpenAI (cloud, opt-in)

def _openai(prompt: str, p: dict[str, Any], c: dict[str, Any], mission_id: int | None,
            agent: str | None) -> tuple[bytes, str, str]:
    key = llm._check_cloud("openai")
    price = float(c["cloud_price_usd"])
    today = llm.spend_today()
    if today["cloud_cost_usd"] + price > today["cloud_budget_usd"]:
        raise MediaError(f"This image (≈${price:.2f}) would go over today's cloud budget")
    size = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024", "wide": "1536x1024"}.get(p["size"],
                                                                                                          "1024x1024")
    model = c["cloud_image_model"]
    r = httpx.post("https://api.openai.com/v1/images/generations", headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "prompt": prompt, "size": size, "n": 1}, timeout=IMAGE_TIMEOUT_S)
    if r.status_code >= 400:
        raise MediaError(f"OpenAI images: {r.text[:300]}")
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        data = httpx.get(item["url"], timeout=120).content
    else:
        raise MediaError("OpenAI returned no image")
    llm.record_cost("openai", model, price, mission_id, agent)
    return data, "png", f"openai:{model}"
