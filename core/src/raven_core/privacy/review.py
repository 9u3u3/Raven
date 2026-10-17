"""Review before send (PLAN4 7.7): every cloud payload waits for the user, in every AI mode.

confirm() runs after gate.seal(). It shows the exact sealed payload in the window (event "cloud_review"), with each
hidden item and its value (on this computer only), and waits. The user can hide more words, show a low-risk item
again, then send or not. "Send" sends the payload that the user saw, never a new one. A request that nobody
answers in 5 minutes, or with no open window, sends nothing.

Suspicious words: values that may be private but stay visible, each with a category and a score, highest first.
Two local checks find them, and neither sends anything:
 1. Patterns, at once: long numbers, codes and project names.
 2. A decision model: GLiNER (the strict privacy model) scores each visible span for the categories in CHECK.
    On a labeled set (PLAN4 7.7) it ranked private words best: AUC 0.86 and 83 % right categories, 14 ms a word.
    Laya (0.77, 422 ms a word, needs torch) and the local LLM (0.64, 1 s a word) did worse. Its list comes when
    the model is done (event "cloud_review" again).
The user decides. Raven never hides or shows a suspicious word by itself.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from .. import events
from . import gate
from .rules import HIGH_RISK, custom_spans

log = logging.getLogger("raven.review")

WAIT_S = 300
NO_WINDOW_S = 10  # the window closed during a review: stop after this time
SUSPECT_LOW = 0.2  # the lowest score that the review shows. 0.15 gave weekdays as dates of birth.
HIGH = 0.5  # a score from here is "likely private"
MAX_SUSPECTS = 40

NO_WINDOW = "no Raven window is open to review the payload, so nothing went to the cloud"
CANCELLED = "you did not send it"
TIMED_OUT = "nobody answered the review in 5 minutes, so nothing went to the cloud"
NEVER_SHOW = {"CUSTOM"} | HIGH_RISK  # a value of these types never goes to the cloud, so the review cannot show it

# The decision model's labels -> the category that the review shows.
CHECK = {"person": "name", "company": "company", "organization": "company", "project name": "project",
         "street address": "address", "amount of money": "money", "password": "password", "id number": "ID number",
         "medical condition": "health"}

_LONG_NUMBER = re.compile(r"(?<![\w.])\d{6,}(?![\w.])")
_DATE_NUMBER = re.compile(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(\d{4,6})?")  # 20250412, 20250412101500
# The parts of a folder listing (sizes, months, times) are not private, so they never count.
_LISTING = re.compile(r"\d+(\.\d+)?\s?(B|KB|MB|GB|TB)|(19|20)\d{2}-\d{2}(-\d{2})?|\d{1,2}:\d{2}")
# The model misses some project names (Project Falcon, Nightjar in the labeled set), so a rule adds the usual forms.
_PROJECT = re.compile(r"(?<![\w.])(?:Project|Operation|Codename|Code name|Program|Programme)[ \t]+[A-Z][\w-]+(?:[ \t]+[A-Z][\w-]+)?")
_CODE = re.compile(r"(?<![\w.])(?=(?:[A-Z0-9]*[A-Z]){2})(?=(?:[A-Z0-9]*\d){3})[A-Z0-9]{8,20}(?![\w.])")


@dataclass
class _Review:
    id: str
    feature: str
    providers: list[str]
    system: str
    payload: str
    red: gate.Redactor
    done: threading.Event = field(default_factory=threading.Event)
    decision: str = ""
    suspects: list[dict] = field(default_factory=list)
    check: str = "off"  # the decision model: off (not installed) | running | done | error
    created: float = field(default_factory=time.time)


_open: dict[str, _Review] = {}
_lock = threading.Lock()


def _bare(text: str) -> str:
    """The text with each placeholder blanked out. The offsets stay the same."""
    return gate.PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), text)


def _add(out: list[dict], bare: str, text: str, category: str, score: float, source: str) -> None:
    """One suspicious word. The same word twice keeps the higher score. A part of a listed word adds nothing."""
    t = text.strip(" \t\n.,;:()[]{}\"'")
    if not 2 <= len(t) <= 80 or t not in bare or _LISTING.fullmatch(t):
        return
    for s in out:
        if t.lower() != s["text"].lower() and re.search(rf"\b{re.escape(t.lower())}\b", s["text"].lower()):
            return
        if s["text"].lower() == t.lower():
            if score > s["score"]:
                s.update(category=category, score=round(score, 2), source=source)
            return
    out.append({"text": t, "category": category, "score": round(score, 2), "source": source})


def _sort(out: list[dict]) -> list[dict]:
    return sorted(out, key=lambda s: -s["score"])[:MAX_SUSPECTS]


def pattern_suspects(payload: str) -> list[dict]:
    bare = _bare(payload)
    out: list[dict] = []
    for m in _LONG_NUMBER.finditer(bare):
        if not _DATE_NUMBER.fullmatch(m.group(0)):
            _add(out, bare, m.group(0), "number", 0.5, "pattern")
    for m in _CODE.finditer(bare):
        _add(out, bare, m.group(0), "ID number", 0.5, "pattern")
    for m in _PROJECT.finditer(bare):
        _add(out, bare, m.group(0), "project", 0.7, "pattern")
    return _sort(out)


def model_suspects(payload: str) -> list[dict]:
    """The decision model's scored spans in the visible text. Empty when the model is not installed."""
    bare = _bare(payload)
    out: list[dict] = []
    labels = {k: v.upper().replace(" ", "_") for k, v in CHECK.items()}
    names = {v.upper().replace(" ", "_"): v for v in CHECK.values()}
    for s in gate.strict.detect(bare, threshold=SUSPECT_LOW, labels=labels):
        _add(out, bare, bare[s.start:s.end], names.get(s.type, s.type.lower()), s.score, "model")
    return _sort(out)


