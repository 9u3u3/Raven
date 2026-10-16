"""Fast path (section 9.10): simple commands match a small rule grammar and need no LLM.

The grammar and the LLM planner give the same abstract plan: a list of steps.

  {"op": "find", "folder": "Sem5", "types": ["docx"], "name": "", "about": ""}
  {"op": "selection"}                          the files selected in the app
  {"op": "convert", "input": 1, "to": "pdf"}   input: the number of an earlier step
  {"op": "move" | "copy", "input": 2, "to": "Submissions"}
  {"op": "rename", "input": 1, "to": "notes-{n}.md"}
  {"op": "mkdir", "to": "Projects/new", "in": ""}
  {"op": "trash", "input": 1}
  {"op": "compress", "input": 1, "to": "7z"}
  {"op": "extract", "input": 1, "to": ""}
"""

from __future__ import annotations

import re

from ..search.filters import TYPE_GROUPS, TYPE_WORDS

VERBS = r"convert|turn|change|move|copy|duplicate|rename|make|create|new|trash|delete|remove|compress|archive|zip|extract|unzip|decompress"
OPS = {
    "convert": "convert", "turn": "convert", "change": "convert",
    "move": "move", "copy": "copy", "duplicate": "copy", "rename": "rename",
    "make": "mkdir", "create": "mkdir", "new": "mkdir",
    "trash": "trash", "delete": "trash", "remove": "trash",
    "compress": "compress", "archive": "compress", "zip": "compress",
    "extract": "extract", "unzip": "extract", "decompress": "extract",
}
FORMATS = {"pdf", "docx", "odt", "md", "markdown", "html", "txt", "epub", "png", "jpg", "jpeg", "webp", "avif", "jxl",
           "mp3", "wav", "flac", "ogg", "opus", "m4a", "mp4", "webm", "mkv", "gif", "csv", "xlsx", "json", "pptx", "rtf"}
ARCHIVES = {"7z": "7z", "zip": "zip", "tar.zst": "tar.zst", "zstd": "tar.zst", "tar": "tar.zst"}
REF = re.compile(r"^(it|them|they|these|those|this|that|the results?|the (?:new|converted) (?:files?|ones)|the (\w+?)s?|selected|the selected(?: files?)?|selection|the selection)$")
EXT_WORD = re.compile(r"^\.?([a-z0-9]{1,5})$")


class OrganizeRequest(Exception):
    """'organize my downloads' is an Organize plan (section 9.5), not an agent command."""


def _split(text: str) -> list[str]:
    """Split "convert X to pdf and move them to Y" into one clause per verb."""
    parts = re.split(rf"\s*(?:,\s*)?(?:\band then\b|\bthen\b|\band\b|;|,)\s+(?=(?:{VERBS})\b)", text)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def _types(words: str) -> list[str]:
    """'docx', 'pdfs', 'word documents', 'images', '.md' -> extensions."""
    out: list[str] = []
    for w in re.split(r"[\s,/]+|\bor\b|\band\b", words):
        w = w.strip().lower()
        if not w or w in ("files", "file", "documents", "the", "all", "every", "my"):
            continue
        if w in TYPE_WORDS:
            out += TYPE_GROUPS[TYPE_WORDS[w]]
        elif w in TYPE_GROUPS:
            out += TYPE_GROUPS[w]
        elif w.rstrip("s") in TYPE_GROUPS:
            out += TYPE_GROUPS[w.rstrip("s")]
        elif w.rstrip("s") in FORMATS or w in FORMATS:
            out.append(w if w in FORMATS else w.rstrip("s"))
        elif w == "word":
            out += ["doc", "docx"]
        else:
            m = EXT_WORD.match(w)
            if m and w.startswith("."):
                out.append(m.group(1))
            else:
                return []
    return list(dict.fromkeys(out))


def _subject(s: str, steps: list[dict]) -> int | None:
    """Turn a noun phrase into a step number. New find steps go into `steps`."""
    s = s.strip().strip("'\"").lower()
    s = re.sub(r"^(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+)?", "", s)
    m = REF.match(s)
    if m and (steps or "select" in s or s in ("this", "these", "it", "them")):
        if steps and "select" not in s:
            return len(steps)
        steps.append({"op": "selection"})
        return len(steps)
    # "<types> (files) (about <topic>) in <folder>"
    m = re.match(r"^(?:the\s+|my\s+)?(.+?)(?:\s+files?)?(?:\s+(?:about|for)\s+(.+?))?\s+(?:in|from|inside|under|within|on)\s+(?:the\s+|my\s+)?(.+?)$", s)
    if m:
        types = _types(m.group(1))
        if types or m.group(1) in ("files", "file", "everything", "items", "the files"):
            steps.append({"op": "find", "folder": m.group(3), "types": types, "name": "", "about": m.group(2) or ""})
            return len(steps)
        if "." in m.group(1) and " " not in m.group(1):
            steps.append({"op": "find", "folder": m.group(3), "types": [], "name": m.group(1), "about": ""})
            return len(steps)
    # "the old-sem3 folder", "folder old-sem3": the folder itself.
    m = re.match(r"^(?:the\s+|my\s+)?(?:folder\s+(.+)|(.+?)\s+folder)$", s)
    if m:
        steps.append({"op": "find", "folder": (m.group(1) or m.group(2)).strip(), "types": [], "name": "", "about": "", "self": True})
        return len(steps)
    # "everything in X" / "files in X" handled above. A plain file name: "report.docx".
    if re.match(r"^[^\s/]+\.[a-z0-9]{1,5}$", s) or "/" in s:
        steps.append({"op": "find", "folder": "", "types": [], "name": s, "about": ""})
        return len(steps)
    # "<types> files" with no folder: the current folder.
    types = _types(re.sub(r"\s+files?$", "", s))
    if types:
        steps.append({"op": "find", "folder": "", "types": types, "name": "", "about": ""})
        return len(steps)
    return None


