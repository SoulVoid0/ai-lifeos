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


def _clause(clause: str, plan: Plan, ctx: dict[str, Any]) -> bool:
    c = clause.strip()
    low = c.lower()
    refers_back = bool(re.search(r"\b(them|these|those|the ones|ones|it)\b", low))

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

    # summaries and planning
    if re.search(r"\b(summari[sz]e|summary|recap|brief)\b", low) and re.search(r"\b(today|day|important|everything|daily)\b", low):
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
