"""The redaction gate (section 10.3) and the privacy ledger (section 10.4).

Steps for every cloud payload:
 1. The caller builds the minimum payload.
 2. Cached spans from index time apply first (fast).
 3. Layer 1 runs again on the whole payload.
 4. Layer 2 runs on text that has no cached spans.
 5. Each entity becomes a typed placeholder: [PERSON_1], [ACCOUNT_1]. The map stays in memory.
 6. Final check: layer 1 scans the redacted payload. A high-risk entity left means block.
    seal() runs the check and gives a Sealed payload. A provider adapter takes only a Sealed payload (PLAN4 4).
 7. The payload goes to the ledger.
 8. The router sends it.
 9. restore() puts the original values back into the answer, locally.

Fail closed (PLAN4 7.5): without a working Privacy Filter, seal() refuses, and the local model answers.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .. import db, events
from .model import model as layer2
from .rules import HIGH_RISK, Span, custom_spans, detect, resolve

PLACEHOLDER = re.compile(r"\[([A-Z]+)_(\d+)\]")
NAME_TYPES = {"PERSON", "EMAIL", "PHONE", "ADDRESS", "SECRET"}
NOT_READY = "The Privacy Filter is not ready, so nothing goes to the cloud. Use the local model, or wait."
NOT_INSTALLED = ("The Privacy Filter is not installed, so nothing goes to the cloud. "
                 "Add it in Settings, Features and models, or use the local model.")


class Blocked(Exception):
    """The gate refuses a payload. The message says why, with no original value in it."""


class Sealed(str):
    """A cloud payload that passed seal(). Only seal() makes one. A string operation on it gives a plain str,
    so a caller cannot add text after the check."""


_words = {"at": 0.0, "words": []}


def custom_words() -> list[str]:
    """The user's own words to always redact (PLAN4 7.4), cached for 5 s."""
    now = time.monotonic()
    if now - _words["at"] > 5:
        raw = db.get_setting("privacy_words") or []
        _words.update(at=now, words=[w.strip() for w in raw if isinstance(w, str) and len(w.strip()) >= 2])
    return _words["words"]


def forget_words_cache() -> None:
    _words["at"] = 0.0


def layer2_ready() -> str | None:
    """None when the Privacy Filter can run, else the reason it cannot."""
    if not layer2.available():
        return NOT_INSTALLED
    if layer2.error:
        return NOT_READY
    return None


@dataclass
class Redaction:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original, in memory only
    entities: list[dict] = field(default_factory=list)  # [{type, placeholder, layer}], no originals
    blocked: bool = False
    reason: str = ""
    layer2: str = "off"
    ms: float = 0.0

    def public(self) -> dict:
        return {"text": self.text, "entities": self.entities, "blocked": self.blocked, "reason": self.reason,
                "layer2": self.layer2, "ms": round(self.ms, 1), "count": len(self.mapping)}


class Redactor:
    """One redactor per request, so the same value gets the same placeholder in every part."""

    def __init__(self, use_layer2: bool = True) -> None:
        self.mapping: dict[str, str] = {}
        self.reverse: dict[tuple[str, str], str] = {}
        self.counts: dict[str, int] = {}
        self.entities: list[dict] = []
        self.use_layer2 = use_layer2
        self.layer2_state = "off"

    def _placeholder(self, typ: str, value: str) -> str:
        key = (typ, value.strip().lower())
        if key in self.reverse:
            return self.reverse[key]
        self.counts[typ] = self.counts.get(typ, 0) + 1
        ph = f"[{typ}_{self.counts[typ]}]"
        self.reverse[key] = ph
        self.mapping[ph] = value
        return ph

    def redact(self, text: str, cached: list[Span] | None = None, cached_layer2: bool = False, name: bool = False) -> str:
        """`cached_layer2`: the cached spans already hold the layer-2 result for this text.
        `name`: the text is a file or folder name. Layer 2 reads names such as notes_v2.md as URLs
        or account numbers, so for names it keeps only people, contacts and addresses."""
        spans = list(cached or [])
        spans += detect(text)
        spans += custom_spans(text, custom_words())
        if self.use_layer2 and not cached_layer2:
            if layer2.available():
                if name:
                    # The model reads "Rahul_Sharma_resume.txt" as a URL and misses lowercase names.
                    # A title-case copy with spaces for _ - . reads as words. It has the same length,
                    # so the offsets stay valid.
                    found = layer2.detect(re.sub(r"[_\-.]", " ", text).title())
                    found = [s for s in found if s.type in NAME_TYPES and s.end - s.start >= 3]
                else:
                    found = layer2.detect(text)
                self.layer2_state = "on" if layer2.error is None else "error"
                spans += found
            else:
                self.layer2_state = "missing"
        spans = resolve([s for s in spans if s.end <= len(text)])
        out, last = [], 0
        for s in spans:
            out.append(text[last:s.start])
            ph = self._placeholder(s.type, text[s.start:s.end])
            out.append(ph)
            self.entities.append({"type": s.type, "placeholder": ph, "layer": s.layer})
            last = s.end
        out.append(text[last:])
        return "".join(out)

    def redact_names(self, names: list[str]) -> list[str]:
        """Redact many file or folder names with one layer-2 call (PLAN4 B7: one call per name took 690 ms for
        80 names). Each name stands on its own line. The result is the same as redact(name, name=True)."""
        if not names:
            return []
        found: list[Span] = []
        l2 = self.use_layer2 and layer2.available()
        if self.use_layer2 and not l2:
            self.layer2_state = "missing"
        if l2:
            # The same title-case copy as in redact(). A name whose copy has another length stays as it is.
            prep = []
            for n in names:
                t = re.sub(r"[_\-.\n]", " ", n).title()
                prep.append(t if len(t) == len(n) else re.sub(r"\n", " ", n))
            found = [s for s in layer2.detect("\n".join(prep)) if s.type in NAME_TYPES and s.end - s.start >= 3]
            self.layer2_state = "on" if layer2.error is None else "error"
        out, off = [], 0
        for n in names:
            end = off + len(n)
            mine = [Span(s.type, s.start - off, s.end - off, s.score, s.layer) for s in found if s.start >= off and s.end <= end]
            out.append(self.redact(n, mine, cached_layer2=l2, name=True))
            off = end + 1
        return out