POLITE = re.compile(r"^(?:(?:hey\s+)?raven,?|please|pls|kindly|can you|could you|would you|will you|i want to|i need to|i'd like to|"
                    r"i would like to|help me|let'?s|go ahead and|just)\s*,?\s+", re.I)
PLACE_FIRST = re.compile(rf"^(?:in|inside|on|under|at|within)\s+(?:the\s+|my\s+)?([~\w./-]+(?:\s+[\w.-]+)?)(?:\s+folder)?\s*,?\s+(?=(?:{VERBS})\b)", re.I)


def normalize(text: str) -> str:
    """"Please, in Desktop make a folder called x" becomes "make a folder called x in Desktop": polite words go,
    and a place said first moves to the end, where the rules below look for it."""
    t = " ".join(text.strip().split()).rstrip(".!")
    for _ in range(3):
        t2 = POLITE.sub("", t).lstrip(", ")
        if t2 == t:
            break
        t = t2
    m = PLACE_FIRST.match(t)
    if m:
        rest, place = t[m.end():], m.group(1)
        # For move, copy or convert, the place is where the files are: it goes before the last "to".
        last_to = list(re.finditer(r"\s+(?:to|into|as)\s+", rest, re.I))
        if last_to and not re.match(r"^(make|create|new)\b", rest, re.I):
            k = last_to[-1].start()
            t = f"{rest[:k]} in {place}{rest[k:]}"
        else:
            t = f"{rest} in {place}"
    return t


def parse(text: str) -> list[dict] | None:
    """Return an abstract plan, or None when the command is not simple enough."""
    t = " ".join(text.strip().split())
    t = normalize(t)
    if re.match(r"^(organi[sz]e|tidy|clean up|sort)\b", t, re.I):
        raise OrganizeRequest(t)
    clauses = _split(t)
    if not clauses:
        return None
    steps: list[dict] = []
    for c in clauses:
        low = c.lower()
        m = re.match(rf"^({VERBS})\b\s*(.*)$", low)
        if not m:
            return None
        op = OPS[m.group(1)]
        rest = c[m.end(1):].strip()
        rl = rest.lower()
        if op == "mkdir":
            m2 = re.match(r"^(?:a\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:called\s+|named\s+)?(.+?)"
                          r"(?:\s+(?:in|inside|under|on|at)\s+(?:the\s+|my\s+)?(.+?))?(?:\s+folder)?$", rest, re.I)
            if not m2:
                return None
            steps.append({"op": "mkdir", "to": m2.group(1).strip("'\" "), "in": (m2.group(2) or "").strip("'\" ")})
            continue
        if op == "convert":
            m2 = re.match(r"^(.+?)\s+(?:to|into|as)\s+(?:an?\s+)?\.?([a-z0-9.]+)(?:\s+files?|\s+format)?$", rl)
            if not m2:
                return None
            fmt = {"markdown": "md", "jpeg": "jpg", "word": "docx"}.get(m2.group(2), m2.group(2))
            if fmt not in FORMATS:
                return None
            src = _subject(m2.group(1), steps)
            if src is None:
                return None
            steps.append({"op": "convert", "input": src, "to": fmt})
            continue
        if op in ("move", "copy", "rename"):
            # The last " to " splits source and target: "move the pdfs in Downloads to Documents/Sem5".
            m2 = re.match(r"^(.+)\s+(?:to|into|as)\s+(.+)$", rest, re.I) or re.match(r"^(.+?)\s+in\s+(.+)$", rest, re.I)
            if not m2:
                return None
            src = _subject(m2.group(1), steps)
            if src is None:
                return None
            dst = re.sub(r"^(?:the\s+|my\s+)", "", m2.group(2).strip("'\" "), flags=re.I)
            dst = re.sub(r"\s+folder$", "", dst, flags=re.I)
            # "to a new folder named Text": the name is "Text".
            dst = re.sub(r"^(?:an?\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:named|called)\s+", "", dst, flags=re.I)
            steps.append({"op": op, "input": src, "to": dst})
            continue
        if op == "trash":
            src = _subject(rl, steps)
            if src is None:
                return None
            steps.append({"op": "trash", "input": src})
            continue
        if op == "compress":
            m2 = re.match(r"^(.+?)(?:\s+(?:as|to|into|with|using)\s+(?:an?\s+)?(7z|zip|tar\.zst|zstd|tar)(?:\s+archive)?)?$", rl)
            if not m2:
                return None
            src = _subject(m2.group(1), steps)
            if src is None:
                return None
            default = "zip" if m.group(1) == "zip" else "7z"
            steps.append({"op": "compress", "input": src, "to": ARCHIVES.get(m2.group(2) or "", default)})
            continue
        if op == "extract":
            m2 = re.match(r"^(.+?)(?:\s+(?:to|into|in)\s+(.+))?$", rest, re.I)
            src = _subject(m2.group(1), steps) if m2 else None
            if src is None:
                return None
            steps.append({"op": "extract", "input": src, "to": (m2.group(2) or "").strip("'\" ")})
            continue
        return None
    return steps or None
