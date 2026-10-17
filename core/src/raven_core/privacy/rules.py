"""PII layer 1 (section 10.3 step 3, PLAN4 7.2): pattern and checksum recognizers.

These follow the Presidio pattern-recognizer approach, with checksums where a format
has one: Luhn for cards and IMEI, Verhoeff for Aadhaar and VID, mod 97 for IBAN, mod 36
for GSTIN, Base58Check and bech32 for Bitcoin. Raven runs them without the Presidio
package, because Presidio pulls in a large spaCy model.

A type that has no fixed format (a passport, a UAN, a medical record number) needs a
context word near the value. The first batch is India and the types that apply in all
countries (PLAN4 section 0, decision 6).

Type names are one word of capital letters, because a placeholder is [TYPE_N].
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    type: str
    start: int
    end: int
    score: float
    layer: int = 1


# Types whose leak is serious (PLAN4 7.2): national IDs, bank, card, health and secrets. A payload with one of
# these left after redaction is blocked.
NATIONAL_ID = {"AADHAAR", "VID", "PAN", "GSTIN", "PASSPORT", "VOTERID", "LICENCE", "UAN", "ABHA", "RATIONCARD"}
BANK = {"ACCOUNT", "IBAN", "CARD", "CVV", "EXPIRY", "WALLET"}
HEALTH = {"HEALTH", "MRN"}
SECRETS = {"SECRET", "PASSWORD", "OTP", "CUSTOM"}
HIGH_RISK = NATIONAL_ID | BANK | HEALTH | SECRETS

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


_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_ok(s: str) -> bool:
    """The GSTIN check digit: mod 36 over the first 14 characters, with the factors 1 and 2 in turn."""
    s = s.upper()
    if len(s) != 15 or any(ch not in _B36 for ch in s):
        return False
    total = 0
    for i, ch in enumerate(s[:14]):
        p = _B36.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _B36[(36 - total % 36) % 36] == s[14]


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58check_ok(s: str) -> bool:
    n = 0
    for ch in s:
        if ch not in _B58:
            return False
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes(25, "big") if n.bit_length() <= 200 else b""
    if len(raw) != 25:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]


_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def bech32_ok(s: str) -> bool:
    s = s.lower()
    hrp, _, data = s.rpartition("1")
    if hrp != "bc" or len(data) < 6 or any(ch not in _BECH32 for ch in data):
        return False
    values = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + [_BECH32.index(ch) for ch in data]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i, g in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            chk ^= g if (top >> i) & 1 else 0
    return chk in (1, 0x2BC830A3)  # bech32 (SegWit v0) and bech32m (Taproot)


def entropy(s: str) -> float:
    """Shannon entropy in bits per character. A random key has more than 3.5. An English word has less."""
    if not s:
        return 0.0
    counts = {ch: s.count(ch) for ch in set(s)}
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


# ---------------------------------------------------------------- patterns

# A separator inside a long number: a space, a dash, or a line end (a number split over two lines).
_SEP = r"(?:[ \-]|\r?\n)?"
# Boundaries for IDs made of letters and digits. \b fails in a file name, because "_" is a word character.
_L, _R = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_STATES = ("AN|AP|AR|AS|BR|CH|CG|DD|DL|DN|GA|GJ|HP|HR|JH|JK|KA|KL|LA|LD|MH|ML|MN|MP|MZ|NL|OD|OR|PB|PY|RJ|SK|TN|TR|TS|"
           "UK|UA|UP|WB")
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = (rf"(?:\d{{1,2}}[/\-.]\d{{1,2}}[/\-.](?:19|20)?\d{{2}}|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}}"
         rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS},?\s+(?:19|20)\d{{2}}|{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{{2}})")

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w+])(?:\+91[\s\-]?|91[\s\-]|0)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)|(?<![\w])\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?!\d)")
_CARD = re.compile(rf"(?<!\d)(?:\d{_SEP}){{12,18}}\d(?!\d)")
# Any one separator between two digits: "2341 2341 2346", "2 3 4 1 …", or a number split over two lines.
_AADHAAR = re.compile(rf"(?<!\d)[2-9](?:{_SEP}\d){{11}}(?!\d)")
_AADHAAR_MASKED = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[x*]{4}[ \-]?){2}\d{4}(?!\d)")
_VID = re.compile(rf"(?<!\d)\d(?:{_SEP}\d){{15}}(?!\d)")
_PAN = re.compile(rf"{_L}[A-Z]{{3}}[PCHFATBLJG][A-Z]\d{{4}}[A-Z]{_R}")
_GSTIN = re.compile(rf"{_L}\d{{2}}[A-Z]{{5}}\d{{4}}[A-Z][1-9A-Z]Z[0-9A-Z]{_R}")
_TAN = re.compile(rf"{_L}[A-Z]{{4}}\d{{5}}[A-Z]{_R}")
_CIN = re.compile(rf"{_L}[LU]\d{{5}}[A-Z]{{2}}\d{{4}}[A-Z]{{3}}\d{{6}}{_R}")
_IFSC = re.compile(rf"{_L}[A-Z]{{4}}0[A-Z0-9]{{6}}{_R}")
_UPI = re.compile(r"\b[A-Za-z0-9.\-_]{2,64}@(?:[A-Za-z]{2,32})\b(?!\.[A-Za-z])")
_IBAN = re.compile(rf"{_L}[A-Z]{{2}}\d{{2}}(?:[ ]?[A-Z0-9]{{4}}){{2,7}}(?:[ ]?[A-Z0-9]{{1,4}})?{_R}")
_SWIFT = re.compile(rf"{_L}[A-Z]{{4}}[A-Z]{{2}}[A-Z0-9]{{2}}(?:[A-Z0-9]{{3}})?{_R}")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){1,7}(?::|(?::[0-9A-Fa-f]{1,4}){1,7}|[0-9A-Fa-f]{1,4})(?![\w:])")
_MAC = re.compile(r"(?<![\w:\-])[0-9A-Fa-f]{2}([:\-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}(?![\w:\-])")
_IMEI = re.compile(rf"(?<!\d)\d{{2}}{_SEP}\d{{6}}{_SEP}\d{{6}}{_SEP}\d(?!\d)")
_GPS = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{4,})\s*°?\s*([NS])?\s*,\s*(-?\d{1,3}\.\d{4,})\s*°?\s*([EW])?(?![\d.])")
_BTC = re.compile(r"(?<![A-Za-z0-9])[13][1-9A-HJ-NP-Za-km-z]{25,34}(?![A-Za-z0-9])")
_BECH = re.compile(r"(?i)(?<![A-Za-z0-9])bc1[02-9ac-hj-np-z]{11,71}(?![A-Za-z0-9])")
_ETH = re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{40}(?![A-Za-z0-9])")
_DL = re.compile(rf"{_L}(?:{_STATES})[ \-]?\d{{2}}[ \-]?(?:19|20)\d{{2}}[ \-]?\d{{7}}{_R}")
_VEHICLE = re.compile(rf"{_L}(?:(?:{_STATES})[ \-]?\d{{1,2}}[ \-]?[A-Z]{{1,3}}[ \-]?\d{{4}}|\d{{2}}[ \-]?BH[ \-]?\d{{4}}[ \-]?[A-Z]{{1,2}}){_R}")
_EPIC = re.compile(rf"{_L}[A-Z]{{3}}\d{{7}}{_R}")
_ABHA = re.compile(rf"(?<!\d)\d{{2}}-\d{{4}}-\d{{4}}-\d{{4}}(?!\d)")
_ACCOUNT_CTX = re.compile(r"(?i)(?<![A-Za-z])(?:a/?c|acct|account|a/c no|account no|account number)[\s.:#\-_=]*(?:no\.?|number)?[\s.:#\-_=]*((?:\d(?:[ \-]|\r?\n)?){8,17}\d)")
_CVV = re.compile(r"(?i)\b(?:cvv2?|cvc2?|cid|card security code|security code)\b\s*(?:is|[:#\-])?\s*(\d{3,4})(?!\d)")
_EXPIRY = re.compile(r"(?i)\b(?:exp(?:iry|ires|iration)?(?:\s*date)?|valid\s*(?:thru|through|till|until|upto))\b\.?\s*(?:is|[:#\-])?\s*((?:0[1-9]|1[0-2])\s*/\s*(?:20)?\d{2})(?!\d)")
_OTP = re.compile(r"(?i)(?<![A-Za-z])(?:otp|one[- ]time (?:password|code)|verification code|security pin|mpin|atm pin)(?![A-Za-z])\D{0,24}?(\d{4,8})(?!\d)")
_MRN = re.compile(r"(?i)(?<![A-Za-z])(?:mrn|medical record (?:no|number|#)|uhid|patient id|hospital (?:no|id)|ip no)(?![A-Za-z])\.?[\s_]*[:#\-=]?\s*([A-Z0-9][A-Z0-9/\-]{3,19})")
_HEALTH = re.compile(r"(?i)(?:\b(?:diagnosis|diagnosed with|diagnosed as|prescribed|prescription|known case of|history of)\b\s*[:\-]?"
                     r"|\b(?:dx|rx)\s*[:\-])\s*([A-Za-z][^\n.;|]{2,60})")
_DOB = re.compile(rf"(?i)\b(?:dob|d\.o\.b\.?|date of birth|birth ?date|born on|born)\b\s*(?:is|[:\-])?\s*({_DATE})")
_PINCODE = re.compile(r"(?<![\w\-])[1-9]\d{2}\s?\d{3}(?![\w\-])")
_ADDRESS_WORDS = re.compile(r"(?i)\b(?:pin|pincode|pin code|postal|address|road|rd|street|st|lane|nagar|colony|sector|"
                            r"flat|apartment|apt|society|chs|district|dist|taluka|tehsil|village|opp|"
                            r"layout|marg|chowk|india|maharashtra|karnataka|delhi|mumbai|pune|"
                            r"bengaluru|bangalore|chennai|kolkata|hyderabad)\b")
# An Indian address that starts with a house word and a number: "Flat 12, FC Road, Pune". Up to three more
# parts after the first comma. The model finds the other forms.
_ADDRESS = re.compile(r"(?i)\b(?:flat|house|h\.? ?no|plot|door|room|shop|apt|apartment|bungalow|row house|block|wing|"
                      r"building|bldg)\.?\s*(?:no\.?\s*)?[A-Z]?-?\d+[A-Z]?(?:[/\-]\d+)?\s*,"
                      r"(?:\s*(?-i:[A-Z0-9][^,\n]{1,40}))(?:,\s*(?-i:[A-Z][^,\n.]{1,30})){0,3}")
_ZIP = re.compile(r"\b(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|"
                  r"V[AT]|W[AIVY])\s+(\d{5}(?:-\d{4})?)\b")

_PASSPORT = re.compile(rf"{_L}[A-Z][1-9]\d\s?\d{{4}}[1-9]{_R}")
_UAN = re.compile(rf"(?<!\d)\d(?:{_SEP}\d){{11}}(?!\d)")
_RATION = re.compile(rf"{_L}[A-Z0-9]{{8,15}}{_R}")
# Context words for types with no fixed format. The word must stand in the 48 characters before the value.
# A word boundary that also holds next to "_" (a file name such as scan_uan_1001….pdf).
_W, _WE = r"(?<![A-Za-z])", r"(?![A-Za-z])"
_CTX = {
    "PASSPORT": re.compile(r"(?i)passport"),
    "UAN": re.compile(rf"(?i){_W}uan{_WE}|universal account|{_W}pf{_WE}|provident fund|{_W}epf{_WE}"),
    "ABHA": re.compile(r"(?i)abha|health id|health account|ndhm|abdm"),
    "RATIONCARD": re.compile(rf"(?i){_W}ration{_WE}"),
    "VOTERID": re.compile(r"(?i)voter|epic|election"),
    "SWIFT": re.compile(rf"(?i)swift|{_W}bic{_WE}"),
    "IMEI": re.compile(r"(?i)imei"),
    "TAN": re.compile(rf"(?i){_W}tan{_WE}"),
}

# Table columns (PLAN4 7.5 red team): in a table the label stands in the header row, not next to the value. A
# column whose header names a type marks each cell below it. Markdown tables, CSV and TSV.
_HEADERS = [
    (re.compile(r"(?i)aadh?aar|^uid$"), "AADHAAR"), (re.compile(r"(?i)^vid$|virtual id"), "VID"),
    (re.compile(r"(?i)^pan\b|pan (?:no|number|card)"), "PAN"), (re.compile(r"(?i)gstin|gst (?:no|number)"), "GSTIN"),
    (re.compile(r"(?i)passport"), "PASSPORT"), (re.compile(r"(?i)voter|epic"), "VOTERID"),
    (re.compile(r"(?i)^dl\b|driving licen[cs]e|licen[cs]e (?:no|number)"), "LICENCE"), (re.compile(r"(?i)^uan\b"), "UAN"),
    (re.compile(r"(?i)abha|health id"), "ABHA"), (re.compile(rf"(?i){_W}ration{_WE}"), "RATIONCARD"),
    (re.compile(r"(?i)iban"), "IBAN"), (re.compile(r"(?i)a/?c\b|acct|account (?:no|number|#)|^account$"), "ACCOUNT"),
    (re.compile(r"(?i)cvv|cvc"), "CVV"), (re.compile(r"(?i)expiry|^exp\b|valid (?:thru|till)"), "EXPIRY"),
    (re.compile(r"(?i)card"), "CARD"), (re.compile(r"(?i)^otp\b|one.time"), "OTP"),
    (re.compile(r"(?i)mrn|uhid|patient id|medical record"), "MRN"), (re.compile(r"(?i)diagnosis|condition"), "HEALTH"),
    (re.compile(r"(?i)password|passwd|pwd|passcode|^pin$"), "PASSWORD"),
    (re.compile(r"(?i)secret|token|api.?key|access.?key"), "SECRET"), (re.compile(r"(?i)e-?mail"), "EMAIL"),
    (re.compile(r"(?i)phone|mobile|contact (?:no|number)"), "PHONE"), (re.compile(r"(?i)^ifsc"), "IFSC"),
    (re.compile(r"(?i)^upi"), "UPI"), (re.compile(r"(?i)dob|date of birth|birth ?date"), "DOB"),
    (re.compile(r"(?i)address"), "ADDRESS"),
    (re.compile(r"(?i)^(?:full |customer |employee |patient |student |account holder )?name$"), "PERSON"),
]
# A header cell is a short label: letters first, then letters, digits, spaces and . _ / # ( ) -. Code with
# commas ("f(a, b)") has no row of such cells.
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9 ._/#()\-]{0,39}$")
_NUMERIC = {"AADHAAR", "VID", "UAN", "ACCOUNT", "CVV", "CARD", "OTP", "PHONE", "ABHA"}

_SECRETS = [
    re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z\-_]{35}\b"),
    re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe
    re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"),
    re.compile(r"(?<![A-Za-z0-9])(?:AC|SK)[a-f0-9]{32}\b"),  # Twilio
    re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),  # SendGrid
    re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[a-f0-9]{40,}"),  # OpenRouter
    re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_\-]{20,}"),  # GitLab
    re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}\b"),
    re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}\b"),  # Hugging Face
]
# A secret inside a match: group 1 is the secret, the rest stays readable.
_SECRET_GROUPS = [
    re.compile(r"(?i)\b(?:AccountKey|SharedAccessKey|azure[_ ]?(?:storage[_ ]?)?key)\s*[=:]\s*([A-Za-z0-9+/]{30,}={0,2})"),
    re.compile(r"\b[a-z][a-z0-9+.\-]{1,20}://[^\s:/@]+:([^\s@/]{3,})@"),  # postgres://user:pass@host
    re.compile(r"(?m)^\s*(?:export\s+)?[A-Za-z_]*(?:SECRET|TOKEN|KEY|PASS(?:WORD)?|PWD|CREDENTIALS?|AUTH)[A-Za-z0-9_]*\s*=\s*[\"']?([^\s\"'#]{4,})"),
    re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-~+/]{16,}=*)"),
]
_SECRET_NEAR = re.compile(r"(?i)\b(?:secret|token|api[_ ]?key|apikey|access[_ ]?key|private[_ ]?key|auth|credential)s?\b[^\n]{0,24}?([A-Za-z0-9+/_\-]{24,}={0,2})")
_PASSWORD_CTX = re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|pin|wifi key|api[_ ]?key|secret|token)\s*(?:is|[:=\-])\s*[\"']?([^\s\"']{4,})")
# In a file name: "wifi_password_Hunter2024!.txt". The value must hold a digit, so "password_policy.pdf" stays.
_PASSWORD_NAME = re.compile(r"(?i)(?<![A-Za-z])(?:password|passwd|pwd|passcode)_((?=[^\s_.]*\d)[^\s_]{4,}?)(?=\.[A-Za-z0-9]{1,5}$|_|$)")
_PERSON_CTX = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Kumari|Prof)\.?\s+([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){0,2})"
    r"|(?i:\b(?:name|lessor|lessee|tenant|landlord|employee|customer|account holder|patient|student|candidate|from|to|dear)\s*[:\-,]?\s*)"
    r"((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}))"
)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _near(text: str, start: int, typ: str, before: int = 48) -> bool:
    return bool(_CTX[typ].search(text, max(0, start - before), start))


def _cells(line: str, start: int, sep: str) -> list[tuple[int, int]]:
    """The (start, end) of each cell value in one table line, with spaces and quotes trimmed."""
    out, pos = [], start
    parts = line.split(sep)
    if sep == "|":
        # "| a | b |": the parts before the first and after the last pipe are not cells.
        lead = 1 if parts and not parts[0].strip() else 0
        pos += sum(len(p) + 1 for p in parts[:lead])
        parts = parts[lead:-1] if len(parts) > lead and not parts[-1].strip() else parts[lead:]
    for p in parts:
        a, b = pos, pos + len(p)
        while a < b and line[a - start] in " \t\"'":
            a += 1
        while b > a and line[b - 1 - start] in " \t\"'\r":
            b -= 1
        out.append((a, b))
        pos += len(p) + 1
    return out


def _table_spans(text: str) -> list[Span]:
    out: list[Span] = []
    lines, pos = [], 0
    for ln in text.split("\n"):
        lines.append((pos, ln))
        pos += len(ln) + 1
    i = 0
    while i < len(lines):
        start, ln = lines[i]
        sep = "|" if ln.count("|") >= 2 else "\t" if "\t" in ln else "," if "," in ln else ""
        if not sep:
            i += 1
            continue
        head = _cells(ln, start, sep)
        names = [text[a:b] for a, b in head]
        # A CSV header has a label in every cell. A markdown or TSV header can have an empty cell.
        if len(head) < 2 or not all(_LABEL.match(n) or (sep != "," and not n) for n in names):
            i += 1
            continue
        cols = {}
        for k, (a, b) in enumerate(head):
            name = text[a:b].strip()
            if 0 < len(name) <= 40:
                typ = next((t for rx, t in _HEADERS if rx.search(name)), None)
                if typ:
                    cols[k] = typ
        j = i + 1
        while j < len(lines):
            s2, l2 = lines[j]
            if l2.count(sep) < 1 or (sep == "|" and l2.count("|") < 2):
                break
            cells = _cells(l2, s2, sep)
            if len(cells) != len(head):
                break
            for k, typ in cols.items():  # empty when the header names no type: the loop only finds the block end
                a, b = cells[k]
                val = text[a:b]
                if b - a < 2 or set(val) <= set("-:| "):
                    continue  # a markdown divider row or an empty cell
                if typ in _NUMERIC and not any(ch.isdigit() for ch in val):
                    continue
                out.append(Span(typ, a, b, 0.8))
            j += 1
        i = j if j > i + 1 else i + 1  # one pass over each block
    return out


def detect(text: str) -> list[Span]:
    """All layer-1 spans, without overlaps. A longer or higher-risk span wins."""
    found: list[Span] = []
    add = found.append
    for m in _EMAIL.finditer(text):
        add(Span("EMAIL", m.start(), m.end(), 0.99))
    for m in _AADHAAR.finditer(text):
        if verhoeff_ok(_digits(m.group())):
            add(Span("AADHAAR", m.start(), m.end(), 0.99))
    for m in _AADHAAR_MASKED.finditer(text):
        add(Span("AADHAAR", m.start(), m.end(), 0.9))
    for m in _VID.finditer(text):
        d = _digits(m.group())
        if len(d) == 16 and verhoeff_ok(d):
            add(Span("VID", m.start(), m.end(), 0.97))
    for m in _CARD.finditer(text):
        d = _digits(m.group())
        if 13 <= len(d) <= 19 and luhn_ok(d) and not (len(d) == 15 and _near(text, m.start(), "IMEI")):
            add(Span("CARD", m.start(), m.end(), 0.95))
    for m in _IMEI.finditer(text):
        d = _digits(m.group())
        if len(d) == 15 and luhn_ok(d) and _near(text, m.start(), "IMEI"):
            add(Span("IMEI", m.start(), m.end(), 0.99))
    for m in _PAN.finditer(text):
        add(Span("PAN", m.start(), m.end(), 0.95))
    for m in _GSTIN.finditer(text):
        if gstin_ok(m.group()):
            add(Span("GSTIN", m.start(), m.end(), 0.99))
    for m in _TAN.finditer(text):
        if _near(text, m.start(), "TAN"):
            add(Span("TAN", m.start(), m.end(), 0.9))
    for m in _CIN.finditer(text):
        add(Span("CIN", m.start(), m.end(), 0.95))
    for m in _IFSC.finditer(text):
        add(Span("IFSC", m.start(), m.end(), 0.85))
    for m in _IBAN.finditer(text):
        if iban_ok(m.group()):
            add(Span("IBAN", m.start(), m.end(), 0.98))
    for m in _SWIFT.finditer(text):
        if _near(text, m.start(), "SWIFT", 24):
            add(Span("SWIFT", m.start(), m.end(), 0.9))
    for m in _ACCOUNT_CTX.finditer(text):
        add(Span("ACCOUNT", m.start(1), m.end(1), 0.9))
    for m in _CVV.finditer(text):
        add(Span("CVV", m.start(1), m.end(1), 0.95))
    for m in _EXPIRY.finditer(text):
        add(Span("EXPIRY", m.start(1), m.end(1), 0.9))
    for m in _PASSPORT.finditer(text):
        if _near(text, m.start(), "PASSPORT"):
            add(Span("PASSPORT", m.start(), m.end(), 0.95))
    for m in _EPIC.finditer(text):
        add(Span("VOTERID", m.start(), m.end(), 0.95 if _near(text, m.start(), "VOTERID") else 0.7))
    for m in _DL.finditer(text):
        add(Span("LICENCE", m.start(), m.end(), 0.9))
    for m in _VEHICLE.finditer(text):
        add(Span("VEHICLE", m.start(), m.end(), 0.85))
    for m in _UAN.finditer(text):
        if _near(text, m.start(), "UAN"):
            add(Span("UAN", m.start(), m.end(), 0.99))
    for m in _ABHA.finditer(text):
        add(Span("ABHA", m.start(), m.end(), 0.95))
    for m in re.finditer(r"(?<!\d)\d{14}(?!\d)", text):
        if _near(text, m.start(), "ABHA"):
            add(Span("ABHA", m.start(), m.end(), 0.95))
    for m in _RATION.finditer(text):
        if _near(text, m.start(), "RATIONCARD", 32) and any(ch.isdigit() for ch in m.group()):
            add(Span("RATIONCARD", m.start(), m.end(), 0.85))
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
    for m in _IPV6.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group())
            if ip.version == 6 and not ip.is_loopback and not ip.is_unspecified and m.group().count(":") >= 2:
                add(Span("IP", m.start(), m.end(), 0.8))
        except ValueError:
            pass
    for m in _MAC.finditer(text):
        add(Span("MAC", m.start(), m.end(), 0.85))
    for m in _GPS.finditer(text):
        try:
            lat, lon = float(m.group(1)), float(m.group(3))
        except ValueError:
            continue
        if abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0):
            add(Span("GPS", m.start(), m.end(), 0.85))
    for m in _BTC.finditer(text):
        if base58check_ok(m.group()):
            add(Span("WALLET", m.start(), m.end(), 0.99))
    for m in _BECH.finditer(text):
        if bech32_ok(m.group()):
            add(Span("WALLET", m.start(), m.end(), 0.99))
    for m in _ETH.finditer(text):
        add(Span("WALLET", m.start(), m.end(), 0.9))
    for m in _DOB.finditer(text):
        add(Span("DOB", m.start(1), m.end(1), 0.9))
    for m in _PINCODE.finditer(text):
        if _ADDRESS_WORDS.search(text, max(0, m.start() - 60), m.start()):
            add(Span("POSTCODE", m.start(), m.end(), 0.75))
    for m in _ADDRESS.finditer(text):
        add(Span("ADDRESS", m.start(), m.end(), 0.8))
    for m in _ZIP.finditer(text):
        add(Span("POSTCODE", m.start(1), m.end(1), 0.75))
    for m in _OTP.finditer(text):
        add(Span("OTP", m.start(1), m.end(1), 0.9))
    for m in _MRN.finditer(text):
        if any(ch.isdigit() for ch in m.group(1)):
            add(Span("MRN", m.start(1), m.end(1), 0.9))
    for m in _HEALTH.finditer(text):
        add(Span("HEALTH", m.start(1), m.start(1) + len(m.group(1).rstrip()), 0.8))
    for rx in _SECRETS:
        for m in rx.finditer(text):
            add(Span("SECRET", m.start(), m.end(), 0.99))
    for rx in _SECRET_GROUPS:
        for m in rx.finditer(text):
            add(Span("SECRET", m.start(1), m.end(1), 0.95))
    for m in _SECRET_NEAR.finditer(text):
        if entropy(m.group(1)) > 3.5 and any(ch.isdigit() for ch in m.group(1)):
            add(Span("SECRET", m.start(1), m.end(1), 0.85))
    for m in _PASSWORD_CTX.finditer(text):
        add(Span("PASSWORD", m.start(1), m.end(1), 0.85))
    for m in _PASSWORD_NAME.finditer(text):
        add(Span("PASSWORD", m.start(1), m.end(1), 0.8))
    for m in _PERSON_CTX.finditer(text):
        g = 1 if m.group(1) else 2
        add(Span("PERSON", m.start(g), m.end(g), 0.7))
    if "|" in text or "," in text or "\t" in text:
        found += _table_spans(text)
    return resolve(found)


def custom_spans(text: str, words: list[str]) -> list[Span]:
    """The user's own words (PLAN4 7.4): family names, a company, a client, a project code. Case does not count,
    and a space in a word also matches "_", "-" and "." (a file name)."""
    out = []
    for w in words:
        parts = [re.escape(p) for p in w.split()]
        if not parts:
            continue
        rx = re.compile(r"(?i)(?<![A-Za-z0-9])" + r"[\s_\-.]+".join(parts) + r"(?![A-Za-z0-9])")
        out += [Span("CUSTOM", m.start(), m.end(), 1.0) for m in rx.finditer(text)]
    return out


def resolve(spans: list[Span]) -> list[Span]:
    """Drop overlaps: keep high-risk first, then the longer span, then the higher score. Among high-risk spans a
    pattern span (layer 1, often with a checksum) comes first, so a PAN stays PAN and not the model's ACCOUNT."""
    order = sorted(spans, key=lambda s: (s.type not in HIGH_RISK, not (s.type in HIGH_RISK and s.layer == 1),
                                         -(s.end - s.start), -s.score))
    kept: list[Span] = []
    for s in order:
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)
