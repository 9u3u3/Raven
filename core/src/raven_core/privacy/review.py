"""Review before send (PLAN4 7.7): every cloud payload waits for the user, in every AI mode.

confirm() runs after gate.seal(). It shows the exact sealed payload in the window (event "cloud_review"), with each
hidden item and its value (on this computer only), and waits. The user can hide more words, show a low-risk item
again, then send or not. "Send" sends the payload that the user saw, never a new one. A request that nobody
answers in 5 minutes, or with no open window, sends nothing.

Suspicious words: values that may be private but stay visible. Two local checks find them, and neither sends
anything:
 1. Fast, at once: the weak finds of the strict model (score 0.2 to 0.4), long numbers, codes and project names.
 2. The local LLM, when it runs: Raven picks the visible names in capitals and the amounts of money, and the LLM
    says which ones may be private. Its list comes a few seconds later (event "cloud_review" again).
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
SUSPECT_LOW = 0.2  # 0.15 gave weekdays as dates of birth
LLM_CHARS = 6000  # the local LLM reads this much of the payload, so its list comes in seconds

NO_WINDOW = "no Raven window is open to review the payload, so nothing went to the cloud"
CANCELLED = "you did not send it"
TIMED_OUT = "nobody answered the review in 5 minutes, so nothing went to the cloud"
NEVER_SHOW = {"CUSTOM"} | HIGH_RISK  # a value of these types never goes to the cloud, so the review cannot show it

LABEL = {"PERSON": "name", "ADDRESS": "address", "DOB": "date of birth", "HEALTH": "health detail"}

# The local LLM (qwen3:4b, no thinking) is weak at finding private words in a listing by itself: it gives whole
# sentences or file names. It is good at a yes or no for each word. So Raven picks the candidates (names in capitals,
# amounts of money), and the LLM says which ones may be private.
SUSPECT_SYSTEM = (
    "You protect the owner of these files. The list holds words from a text that will go to a cloud AI. For each "
    "word, say if it may be private. Private means the owner may not want a stranger to see it, also when it is not "
    "about a person: a name of a person, a company or a client, a project or code name, an address, an ID or account "
    "number, a health detail, or an amount of money. Tools, languages, public products, famous places and common "
    "words are not private. [hidden] marks a value that is hidden already. Give a reason of at most six words."
)
SUSPECT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "private": {"type": "boolean"}, "why": {"type": "string"}},
        "required": ["text", "private", "why"]}}},
    "required": ["items"],
}
MAX_CANDIDATES = 40
_CAPS = re.compile(r"(?<![\w.])[A-Z][\w'’-]*(?:[ \t]+[A-Z][\w'’-]*){0,3}(?!\w|\.\w)")  # a full stop ends a sentence, Notes.txt is a file
_MONEY = re.compile(r"(?<![\w.])(?:(?:Rs\.?|INR|USD|EUR|₹|\$|€|£)\s?\d[\d,]*(?:\.\d+)?|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)(?![\w.])")
# Words that start a sentence or a payload line. They drop from the front of a candidate: "Call Ramesh" -> "Ramesh".
_COMMON = set("""a an the and or but if then so to of in on at by for from with about after before as is are was were be
this that these those it its he she they we you i my our your his her their there here when where what which who why how
call pay send meet meeting meet forwarding forward please thanks thank dear hi hello note notes see also new old
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september
october november december today tomorrow yesterday folder folders files file text samples sample existing selected
command question sources source page summary describe cv pdf""".split())

_LONG_NUMBER = re.compile(r"(?<![\w.])\d{6,}(?![\w.])")
_DATE_NUMBER = re.compile(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(\d{4,6})?")  # 20250412, 20250412101500
# The parts of a folder listing (sizes, months, times) are not private, so they are never candidates.
_LISTING = re.compile(r"\d+(\.\d+)?\s?(B|KB|MB|GB|TB)|(19|20)\d{2}-\d{2}(-\d{2})?|\d{1,2}:\d{2}")
# The local LLM (4B) reads a project name as "not personal", so a rule finds "Project Falcon", "Operation Blue".
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
    llm: str = "off"  # off | running | done | error
    created: float = field(default_factory=time.time)


_open: dict[str, _Review] = {}
_lock = threading.Lock()


def _bare(text: str) -> str:
    """The text with each placeholder blanked out. The offsets stay the same."""
    return gate.PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), text)


def _add(out: list[dict], bare: str, text: str, why: str, source: str) -> None:
    t = text.strip(" \t\n.,;:()[]{}\"'")
    if not 2 <= len(t) <= 80 or t not in bare or any(t.lower() == s["text"].lower() for s in out):
        return
    out.append({"text": t, "why": why, "source": source})


def fast_suspects(payload: str) -> list[dict]:
    bare = _bare(payload)
    out: list[dict] = []
    if gate.strict.available():
        for s in gate.strict.detect(bare, threshold=SUSPECT_LOW):
            if s.score < 0.4 or not gate.strict_on():  # a stronger find is hidden already when Strict mode is on
                _add(out, bare, bare[s.start:s.end], f"may be a {LABEL.get(s.type, s.type.lower())} ({round(s.score * 100)} %)", "model")
    for m in _LONG_NUMBER.finditer(bare):
        if not _DATE_NUMBER.fullmatch(m.group(0)):
            _add(out, bare, m.group(0), "a long number", "pattern")
    for m in _CODE.finditer(bare):
        _add(out, bare, m.group(0), "a code that may be an ID", "pattern")
    for m in _PROJECT.finditer(bare):
        _add(out, bare, m.group(0), "a project or code name", "pattern")
    return out[:30]


def candidates(bare: str, skip: list[str] | None = None) -> list[str]:
    """Words the local LLM checks: names in capitals and amounts of money that are still visible."""
    out: list[str] = []
    seen = {s.lower() for s in skip or []}
    for m in _CAPS.finditer(bare):
        words = m.group(0).split()
        while words and words[0].lower() in _COMMON:
            words = words[1:]
        while words and words[-1].rstrip(".").upper() in ("RS", "INR", "USD", "EUR"):  # "Pay Ramesh Rs 45,000"
            words = words[:-1]
        t = " ".join(words)
        if len(t) < 3 or t.lower() in seen or _LISTING.fullmatch(t):
            continue
        if t.isupper() and len(t) < 4:  # PAN, PDF: a type word, not a value
            continue
        seen.add(t.lower())
        out.append(t)
    for m in _MONEY.finditer(bare):
        if m.group(0).lower() not in seen:
            seen.add(m.group(0).lower())
            out.append(m.group(0))
    return out[:MAX_CANDIDATES]


def _match(said: str, cands: list[str]) -> str | None:
    """The candidate that the model means. It can write "Kavya (person)" for "Kavya"."""
    t = said.strip().lower()
    for c in cands:
        if c.lower() == t:
            return c
    for c in cands:
        if len(t) >= 3 and (c.lower() in t or t in c.lower()):
            return c
    return None


def _llm_suspects(r: _Review) -> None:
    from ..router import local

    try:
        bare = _bare(r.payload[:LLM_CHARS])
        cands = candidates(bare, [s["text"] for s in r.suspects])
        if not cands or not local.available():
            r.llm = "off" if cands else "done"
            return
        r.llm = "running"
        _publish(r)
        lines = []
        shown = gate.PLACEHOLDER.sub("[hidden]", r.payload[:LLM_CHARS])
        for c in cands:
            i = shown.find(c)
            around = " ".join(shown[max(0, i - 40):i + len(c) + 40].split())
            lines.append(f"- {c}: “…{around}…”")
        data = local.chat(SUSPECT_SYSTEM, "Words, each with the text around it:\n" + "\n".join(lines), SUSPECT_SCHEMA,
                          max_tokens=40 + 24 * len(cands))
        for it in data.get("items") or []:
            if not (isinstance(it, dict) and it.get("private") is True and isinstance(it.get("text"), str)):
                continue
            c = _match(it["text"], cands)
            if c:  # only a candidate that Raven gave: the model cannot add text of its own
                _add(r.suspects, _bare(r.payload), c, str(it.get("why") or "the local AI marked it")[:60], "local AI")
        r.llm = "done"
    except Exception as e:  # noqa: BLE001 - the review works without this list
        log.info("local suspect check failed: %s", e)
        r.llm = "error"
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
            "suspects": [s for s in r.suspects if s["text"] in bare], "llm": r.llm, "created": r.created,
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
    r.suspects = fast_suspects(r.payload)
    with _lock:
        _open[r.id] = r
    _publish(r)
    threading.Thread(target=_llm_suspects, args=(r,), name="review-suspects", daemon=True).start()
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
