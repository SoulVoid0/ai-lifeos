"""Story mode: an interactive story told by a crew member, with a picture for every scene made locally in ComfyUI.

You give a premise (and optionally an art style); the narrator (Muse by default) writes a scene and three choices.
Pick one, or write your own, and the story goes on from there, remembering what happened. Each scene's picture is
an ordinary Gallery image (so it can be regenerated or saved), made in the background so the text shows at once.
Stories can be read back as a whole, exported as Markdown, and read aloud in the narrator's voice.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from . import activity, crew, db, llm, media

SCENE_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "text": {"type": "string"}, "image_prompt": {"type": "string"},
    "choices": {"type": "array", "items": {"type": "string"}}, "ending": {"type": "boolean"}},
    "required": ["text", "image_prompt", "choices"]}
STYLES = {
    "": "",
    "storybook": "storybook illustration, soft watercolor, warm light",
    "anime": "anime key visual, cel shading, vibrant colors",
    "noir": "black and white film noir, dramatic shadows, rain",
    "scifi": "cinematic sci-fi concept art, volumetric light, detailed",
    "fantasy": "epic fantasy painting, golden hour, highly detailed",
    "pixel": "16-bit pixel art scene",
    "comic": "comic book panel, bold ink lines, halftone",
}
MAX_SCENES = 60


def _story(r: Any, conn: Any, scenes: bool = True) -> dict[str, Any]:
    s = dict(r)
    rows = conn.execute("SELECT * FROM story_scenes WHERE story_id = ? ORDER BY idx", (s["id"],)).fetchall()
    s["scene_count"] = len(rows)
    if scenes:
        s["scenes"] = []
        for row in rows:
            sc = db.row_to_dict(row, ("choices",))
            assert sc is not None
            if sc["media_id"]:
                m = conn.execute("SELECT status, error FROM media WHERE id = ?", (sc["media_id"],)).fetchone()
                sc["image_status"], sc["image_error"] = (m["status"], m["error"]) if m else ("failed", "deleted")
            else:
                sc["image_status"], sc["image_error"] = None, None
            s["scenes"].append(sc)
    else:
        first = conn.execute("SELECT media_id FROM story_scenes WHERE story_id = ? AND media_id IS NOT NULL ORDER BY idx LIMIT 1",
                             (s["id"],)).fetchone()
        s["cover_media_id"] = first["media_id"] if first else None
    return s


def get(sid: int) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM stories WHERE id = ?", (sid,)).fetchone()
        if not r:
            raise KeyError(f"Story {sid} not found")
        return _story(r, conn)


def list_stories() -> list[dict[str, Any]]:
    with db.session() as conn:
        return [_story(r, conn, scenes=False) for r in conn.execute("SELECT * FROM stories ORDER BY id DESC").fetchall()]


def delete(sid: int) -> dict[str, Any]:
    s = get(sid)
    with db.session() as conn:
        conn.execute("DELETE FROM stories WHERE id = ?", (sid,))
    return s


def start(premise: str, style: str = "", narrator: str = "creative", pictures: bool = True) -> dict[str, Any]:
    premise = re.sub(r"\s+", " ", premise or "").strip()
    if len(premise) < 5:
        raise ValueError("Give the story a premise")
    if style not in STYLES and len(style) > 200:
        raise ValueError("Style is too long")
    try:
        crew.get(narrator)
    except KeyError:
        narrator = crew.OVERSEER
    with db.session() as conn:
        cur = conn.execute("INSERT INTO stories(premise, style, narrator, created_at) VALUES (?,?,?,?)",
                           (premise[:2000], style, narrator, db.now()))
        sid = int(cur.lastrowid)
    threading.Thread(target=_next, args=(sid, None, pictures), daemon=True, name=f"lifeos-story-{sid}").start()
    activity.log("story", f"Story {sid} started: {premise[:100]}")
    return get(sid)


def choose(sid: int, choice: str, pictures: bool = True) -> dict[str, Any]:
    choice = re.sub(r"\s+", " ", choice or "").strip()[:400]
    if not choice:
        raise ValueError("Pick a choice or write what happens next")
    s = get(sid)
    if s["status"] == "writing":
        raise ValueError("The next scene is still being written")
    if s["status"] == "ended":
        raise ValueError("This story has ended")
    if not s["scenes"]:
        raise ValueError("The story has no scenes yet")
    if s["scene_count"] >= MAX_SCENES:
        raise ValueError("This story is as long as it can get; start a sequel")
    with db.session() as conn:
        conn.execute("UPDATE story_scenes SET chosen = ? WHERE id = ?", (choice, s["scenes"][-1]["id"]))
        conn.execute("UPDATE stories SET status = 'writing', error = NULL WHERE id = ?", (sid,))
    threading.Thread(target=_next, args=(sid, choice, pictures), daemon=True, name=f"lifeos-story-{sid}").start()
    return get(sid)


def retry(sid: int, pictures: bool = True) -> dict[str, Any]:
    s = get(sid)
    if s["status"] != "failed":
        raise ValueError("Only a failed scene can be retried")
    with db.session() as conn:
        conn.execute("UPDATE stories SET status = 'writing', error = NULL WHERE id = ?", (sid,))
    last = s["scenes"][-1]["chosen"] if s["scenes"] else None
    threading.Thread(target=_next, args=(sid, last, pictures), daemon=True, name=f"lifeos-story-{sid}").start()
    return get(sid)


def _history(s: dict[str, Any]) -> str:
    parts = []
    scenes = s["scenes"]
    for sc in scenes[:-4]:  # older scenes: just the gist
        parts.append(f"Scene {sc['idx'] + 1} (summary): {sc['text'][:220]}… → chose: {sc['chosen'] or '-'}")
    for sc in scenes[-4:]:
        parts.append(f"Scene {sc['idx'] + 1}:\n{sc['text']}\n→ The reader chose: {sc['chosen'] or '-'}")
    return "\n\n".join(parts)[-9000:]


def _next(sid: int, choice: str | None, pictures: bool) -> None:
    try:
        s = get(sid)
        narrator = crew.get(s["narrator"])
        system = (f"You are {narrator['name']}, narrating an interactive story for the user. "
                  + (f"Your voice: {narrator['voice']}. " if narrator.get("voice") else "")
                  + "Write vivid, concrete prose in second person or close third person (keep whichever the story uses), "
                    "150-260 words per scene, with dialogue where it fits. Keep characters, names and facts consistent. "
                    "End each scene at a moment of decision. Keep it suitable for a general audience unless the premise "
                    "clearly asks otherwise. `choices`: exactly 3 short, distinct options (max 12 words each) that lead "
                    "different ways. `image_prompt`: a visual description of the scene for an image model (who/what, "
                    "setting, lighting, mood; no text or speech bubbles; max 60 words). Set `ending` true only for a "
                    "satisfying conclusion, and then give no choices.")
        if not s["scenes"]:
            ask = f"Premise: {s['premise']}\n\nWrite the opening scene. Also give the story a short `title`."
        else:
            ask = (f"Premise: {s['premise']}\n\nThe story so far:\n{_history(s)}\n\nWrite the next scene, following "
                   f"the reader's choice: {choice}"
                   + (" This should be close to the end: start wrapping up." if len(s["scenes"]) >= MAX_SCENES - 3 else ""))
        with llm.track(None, narrator["slug"]):
            data = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": ask}], SCENE_SCHEMA,
                                 model=narrator["effective_model"], timeout=600)
        text = str(data.get("text") or "").strip()
        if len(text) < 40:
            raise ValueError("The narrator didn't write a scene")
        ending = bool(data.get("ending"))
        choices = [re.sub(r"\s+", " ", str(c)).strip()[:120] for c in data.get("choices") or [] if str(c).strip()][:3]
        if not ending and len(choices) < 2:
            choices = (choices + ["Keep going", "Turn back", "Try something unexpected"])[:3]
        image_prompt = re.sub(r"\s+", " ", str(data.get("image_prompt") or "")).strip()[:600]
        idx = len(s["scenes"])
        with db.session() as conn:
            cur = conn.execute("INSERT INTO story_scenes(story_id, idx, text, choices, image_prompt, created_at) "
                               "VALUES (?,?,?,?,?,?)", (sid, idx, text, json.dumps([] if ending else choices), image_prompt, db.now()))
            scene_id = int(cur.lastrowid)
            if idx == 0 and data.get("title"):
                conn.execute("UPDATE stories SET title = ? WHERE id = ?", (str(data["title"])[:120], sid))
            conn.execute("UPDATE stories SET status = ? WHERE id = ?", ("ended" if ending else "ready", sid))
        if pictures and image_prompt:
            picture(scene_id)
    except Exception as exc:  # noqa: BLE001 - shown on the story, with a retry button
        with db.session() as conn:
            conn.execute("UPDATE stories SET status = 'failed', error = ? WHERE id = ?", (f"{type(exc).__name__}: {exc}"[:400], sid))


def picture(scene_id: int) -> dict[str, Any] | None:
    """(Re)make a scene's picture in the background. Returns the media job, or None when generation is off."""
    with db.session() as conn:
        sc = conn.execute("SELECT s.*, st.style AS style, st.narrator AS narrator FROM story_scenes s "
                          "JOIN stories st ON st.id = s.story_id WHERE s.id = ?", (scene_id,)).fetchone()
    if not sc:
        raise KeyError(f"Scene {scene_id} not found")
    style = STYLES.get(sc["style"], sc["style"])
    prompt = sc["image_prompt"] + (f", {style}" if style else "")
    try:
        job = media.generate("image", prompt, size="landscape", agent="photo", wait=False)
    except Exception:  # noqa: BLE001 - generation off or not set up: the story works without pictures
        return None
    with db.session() as conn:
        conn.execute("UPDATE story_scenes SET media_id = ? WHERE id = ?", (job["id"], scene_id))
    return job


def export_markdown(sid: int) -> str:
    s = get(sid)
    out = [f"# {s['title'] or 'Untitled story'}", "", f"_{s['premise']}_", ""]
    for sc in s["scenes"]:
        out += [f"## {sc['idx'] + 1}", "", sc["text"], ""]
        if sc["chosen"]:
            out += [f"> **You chose:** {sc['chosen']}", ""]
    if s["status"] == "ended":
        out.append("*The end.*")
    return "\n".join(out)
