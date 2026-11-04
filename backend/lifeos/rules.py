"""Deterministic planner for common requests.

Follows the spec's hybrid principle: simple, recognizable commands are handled by normal
code (fast, predictable, works offline); anything it cannot fully parse goes to the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import permissions

VERBS = ("remind", "organize", "organise", "sort", "tidy", "clean", "find", "search", "look", "locate", "create",
         "add", "summarize", "summarise", "show", "list", "remember", "plan", "move", "open", "mark", "complete",
         "schedule", "what", "where", "forget", "tell", "give", "help", "write", "explain", "check", "prepare",
         "make", "set", "start", "delete", "rename", "send", "email", "draft", "translate", "how", "why", "who")

CLAUSE_SPLIT = re.compile(r"(?:,\s*(?:and then|then|and also|and|also)?\s*|\s+(?:and then|then|and also|and|also)\s+)(?=(?:" + "|".join(VERBS) + r")\b)"
                          r"|[.;!?]\s+|\n+", re.I)

EXT_WORDS: dict[str, list[str]] = {
    r"pdfs?": ["pdf"],
    r"word (?:docs?|documents?)|docx": ["doc", "docx"],
    r"images?|photos?|pictures?|pics|screenshots?": ["jpg", "jpeg", "png", "gif", "webp", "heic"],
    r"videos?|movies?": ["mp4", "mov", "mkv", "avi", "webm"],
    r"music|songs?|audio": ["mp3", "wav", "flac", "m4a"],
    r"spreadsheets?|excel": ["xls", "xlsx", "csv", "ods"],
    r"presentations?|slides?|powerpoints?": ["ppt", "pptx", "key"],
    r"zips?|archives?": ["zip", "rar", "7z", "tar", "gz"],
    r"installers?": ["exe", "msi", "dmg", "pkg", "deb"],
    r"text files?|notes?": ["txt", "md"],
}

TIME_PHRASE = re.compile(
    r"\b(?:(?:on |by |at |next |this )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?: (?:morning|afternoon|evening|night))?|day after tomorrow|tomorrow|tonight|today|next week|this week(?:end)?|"
    r"end of (?:the )?week|in (?:\d+|an?|one|two|three) (?:minute|min|hour|hr|day|week|month)s?|"
    r"(?:at |by )?\d{1,2}(?::\d{2})? ?(?:am|pm)|at \d{1,2}:\d{2}|this (?:morning|afternoon|evening)|"
    r"(?:in the )?(?:morning|afternoon|evening)|(?:on )?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(?:st|nd|rd|th)?)\b",
    re.I,
)

STOP = {"find", "search", "for", "look", "locate", "show", "me", "my", "the", "a", "an", "all", "any", "in", "on",
        "from", "of", "that", "i", "file", "files", "folder", "document", "documents", "about", "where", "is", "are",
        "did", "do", "was", "which", "what", "with", "downloaded", "saved", "worked", "on", "please", "can", "you",
        "get", "some", "few", "months", "ago", "last", "week", "month", "year", "yesterday", "recently", "there",
        "it", "to", "and", "or", "folders", "this"}


@dataclass
class Plan:
    steps: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    default_when: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.steps) and not self.unmatched


def split_clauses(text: str) -> list[str]:
    parts = [p.strip(" ,.") for p in CLAUSE_SPLIT.split(text.strip()) if p and p.strip(" ,.")]
    return parts or [text.strip()]


def _extensions(text: str) -> list[str] | None:
    exts: list[str] = []
    for pattern, values in EXT_WORDS.items():
        if re.search(rf"\b(?:{pattern})\b", text, re.I):
            exts.extend(values)
    m = re.findall(r"\.([a-z0-9]{2,5})\b(?! ?(?:com|org|net))", text, re.I)
    exts.extend(e.lower() for e in m)
    return sorted(set(exts)) or None


def _folder(text: str) -> str | None:
    for _, root in permissions.approved_roots():
        if re.search(rf"\b{re.escape(root.name)}\b", text, re.I):
            return root.name
    m = re.search(r"\b(?:in|from|inside|of)\s+(?:my |the )?([\w\- ]{2,30}?)\s+(?:folder|directory|dir)\b", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:my |the )([\w\-]{2,30})\s+(?:folder|directory)\b", text, re.I)
    return m.group(1) if m else None


def _time_window(text: str) -> dict[str, Any]:
    t = text.lower()
    if "yesterday" in t or re.search(r"\blast (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        return {"modified_within_days": 10}
    if "last week" in t or "this week" in t:
        return {"modified_within_days": 14}
    if "last month" in t or "few weeks" in t:
        return {"modified_within_days": 62}
    if re.search(r"(few|couple of|\d+) months? ago", t):
        return {"modified_within_days": 365}
    if "today" in t:
        return {"modified_within_days": 1}
    return {}


def _query_terms(text: str, exts: list[str] | None, folder: str | None) -> str:
    t = text.lower()
    for pattern in EXT_WORDS:
        t = re.sub(rf"\b(?:{pattern})\b", " ", t)
    if folder:
        t = re.sub(rf"\b{re.escape(folder.lower())}\b", " ", t)
    t = re.sub(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|ago)\b", " ", t)
    words = [w for w in re.findall(r"[a-z0-9+#]+", t) if w not in STOP and len(w) > 1]
    return " ".join(words)


def _strip_time(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TIME_PHRASE.sub(" ", text)).strip(" ,.")


def _when(text: str) -> str | None:
    found = [m.group(0) for m in TIME_PHRASE.finditer(text)]
    return " ".join(found) if found else None


def _title(text: str) -> str:
    title = _strip_time(text)
    title = re.sub(r"^(?:to|that|about)\s+", "", title, flags=re.I)
    title = re.sub(r"\b(?:the )?important ones\b", "the important files", title, flags=re.I)
    return (title[:1].upper() + title[1:])[:200] if title else "Reminder"


_DEVICE = r"(?:my\s+|the\s+|this\s+)?(?:pc|computer|laptop|machine|system|device)"
_NOT_APPS = {"it", "that", "this", "them", "file", "files", "folder", "folders", "the file", "my files", "a file", "up"}


def _system_clause(c: str, low: str, plan: Plan) -> bool:
    power = None
    if re.search(rf"\block\s+(?:{_DEVICE}|(?:my\s+|the\s+)?screen)\b", low):
        power = "lock"
    elif re.search(rf"\b(?:restart|reboot)\s+{_DEVICE}\b", low) or low.strip(" .!") in ("restart", "reboot"):
        power = "restart"
    elif re.search(rf"\b(?:shut\s*down|turn\s+off|power\s+off)\s+{_DEVICE}\b", low) or low.strip(" .!") in ("shut down", "shutdown"):
        power = "shutdown"
    elif re.search(rf"\b(?:put\s+{_DEVICE}\s+to\s+sleep|sleep\s+{_DEVICE})\b", low):
        power = "sleep"
    elif re.search(r"\b(?:sign|log)\s+(?:me\s+)?out\b", low) and not re.search(r"\b(?:of|from)\b", low):
        power = "sign_out"
    if power:
        plan.steps.append({"tool": "power_action", "args": {"action": power}})
        return True

    if re.search(r"\b(?:volume|sound|speakers?)\b", low) or re.fullmatch(r"(?:un)?mute(?: the sound| it| audio)?[.!]?", low.strip()):
        m = re.search(r"\b(\d{1,3})\s*%?", low)
        if re.search(r"\b(?:un)?mute\b", low):
            plan.steps.append({"tool": "set_volume", "args": {"change": "mute"}})
        elif m:
            plan.steps.append({"tool": "set_volume", "args": {"level": min(100, int(m.group(1)))}})
        elif re.search(r"\b(?:up|louder|increase|raise|higher)\b", low):
            plan.steps.append({"tool": "set_volume", "args": {"change": "up"}})
        elif re.search(r"\b(?:down|quieter|lower|decrease|reduce)\b", low):
            plan.steps.append({"tool": "set_volume", "args": {"change": "down"}})
        else:
            return False
        return True

    if re.search(r"\b(?:wi-?fi|wireless|internet connection)\b", low):
        m = re.search(r"\bconnect(?: me)? to\s+(?:the\s+)?(?:wi-?fi\s+)?[\"']?([^\"']+?)[\"']?(?:\s+wi-?fi)?(?:\s+(?:with\s+)?(?:the\s+)?password\s+(\S+))?[.!]?$", c, re.I)
        if m and not re.fullmatch(r"(?:the\s+)?(?:wi-?fi|internet|network)", m.group(1).strip(), re.I):
            plan.steps.append({"tool": "wifi", "args": {"action": "connect", "ssid": m.group(1).strip(),
                                                         "password": m.group(2) or ""}})
        elif re.search(r"\bturn\s+(?:the\s+)?wi-?fi\s+off\b|\bturn\s+off\s+(?:the\s+)?wi-?fi\b|\bdisable\s+wi-?fi\b", low):
            plan.steps.append({"tool": "wifi", "args": {"action": "off"}})
        elif re.search(r"\bturn\s+(?:the\s+)?wi-?fi\s+on\b|\bturn\s+on\s+(?:the\s+)?wi-?fi\b|\benable\s+wi-?fi\b", low):
            plan.steps.append({"tool": "wifi", "args": {"action": "on"}})
        elif re.search(r"\bdisconnect\b", low):
            plan.steps.append({"tool": "wifi", "args": {"action": "disconnect"}})
        elif re.search(r"\b(?:networks?|list|show|scan|available|nearby)\b", low):
            plan.steps.append({"tool": "wifi", "args": {"action": "list"}})
        elif re.search(r"\b(?:am i|connected|status|which|what)\b", low):
            plan.steps.append({"tool": "wifi", "args": {"action": "status"}})
        else:
            return False
        return True

    if re.search(r"\bbluetooth\b|\b(?:headphones|earbuds|airpods|headset|controller)\b", low) and \
            re.search(r"\b(?:bluetooth|connect|disconnect|pair)\b", low):
        m = re.search(r"\b(connect|disconnect|pair|forget)\s+(?:to\s+|with\s+)?(?:my\s+|the\s+)?(.+?)(?:\s+(?:via|over|with)\s+bluetooth|\s+bluetooth)?[.!]?$", c, re.I)
        if re.search(r"\bturn\s+(?:on\s+)?bluetooth(?:\s+on)?\b|\benable\s+bluetooth\b", low) and "off" not in low:
            plan.steps.append({"tool": "bluetooth", "args": {"action": "on"}})
        elif re.search(r"\bturn\s+(?:off\s+)?bluetooth(?:\s+off)?\b|\bdisable\s+bluetooth\b", low):
            plan.steps.append({"tool": "bluetooth", "args": {"action": "off"}})
        elif m and m.group(2).strip().lower() not in ("bluetooth", "a device", "devices"):
            plan.steps.append({"tool": "bluetooth", "args": {"action": m.group(1).lower(), "device": m.group(2).strip()}})
        elif re.search(r"\b(?:scan|search|find|nearby|pair)\b", low):
            plan.steps.append({"tool": "bluetooth", "args": {"action": "scan"}})
        else:
            plan.steps.append({"tool": "bluetooth", "args": {"action": "list"}})
        return True

    if re.search(r"\bbrightness\b|\bscreen\s+(?:brighter|dimmer)\b", low):
        m = re.search(r"\b(\d{1,3})\s*%?", low)
        if not m:
            return False
        plan.steps.append({"tool": "set_brightness", "args": {"level": max(1, min(100, int(m.group(1))))}})
        return True

    if re.search(r"\b(?:what'?s|what is|which (?:apps?|programs?) (?:are|is))\s+(?:using|eating|hogging|slowing)\b", low) \
            or re.search(r"\b(?:running programs|task manager|running apps|what'?s running)\b", low):
        plan.steps.append({"tool": "list_programs", "args": {"sort": "cpu" if re.search(r"\bcpu|slow", low) else "memory"}})
        return True
    if re.search(r"\b(?:system status|pc status|computer status)\b", low) \
            or re.search(rf"\bhow'?s\s+{_DEVICE}|\bhow is\s+{_DEVICE}", low) \
            or (re.search(r"\b(?:battery|cpu|ram|disk space|free space|storage)\b", low)
                and re.search(r"\b(?:how|what'?s|what is|check|show|status|usage|level|left|much)\b", low)
                and not re.search(r"\b(?:find|search|files?|documents?|pdfs?)\b", low)):
        plan.steps.append({"tool": "system_status", "args": {}})
        return True

    m = re.match(r"^(?:please\s+)?(?:kill|force[- ]?(?:close|quit)|end task(?: for)?|close|quit)\s+(?:the\s+)?(.+?)(?:\s+(?:app|program))?[.!]?$", c, re.I)
    if m:
        from . import system

        name = m.group(1).strip()
        if name.lower() not in _NOT_APPS and system._matching(name):
            plan.steps.append({"tool": "end_program", "args": {"name": name}})
            return True
        return False

    m = re.match(r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:up\s+)?(?:the\s+)?(.+?)(?:\s+(?:app|application|program))?[.!]?$", c, re.I)
    if m and not re.search(r"[\\/]|\b(?:file|files|folder|pdf|document|downloads|workspace|mission|task)\b", m.group(1), re.I):
        from . import apps

        name = m.group(1).strip()
        if len(name) >= 3 and name.lower() not in _NOT_APPS:
            app = apps.find(name)
            if app:
                plan.steps.append({"tool": "launch_app", "args": {"name": app["name"]}})
                return True
    return False


def _clause(clause: str, plan: Plan, ctx: dict[str, Any]) -> bool:
    c = clause.strip()
    low = c.lower()
    refers_back = bool(re.search(r"\b(them|these|those|the ones|ones|it)\b", low))

    # images and video ("make a picture of...", "generate a 5 second video of...")
    m = re.match(r"^(?:please )?(?:can you |could you )?(?:generate|make|create|draw|paint|render|design)\s+(?:me\s+)?"
                 r"(?:an?\s+|some\s+|\d+\s+)?(?:(\d+)[- ]?(?:s|sec|secs|second|seconds)\s+)?"
                 r"(video|clip|animation|movie|image|picture|photo|illustration|poster|wallpaper|drawing|painting|art(?:work)?|logo)s?"
                 r"\b(?:\s+(of|showing|with|for|about))?\s*(.*)$", c, re.I)
    if m and len(m.group(4).strip()) >= 3:
        noun = m.group(2).lower()
        video = noun in ("video", "clip", "animation", "movie")
        subject = m.group(4).strip().rstrip(".!")
        # "a poster for my band" keeps the kind of picture; "an image of a cat" is just "a cat".
        if noun not in ("video", "clip", "movie", "image", "picture", "photo"):
            subject = " ".join(x for x in (noun, m.group(3), subject) if x)
        args = {"prompt": subject}
        if video and m.group(1):
            args["seconds"] = max(1, min(int(m.group(1)), 10))
        plan.steps.append({"tool": "generate_video" if video else "generate_image", "args": args})
        return True

    # remember / forget
    m = re.match(r"^(?:please )?(?:remember|note|keep in mind)(?: that)?\s+(.+)$", c, re.I)
    if m and not low.startswith("remind"):
        content = m.group(1).strip()
        kind = "preference" if re.search(r"\b(i prefer|i like|i love|i hate|i don't like|always|never|my favou?rite)\b", content, re.I) else \
            "project" if re.search(r"\b(project|repo|working on)\b", content, re.I) else "fact"
        plan.steps.append({"tool": "save_memory", "args": {"content": content, "kind": kind}})
        return True

    # reminders and tasks
    m = re.match(r"^(?:please )?remind me(?: to| about| that)?\s+(.+)$", c, re.I)
    if m:
        body = m.group(1)
        when = _when(body) or plan.default_when
        args: dict[str, Any] = {"title": _title(body), "remind": when or "in 1 hour"}
        if refers_back or re.search(r"\b(files?|pdfs?|documents?|important)\b", body, re.I):
            args["include_important_files"] = True
        plan.steps.append({"tool": "create_task", "args": args})
        return True
    m = re.match(r"^(?:please )?(?:add|create|make)(?: a| an)?(?: new)? (?:task|todo|to-do|reminder)(?: to| for| called)?:?\s+(.+)$", c, re.I)
    if m:
        body = m.group(1)
        due_m = re.search(r"\b(?:due|by|before)\s+(.+)$", body, re.I)
        args = {"title": _title(re.sub(r"\b(?:due|by|before)\s+.+$", "", body, flags=re.I) or body)}
        if due_m:
            args["due"] = due_m.group(1)
        elif _when(body):
            args["due"] = _when(body)
        if re.search(r"\b(urgent|asap|important|high priority)\b", body, re.I):
            args["priority"] = "high"
        plan.steps.append({"tool": "create_task", "args": args})
        return True
    m = re.match(r"^(?:please )?(?:mark|complete|finish|check off|tick off)\s+(.+?)(?:\s+as)?(?:\s+(?:done|complete|completed|finished))?$", c, re.I) \
        or re.match(r"^i (?:finished|completed|did|have done)\s+(.+)$", c, re.I)
    if m:
        plan.steps.append({"tool": "complete_task", "args": {"title": m.group(1)}})
        return True

    # workspaces: "prepare my workspace for studying", "open my coding workspace"
    m = re.search(r"\b(?:prepare|open|start|set up|load|launch)\s+(?!(?:my\s+|the\s+)?workspace\b)(?:my\s+|the\s+)?(.+?)\s+workspace\b", low) \
        or re.search(r"\b(?:prepare|open|start|set up|setup|load|launch)\b.*?\bworkspace\b(?:\s+(?:for|to)\s+(.+))?", low)
    if m:
        from . import workspaces

        wanted = (m.group(1) or "").strip(" .")
        w = workspaces.find(wanted) if wanted else None
        if w is None:
            # "prepare my workspace for studying" matches a workspace named "study" or "studying".
            stem = re.sub(r"(ing|y|ies)$", "", wanted)
            w = next((x for x in workspaces.list_all() if stem and stem in x["name"].lower()), None)
        if w is None:
            return False  # no such workspace: let the model (or the help text) answer
        plan.steps.append({"tool": "open_workspace", "args": {"name": w["name"]}})
        return True

    # renaming by content
    if re.search(r"\brename\b", low) and re.search(r"\b(content|contents|what'?s in|based on|meaningful|properly|sensible|scans?|files)\b", low):
        folder = _folder(c) or ctx.get("last_folder")
        if folder:
            args = {"folder": folder}
            if _extensions(c):
                args["extensions"] = _extensions(c)
            plan.steps.append({"tool": "rename_by_content", "args": args})
            return True

    # breaking work down: "break my history essay into steps", "split the project into smaller tasks"
    m = re.search(r"\b(?:break|split|divide)\s+(?:down\s+)?(?:my\s+|the\s+)?(.+?)\s+(?:down\s+)?into\s+(?:smaller\s+)?"
                  r"(?:steps|tasks|parts|pieces|chunks)\b", low) or re.search(r"\bbreak down\s+(?:my\s+|the\s+)?(.+)$", low)
    if m:
        plan.steps.append({"tool": "break_down_task", "args": {"title": m.group(1).strip(" .")}})
        return True
    if re.search(r"\b(i'?m|i am|fell|falling|got)\s+behind\b", low):
        m = re.search(r"\bbehind (?:on|with)\s+(?:my\s+|the\s+)?(.+)$", low)
        if m:
            plan.steps.append({"tool": "replan_task", "args": {"title": m.group(1).strip(" .")}})
            return True

    # the computer: power, volume, status, programs, apps
    if _system_clause(c, low, plan):
        return True

    # summaries and planning
    if re.search(r"\b(summari[sz]e|summary|recap|brief)\b", low) and re.search(r"\b(today|day|important|everything|daily|tasks|completed|done)\b", low):
        plan.steps.append({"tool": "daily_summary", "args": {}})
        return True
    if re.search(r"\b(plan|schedule)\b.*\b(day|today|tasks|assignments|work|study)\b", low) or \
            re.search(r"\bwhat should i (do|work on)\b", low):
        hours = re.search(r"(\d+(?:\.\d+)?) ?h(?:ours?)?", low)
        plan.steps.append({"tool": "plan_day", "args": {"available_hours": float(hours.group(1))} if hours else {}})
        return True
    if re.search(r"\b(my|open|upcoming|pending)?\s*(tasks|to-?dos|deadlines|reminders)\b", low) and \
            re.search(r"\b(show|list|what|which|any|see)\b", low):
        args = {"due_within_days": 7} if "deadline" in low or "upcoming" in low or "this week" in low else {}
        plan.steps.append({"tool": "list_tasks", "args": args})
        return True

    # file organization
    if re.search(r"\b(organi[sz]e|sort|tidy|clean up|cleanup|declutter|arrange)\b", low):
        folder = _folder(c) or (ctx.get("last_folder") if refers_back or not _folder(c) else None)
        exts = _extensions(c) or (ctx.get("last_exts") if refers_back else None)
        if not folder:
            return False
        strategy = "type" if re.search(r"\bby (file )?type\b", low) else "date" if re.search(r"\bby (date|month|year)\b", low) else "category"
        args = {"folder": folder, "strategy": strategy}
        if exts:
            args["extensions"] = exts
        plan.steps.append({"tool": "organize_files", "args": args})
        ctx["last_folder"], ctx["last_exts"] = folder, exts
        return True

    # file finding
    if re.search(r"\b(find|search|look for|locate|where is|where are|show me|list|which)\b", low) or \
            re.search(r"\b(pdfs?|files?|documents?|images?|photos?)\b", low):
        if "duplicate" in low:
            plan.steps.append({"tool": "find_duplicates", "args": {"folder": _folder(c)} if _folder(c) else {}})
            return True
        if re.search(r"\b(large|big|huge|biggest|largest)\b", low):
            args = {}
            mb = re.search(r"(\d+) ?(mb|gb)", low)
            if mb:
                args["min_mb"] = float(mb.group(1)) * (1024 if mb.group(2) == "gb" else 1)
            if re.search(r"(haven'?t|not|never) (used|opened|touched)|unused|old", low):
                args["older_than_days"] = 90
            plan.steps.append({"tool": "find_large_files", "args": args})
            return True
        if re.search(r"(haven'?t|not|never) (used|opened|touched)|forgotten|\bold (files|documents)\b|archiv", low):
            plan.steps.append({"tool": "find_old_files", "args": {}})
            return True
        if re.search(r"\b(folders)\b", low) and re.search(r"\b(approved|allowed|access)\b", low):
            plan.steps.append({"tool": "list_folders", "args": {}})
            return True
        folder = _folder(c)
        exts = _extensions(c)
        args: dict[str, Any] = {"query": _query_terms(c, exts, folder)}
        if folder:
            args["folder"] = folder
        if exts:
            args["extensions"] = exts
        args.update(_time_window(c))
        if not args["query"] and not exts and not folder and "modified_within_days" not in args:
            return False
        if "all" in low.split():
            args["limit"] = 200
        plan.steps.append({"tool": "search_files", "args": args})
        ctx["last_folder"], ctx["last_exts"] = folder, exts
        return True

    # recall
    m = re.search(r"\b(?:what (?:do you know|did i (?:say|find|decide|write))|what was|do you remember|recall)\b\s*(.*)$", low)
    if m:
        plan.steps.append({"tool": "search_everything", "args": {"query": _query_terms(m.group(1) or c, None, None) or c}})
        return True
    return False


def plan(text: str) -> Plan:
    p = Plan()
    ctx: dict[str, Any] = {}
    clauses = split_clauses(text)
    # A time phrase in any clause ("...tomorrow") applies to reminders that lack their own.
    p.default_when = _when(text)
    for clause in clauses:
        if not _clause(clause, p, ctx):
            p.unmatched.append(clause)
    return p