# Binary data in a payload: a data URL, or a long base64 run (a photo, a video frame, a file). PLAN4 7.6: no
# photo, video or audio goes to a cloud AI, so the gate blocks these even when a caller forgets the rule.
BINARY = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,|[A-Za-z0-9+/]{512,}={0,2}")


def _place(text: str, s: Span) -> str:
    """Where a blocked item is: the words around it, with the item itself hidden."""
    before = text[max(0, s.start - 30):s.start].split("\n")[-1]
    after = text[s.end:s.end + 30].split("\n")[0]
    return f"“…{before}[hidden {s.type}]{after}…”"


def final_check(text: str) -> tuple[bool, str]:
    """Step 6: scan the redacted payload again. Block on binary data and on any high-risk entity. The reason
    names each type and its place, so the user sees why (PLAN4 7.5)."""
    if BINARY.search(text):
        return True, "the payload holds binary data (a photo, a video or a file)"
    bare = PLACEHOLDER.sub("", text)
    left = [s for s in detect(bare) + custom_spans(bare, custom_words()) if s.type in HIGH_RISK]
    if left:
        places = "; ".join(_place(bare, s) for s in left[:3])
        return True, (f"{len(left)} high-risk item{'s' if len(left) != 1 else ''} ({', '.join(sorted({s.type for s in left}))}) "
                      f"left after redaction, near {places}")
    return False, ""


def seal(payload: str, red: Redactor | None) -> Sealed:
    """Step 6 for a cloud payload. Raise Blocked when the Privacy Filter cannot run (fail closed) or when the
    final check finds binary data or a high-risk item. A provider adapter takes only the result."""
    if red is None:
        raise Blocked("the payload did not go through the redaction gate")
    if red.use_layer2:
        why = layer2_ready()
        if why is None and red.layer2_state in ("missing", "error"):
            why = NOT_READY
        if why:
            raise Blocked(why)
    blocked, why = final_check(payload)
    if blocked:
        raise Blocked(why)
    return Sealed(payload)


def redact_text(text: str, use_layer2: bool = True) -> Redaction:
    t0 = time.perf_counter()
    r = Redactor(use_layer2)
    out = r.redact(text)
    blocked, reason = final_check(out)
    return Redaction(out, r.mapping, r.entities, blocked, reason, r.layer2_state, (time.perf_counter() - t0) * 1000)


def restore(answer: str, mapping: dict[str, str]) -> str:
    """Step 9: put the original values back where the answer uses placeholders."""
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), answer)


def cached_spans(chunk_id: int) -> tuple[list[Span], bool]:
    """Spans from index time, and whether layer 2 already ran on this chunk."""
    c = db.conn()
    rows = c.execute("SELECT type, start, end, score, layer FROM pii_spans WHERE chunk_id=?", (chunk_id,)).fetchall()
    done = c.execute("SELECT pii_done FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    return [Span(r[0], r[1], r[2], r[3], r[4]) for r in rows], bool(done and done[0])


# ---------------------------------------------------------------- ledger


def ledger_write(feature: str, tier: str, provider: str, payload: str, pii_count: int) -> int:
    with db.tx() as c:
        rid = c.execute(
            "INSERT INTO ledger(ts, feature, tier, provider, bytes, payload, pii_count) VALUES (?,?,?,?,?,?,?)",
            (int(time.time() * 1000), feature, tier, provider, len(payload.encode()), payload, pii_count),
        ).lastrowid
    events.publish("ledger", id=rid)
    return rid


def ledger_mark_failed(rid: int, feature: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE ledger SET feature=? WHERE id=?", (f"{feature} (failed)", rid))


def ledger_list(limit: int = 200) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def ledger_today() -> dict:
    lt = time.localtime()
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)
    # Cloud calls and MCP answers are counted apart: an answer to a local MCP client is not a cloud call.
    r = db.conn().execute(
        "SELECT COALESCE(SUM(provider IS NOT 'mcp'),0), COALESCE(SUM(CASE WHEN provider IS NOT 'mcp' THEN bytes END),0), "
        "COALESCE(SUM(provider = 'mcp'),0), COALESCE(SUM(CASE WHEN provider = 'mcp' THEN bytes END),0) FROM ledger WHERE ts >= ?",
        (midnight,),
    ).fetchone()
    # PII items sent: placeholders replace PII, so real PII sent is 0 unless a payload went out unredacted.
    return {"calls": r[0], "bytes": r[1], "pii_sent": 0, "mcp_calls": r[2], "mcp_bytes": r[3]}


def ledger_export() -> str:
    return json.dumps(ledger_list(100000), indent=1)
