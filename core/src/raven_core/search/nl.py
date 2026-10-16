"""Natural-language filters with the local LLM (section 9.3).

The rule parser (filters.py) runs first. The LLM runs only when words that look like a filter are left
over ("two weeks ago", "since January", "my DBMS notes"). Its JSON becomes filter tokens that the rule
parser already reads (`type:pdf,docx`, `after:2026-01-01`, `>2MB`, `in:Downloads`). So the user sees the
same chips, can remove any of them, and every search path runs the same code.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime

from .filters import parse

log = logging.getLogger("raven.nl")

HINT = re.compile(
    r"\b(ago|since|before|after|between|until|recent(ly)?|older|newer|latest|earlier|week|weeks|month|months|year|years|"
    r"days?|yesterday|today|semester|sem|summer|winter|diwali|holidays?|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|"
    r"june?|july?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|20\d\d|big|bigger|large|larger|huge|small|"
    r"smaller|tiny|mb|gb|kb|notes|slides|ppt|deck|sheets?|excel|word|docs?|documents?|pdfs?|photos?|images?|videos?|"
    r"recordings?|audio|music|songs?|code|scripts?|downloads|desktop|documents)\b",
    re.I,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "semantic_query": {"type": "string"},
        "file_types": {"type": "array", "items": {"type": "string"}},
        "modified_after": {"type": "string"},
        "modified_before": {"type": "string"},
        "min_size_bytes": {"type": "integer"},
        "max_size_bytes": {"type": "integer"},
        "folder": {"type": "string"},
    },
    "required": ["semantic_query", "file_types", "modified_after", "modified_before", "min_size_bytes", "max_size_bytes", "folder"],
}

SYSTEM = """You turn a file search query into JSON filters. Today is {today}.
Rules:
- semantic_query: the topic words only, without dates, sizes, types or folders. Add one or two close synonyms or the full form of an abbreviation ("DBMS" -> "DBMS database management systems").
- file_types: lower-case file extensions, only when the query names a kind of file ("notes" -> ["pdf","docx","md","txt"], "slides" -> ["pptx","pdf"], "photos" -> ["jpg","jpeg","png","heic"]). Else [].
- modified_after / modified_before: ISO dates YYYY-MM-DD, only when the query names a time. Else "".
  The odd semester is July to December. The even semester is January to June.
- min_size_bytes / max_size_bytes: only when the query names a size. Else 0.
- folder: a folder name only when the query names one ("in my downloads" -> "Downloads"). Else "".
Do not invent filters that the query does not state."""

EXT_OK = re.compile(r"^[a-z0-9]{1,6}$")


def needs_llm(q: str) -> bool:
    """True when the rule parser leaves words that look like a filter."""
    f = parse(q)
    return bool(HINT.search(f.text))


def _iso(s: str) -> str | None:
    s = (s or "").strip()
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return None
    # A model that misreads a year can give dates far away. Keep a sane range.
    if not (1990 <= d.year <= datetime.now().year + 1):
        return None
    return d.isoformat()


def tokens_from(out: dict) -> tuple[str, list[str]]:
    """The LLM JSON as (text, filter tokens). Bad values are dropped, not guessed."""
    toks: list[str] = []
    exts = [e.lower().lstrip(".") for e in out.get("file_types") or [] if isinstance(e, str)]
    exts = [e for e in exts if EXT_OK.match(e)][:12]
    if exts:
        toks.append("type:" + ",".join(dict.fromkeys(exts)))
    a, b = _iso(out.get("modified_after", "")), _iso(out.get("modified_before", ""))
    if a and b and a >= b:
        a = b = None
    if a:
        toks.append(f"after:{a}")
    if b:
        toks.append(f"before:{b}")
    lo, hi = int(out.get("min_size_bytes") or 0), int(out.get("max_size_bytes") or 0)
    if lo > 0:
        toks.append(f">{max(1, lo // 1024)}KB")
    if hi > 0 and hi > lo:
        toks.append(f"<{max(1, hi // 1024)}KB")
    folder = (out.get("folder") or "").strip().strip("/")
    if folder and " " not in folder and len(folder) < 60:
        toks.append(f"in:{folder}")
    text = " ".join((out.get("semantic_query") or "").split())[:200]
    return text, toks


def llm_parse(q: str) -> dict:
    """Return {"query": the rewritten query, "chips", "ms", "used": bool}. Falls back to the rule parser."""
    from ..router import local

    t0 = time.perf_counter()
    rule = parse(q)
    if not needs_llm(q):
        return {"query": q, "chips": rule.chips, "ms": 0, "used": False}
    if not local.available():
        return {"query": q, "chips": rule.chips, "ms": 0, "used": False, "error": "The local model is not ready"}
    try:
        out = local.chat(SYSTEM.format(today=date.today().isoformat()), q, SCHEMA, max_tokens=160)
    except Exception as e:  # noqa: BLE001
        log.info("nl parse failed: %s", e)
        return {"query": q, "chips": rule.chips, "ms": round((time.perf_counter() - t0) * 1000), "used": False, "error": str(e)[:200]}
    text, toks = tokens_from(out)
    # The rule parser keeps the filters it found itself. The LLM adds the ones it did not.
    # A plain type word ("photos") is a chip and also stays in the text. Do not write it twice.
    keep = [c["token"] for c in rule.chips if not (c["kind"] == "type" and ":" not in c["token"])]
    have = {c["kind"] for c in rule.chips}
    extra = [t for t in toks if not (
        (t.startswith("type:") and "type" in have) or (t.startswith(("after:", "before:")) and "date" in have)
        or (t.startswith((">", "<")) and "size" in have) or (t.startswith("in:") and "folder" in have))]
    rewritten = " ".join([text or rule.text, *keep, *extra]).strip()
    f = parse(rewritten)
    return {"query": rewritten, "chips": f.chips, "text": f.text, "ms": round((time.perf_counter() - t0) * 1000), "used": True}