def _check(r: _Review) -> None:
    try:
        found = model_suspects(r.payload)
        for s in found:
            _add(r.suspects, _bare(r.payload), s["text"], s["category"], s["score"], s["source"])
        r.suspects = _sort(r.suspects)
        r.check = "done"
    except Exception as e:  # noqa: BLE001 - the review works without this list
        log.info("suspect check failed: %s", e)
        r.check = "error"
    if not r.done.is_set():
        _publish(r)


def public(r: _Review) -> dict:
    layers = {}
    for e in r.red.entities:
        layers.setdefault(e["placeholder"], e["layer"])
    items = []
    for ph in dict.fromkeys(m.group(0) for m in gate.PLACEHOLDER.finditer(r.payload)):
        if ph not in r.red.mapping:
            continue
        typ = gate.PLACEHOLDER.fullmatch(ph).group(1)
        items.append({"placeholder": ph, "type": typ, "value": r.red.mapping[ph], "layer": layers.get(ph),
                      "can_show": typ not in NEVER_SHOW})
    bare = _bare(r.payload)
    return {"id": r.id, "feature": r.feature, "providers": r.providers, "system": r.system, "payload": r.payload,
            "bytes": len((r.system + "\n\n" + r.payload).encode()), "items": items,
            "suspects": [s for s in r.suspects if s["text"] in bare], "check": r.check, "created": r.created,
            "wait_s": WAIT_S}


def _publish(r: _Review) -> None:
    events.publish("cloud_review", review=public(r))


def confirm(payload: gate.Sealed, red: gate.Redactor, feature: str, providers: list[str], system: str = "") -> gate.Sealed:
    """Show the payload and wait for the user. Return the payload to send. Raise gate.Blocked when the user does not
    send it, when nobody answers, or when no window is open."""
    from ..router import providers as registry

    if events.clients() == 0:
        raise gate.Blocked(NO_WINDOW)
    r = _Review(id=uuid.uuid4().hex[:12], feature=feature, providers=[registry.label(p) for p in providers], system=system,
                payload=str(payload), red=red)
    r.suspects = pattern_suspects(r.payload)
    r.check = "running" if gate.strict.available() else "off"
    with _lock:
        _open[r.id] = r
    _publish(r)
    if r.check == "running":
        threading.Thread(target=_check, args=(r,), name="review-suspects", daemon=True).start()
    end, alone = time.monotonic() + WAIT_S, None
    try:
        while not r.done.wait(1.0):
            now = time.monotonic()
            if now > end:
                raise gate.Blocked(TIMED_OUT)
            if events.clients() == 0:
                alone = alone or now
                if now - alone > NO_WINDOW_S:
                    raise gate.Blocked(NO_WINDOW)
            else:
                alone = None
    finally:
        with _lock:
            _open.pop(r.id, None)
        events.publish("cloud_review_closed", id=r.id)
    if r.decision != "send":
        raise gate.Blocked(CANCELLED)
    return gate.seal(r.payload, r.red)  # the final check runs again on the payload with the user's changes


def _get(rid: str) -> _Review:
    with _lock:
        r = _open.get(rid)
    if r is None or r.done.is_set():
        raise KeyError("This review is closed")
    return r


def update(rid: str, hide: list[str] | None = None, show: list[str] | None = None) -> dict:
    """Hide more words, or show low-risk items again. The final check must pass on the result."""
    r = _get(rid)
    text = r.payload
    for ph in show or []:
        m = gate.PLACEHOLDER.fullmatch(ph)
        if not m or ph not in r.red.mapping:
            raise ValueError(f"Unknown item {ph}")
        if m.group(1) in NEVER_SHOW:
            raise ValueError(f"A {m.group(1)} never goes to the cloud, so Raven keeps it hidden")
        text = text.replace(ph, r.red.mapping[ph])
    for w in hide or []:
        w = w.strip()
        if len(w) < 2:
            continue
        holes = [(m.start(), m.end()) for m in gate.PLACEHOLDER.finditer(text)]
        spans = [s for s in custom_spans(text, [w]) if not any(s.start < b and a < s.end for a, b in holes)]
        if not spans:
            raise ValueError(f"“{w}” is not in the payload")
        for s in sorted(spans, key=lambda s: -s.start):
            text = text[:s.start] + r.red._placeholder("HIDDEN", text[s.start:s.end]) + text[s.end:]
    blocked, why = gate.final_check(text)
    if blocked:
        raise ValueError(f"Raven cannot send that: {why}")
    r.payload = text
    _publish(r)
    return public(r)


def decide(rid: str, send: bool) -> None:
    r = _get(rid)
    r.decision = "send" if send else "cancel"
    r.done.set()


def pending() -> list[dict]:
    """Open reviews, for a window that opens or reloads while one waits."""
    with _lock:
        return [public(r) for r in _open.values() if not r.done.is_set()]
