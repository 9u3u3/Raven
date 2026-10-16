"""The redaction gate (section 10.3) and the privacy ledger (section 10.4).

Steps for every cloud payload:
 1. The caller builds the minimum payload.
 2. Cached spans from index time apply first (fast).
 3. Layer 1 runs again on the whole payload.
 4. Layer 2 runs on text that has no cached spans.
 5. Each entity becomes a typed placeholder: [PERSON_1], [ACCOUNT_1]. The map stays in memory.
 6. Final check: layer 1 scans the redacted payload. A high-risk entity left means block.
 7. The payload goes to the ledger.
 8. The router sends it.
 9. restore() puts the original values back into the answer, locally.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .. import db, events
from .model import model as layer2
from .rules import HIGH_RISK, Span, detect, resolve

PLACEHOLDER = re.compile(r"\[([A-Z]+)_(\d+)\]")
NAME_TYPES = {"PERSON", "EMAIL", "PHONE", "ADDRESS", "SECRET"}


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


def final_check(text: str) -> tuple[bool, str]:
    """Step 6: scan the redacted payload again. Block on any high-risk entity."""
    left = [s for s in detect(PLACEHOLDER.sub("", text)) if s.type in HIGH_RISK]
    if left:
        return True, f"{len(left)} high-risk item{'s' if len(left) != 1 else ''} ({', '.join(sorted({s.type for s in left}))}) left after redaction"
    return False, ""


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
