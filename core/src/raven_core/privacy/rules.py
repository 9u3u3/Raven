"""PII layer 1 (section 10.3 step 3): pattern and checksum recognizers.

These follow the Presidio pattern-recognizer approach, with checksums where a format
has one: Luhn for cards, Verhoeff for Aadhaar, mod 97 for IBAN. Raven runs them
without the Presidio package, because Presidio pulls in a large spaCy model.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    type: str
    start: int
    end: int
    score: float
    layer: int = 1


# Types whose leak is serious. A payload with one of these left after redaction is blocked.
HIGH_RISK = {"AADHAAR", "PAN", "CARD", "ACCOUNT", "IBAN", "SECRET", "PASSWORD"}

# ---------------------------------------------------------------- checksums

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_ok(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_ok(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if len(s) < 15:
        return False
    moved = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


# ---------------------------------------------------------------- patterns

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w+])(?:\+91[\s\-]?|91[\s\-]|0)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)|(?<![\w])\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_UPI = re.compile(r"\b[A-Za-z0-9.\-_]{2,64}@(?:[A-Za-z]{2,32})\b(?!\.[A-Za-z])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_ACCOUNT_CTX = re.compile(r"(?i)\b(?:a/?c|acct|account|a/c no|account no|account number)[\s.:#\-]*(?:no\.?|number)?[\s.:#\-]*((?:\d[ \-]?){8,17}\d)")
_SECRETS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]
_PASSWORD_CTX = re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|pin|wifi key|api[_ ]?key|secret|token)\s*(?:is|[:=\-])\s*[\"']?([^\s\"']{4,})")
_PERSON_CTX = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Kumari|Prof)\.?\s+([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){0,2})"
    r"|(?i:\b(?:name|lessor|lessee|tenant|landlord|employee|customer|account holder|patient|student|candidate|from|to|dear)\s*[:\-,]?\s*)"
    r"((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}))"
)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def detect(text: str) -> list[Span]:
    """All layer-1 spans, without overlaps. A longer or higher-risk span wins."""
    found: list[Span] = []
    add = found.append
    for m in _EMAIL.finditer(text):
        add(Span("EMAIL", m.start(), m.end(), 0.99))
    for m in _AADHAAR.finditer(text):
        d = _digits(m.group())
        if len(d) == 12 and verhoeff_ok(d):
            add(Span("AADHAAR", m.start(), m.end(), 0.99))
    for m in _CARD.finditer(text):
        d = _digits(m.group())
        if 13 <= len(d) <= 19 and luhn_ok(d) and not (len(d) == 12):
            add(Span("CARD", m.start(), m.end(), 0.95))
    for m in _PAN.finditer(text):
        add(Span("PAN", m.start(), m.end(), 0.95))
    for m in _IFSC.finditer(text):
        add(Span("IFSC", m.start(), m.end(), 0.85))
    for m in _IBAN.finditer(text):
        if iban_ok(m.group()):
            add(Span("IBAN", m.start(), m.end(), 0.98))
    for m in _ACCOUNT_CTX.finditer(text):
        add(Span("ACCOUNT", m.start(1), m.end(1), 0.9))
    for m in _PHONE.finditer(text):
        if len(_digits(m.group())) >= 10:
            add(Span("PHONE", m.start(), m.end(), 0.85))
    for m in _UPI.finditer(text):
        if not _EMAIL.fullmatch(m.group()):
            add(Span("UPI", m.start(), m.end(), 0.8))
    for m in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group())
            if not ip.is_loopback and not ip.is_unspecified:
                add(Span("IP", m.start(), m.end(), 0.8))
        except ValueError:
            pass
    for rx in _SECRETS:
        for m in rx.finditer(text):
            add(Span("SECRET", m.start(), m.end(), 0.99))
    for m in _PASSWORD_CTX.finditer(text):
        add(Span("PASSWORD", m.start(1), m.end(1), 0.85))
    for m in _PERSON_CTX.finditer(text):
        g = 1 if m.group(1) else 2
        add(Span("PERSON", m.start(g), m.end(g), 0.7))
    return resolve(found)


def resolve(spans: list[Span]) -> list[Span]:
    """Drop overlaps: keep high-risk first, then the longer span, then the higher score."""
    order = sorted(spans, key=lambda s: (s.type not in HIGH_RISK, -(s.end - s.start), -s.score))
    kept: list[Span] = []
    for s in order:
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)
