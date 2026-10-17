"""Rule-based filter parser (section 9.3 fallback). The LLM parser is search/nl.py.

Tokens:   type:pdf  ext:md  in:Downloads  after:2026-01  before:2026-06-30  >2MB  <500KB
Phrases:  "bigger than 2MB", "smaller than 1 GB", "last week", "last month", "last year",
          "today", "yesterday", "older than 6 months", "last 10 days", "not opened for 90 days",
          "from March", "in march 2025", "last semester", and plain type words such as "pdf" or "images".
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from ..platform import like_escape

TYPE_GROUPS = {
    "pdf": ["pdf"],
    "doc": ["doc", "docx", "odt", "rtf", "pdf", "md", "txt"],
    "docs": ["doc", "docx", "odt", "rtf", "pdf", "md", "txt"],
    "document": ["doc", "docx", "odt", "rtf", "pdf", "md", "txt"],
    "word": ["doc", "docx"],
    "slides": ["ppt", "pptx", "odp"],
    "sheet": ["xls", "xlsx", "ods", "csv"],
    "spreadsheet": ["xls", "xlsx", "ods", "csv"],
    "image": ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "avif", "jxl", "svg"],
    "photo": ["jpg", "jpeg", "png", "heic", "webp", "avif", "jxl"],
    "video": ["mp4", "mkv", "webm", "mov", "avi"],
    "audio": ["mp3", "wav", "flac", "ogg", "m4a", "opus"],
    "code": ["py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "java", "go", "rs", "rb", "sh", "sql", "kt", "dart"],
    "archive": ["zip", "7z", "tar", "gz", "zst", "xz", "bz2", "rar"],
    "text": ["txt", "md", "log"],
    "markdown": ["md", "markdown"],
}
# Plain words that act as a type filter only when they appear as a whole word.
TYPE_WORDS = {
    "pdf": "pdf", "pdfs": "pdf", "images": "image", "photos": "photo", "pictures": "image",
    "videos": "video", "spreadsheets": "sheet", "slides": "slides", "screenshots": "image",
}

PROP_TOKEN = re.compile(r"(?<!\S)(owner|perm|is|tag):(\S+)", re.I)
UNITS = {"b": 1, "kb": 1024, "k": 1024, "mb": 1024**2, "m": 1024**2, "gb": 1024**3, "g": 1024**3, "tb": 1024**4}


@dataclass
class Filters:
    text: str = ""
    exts: list[str] = field(default_factory=list)
    after: int | None = None   # unix seconds, inclusive
    before: int | None = None  # unix seconds, exclusive
    min_size: int | None = None
    max_size: int | None = None
    folder: str | None = None
    chips: list[dict] = field(default_factory=list)
    # PLAN2 P2-5: owner:harsh  perm:755 (or perm:+x, perm:w)  is:link  is:broken  is:hidden  is:system  tag:work
    owner: str | None = None
    perm: str | None = None
    is_: set = field(default_factory=set)
    tags: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.exts or self.after or self.before or self.min_size or self.max_size or self.folder or self.owner
                    or self.perm or self.is_ or self.tags)

    @property
    def needs_post(self) -> bool:
        """Filters that the index cannot answer: Raven checks each result on disk."""
        return bool(self.owner or self.perm or self.tags or self.is_ & {"system"})

    def post(self, path: str) -> bool:
        import os
        import stat as st_

        try:
            s = os.lstat(path)
        except OSError:
            return False
        if self.owner:
            from ..platform import owner_group

            if self.owner.lower() not in [x.lower() for x in owner_group(s)]:
                return False
        if self.perm:
            mode = s.st_mode & 0o7777
            p = self.perm
            if re.fullmatch(r"[0-7]{3,4}", p):
                if mode != int(p, 8):
                    return False
            else:
                # perm:x, perm:+x, perm:w: someone may run, write or read the item.
                want = {"r": 0o444, "w": 0o222, "x": 0o111}
                for ch in p.lstrip("+"):
                    if ch in want and not mode & want[ch]:
                        return False
        if "system" in self.is_:
            from ..fs.props import is_system

            if not is_system(path if st_.S_ISDIR(s.st_mode) else os.path.dirname(path)):
                return False
        if self.tags:
            from ..fs.props import read_tags

            have = {t.lower() for t in read_tags(path)}
            if not all(t.lower() in have for t in self.tags):
                return False
        return True

    def sql(self, alias: str = "f") -> tuple[str, list]:
        conds, args = [], []
        if self.exts:
            conds.append(f"{alias}.ext IN ({','.join('?' * len(self.exts))})")
            args += self.exts
        if self.after is not None:
            conds.append(f"{alias}.mtime >= ?")
            args.append(self.after)
        if self.before is not None:
            conds.append(f"{alias}.mtime < ?")
            args.append(self.before)
        if self.min_size is not None:
            conds.append(f"{alias}.size >= ?")
            args.append(self.min_size)
        if self.max_size is not None:
            conds.append(f"{alias}.size <= ?")
            args.append(self.max_size)
        if "hidden" in self.is_:
            conds.append(f"{alias}.is_hidden = 1")
        if self.folder:
            conds.append(f"{alias}.parent LIKE ? ESCAPE '\\'")
            args.append("%" + like_escape(self.folder) + "%")
        return (" AND ".join(conds) or "1"), args

    def match(self, row: dict) -> bool:
        if self.exts and row.get("ext") not in self.exts:
            return False
        m = row.get("mtime") or 0
        if self.after is not None and m < self.after:
            return False
        if self.before is not None and m >= self.before:
            return False
        s = row.get("size")
        if self.min_size is not None and (s is None or s < self.min_size):
            return False
        if self.max_size is not None and (s is None or s > self.max_size):
            return False
        if self.folder and self.folder.lower() not in (row.get("parent") or "").lower():
            return False
        return True


def _size(num: str, unit: str) -> int:
    return int(float(num) * UNITS[unit.lower()])


def _parse_date(s: str, end: bool) -> int | None:
    """'2026', '2026-03', '2026-03-14'. With end=True return the first second after the period."""
    try:
        parts = [int(x) for x in s.split("-")]
    except ValueError:
        return None
    if len(parts) == 1:
        d = date(parts[0] + (1 if end else 0), 1, 1)
    elif len(parts) == 2:
        y, m = parts
        if end:
            d = date(y, m, calendar.monthrange(y, m)[1]) + timedelta(days=1)
        else:
            d = date(y, m, 1)
    else:
        d = date(*parts[:3]) + (timedelta(days=1) if end else timedelta())
    return int(datetime(d.year, d.month, d.day).timestamp())


def _human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:g} {u}" if u == "B" else f"{n:.3g} {u}"
        n /= 1024
    return str(n)


SIZE_TOKEN = re.compile(r"^(>=?|<=?)(\d+(?:\.\d+)?)(b|kb|k|mb|m|gb|g|tb)$", re.I)
SIZE_PHRASE = re.compile(r"\b(bigger|larger|more|greater|over|smaller|less|under)\s+(?:than\s+)?(\d+(?:\.\d+)?)\s*(b|kb|k|mb|m|gb|g|tb)\b", re.I)
AGE_PHRASE = re.compile(
    r"\b(?:(older|newer)\s+than|(?:in\s+the\s+)?(?:last|past)|not\s+(?:opened|used|touched|changed|modified)\s+(?:for|in)(?:\s+the\s+last)?)"
    r"\s+(\d+)\s+(day|week|month|year)s?\b", re.I)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
MONTH_PHRASE = re.compile(r"\b(?:from|in|during|of)\s+(" + "|".join(m[:3] + r"(?:" + m[3:] + r")?" for m in MONTHS) + r")(?:\s+(\d{4}))?\b", re.I)
SEMESTER = re.compile(r"\b(this|last|previous)\s+(semester|sem)\b", re.I)
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
NUM_WORDS = {"a few": 3, "a couple of": 2, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "couple of": 2, "few": 3}
NUM_WORD = re.compile(r"\b(" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")\s+(day|week|month|year)s?\b", re.I)
# "2 weeks ago": a window around that time, from N+1 to N-1 units back.
AGO = re.compile(r"\b(\d+)\s+(day|week|month|year)s?\s+ago\b", re.I)
REL_DATE = re.compile(r"\b(today|yesterday|this week|last week|this month|last month|this year|last year)\b", re.I)


def parse(q: str) -> Filters:
    f = Filters()
    words = []
    text = q

    def chip(kind: str, label: str, token: str):
        f.chips.append({"kind": kind, "label": label, "token": token})

    for m in list(PROP_TOKEN.finditer(text)):
        key, val = m.group(1).lower(), m.group(2)
        if key == "owner":
            f.owner = val
            chip("owner", f"owner {val}", m.group(0))
        elif key == "perm":
            f.perm = val
            chip("perm", f"permissions {val}", m.group(0))
        elif key == "is" and val.lower() in ("link", "broken", "hidden", "system"):
            f.is_.add(val.lower())
            chip("is", val.lower(), m.group(0))
        elif key == "tag":
            f.tags.append(val)
            chip("tag", f"tag {val}", m.group(0))
        else:
            continue
        text = text.replace(m.group(0), " ")

    # "two weeks" -> "2 weeks", so the phrases below read number words too.
    text = NUM_WORD.sub(lambda m: f"{NUM_WORDS[m.group(1).lower()]} {m.group(2)}s", text)

    m = AGO.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        now = int(time.time())
        f.after = now - (n + 1) * UNIT_DAYS[unit] * 86400
        f.before = now - max(n - 1, 0) * UNIT_DAYS[unit] * 86400
        chip("date", f"about {n} {unit}{'s' if n > 1 else ''} ago", m.group(0))
        text = text.replace(m.group(0), " ")

    m = SIZE_PHRASE.search(text)
    if m:
        n = _size(m.group(2), m.group(3))
        if m.group(1).lower() in ("smaller", "less", "under"):
            f.max_size = n
            chip("size", f"< {_human_size(n)}", m.group(0))
        else:
            f.min_size = n
            chip("size", f"> {_human_size(n)}", m.group(0))
        text = text.replace(m.group(0), " ")

    m = AGE_PHRASE.search(text) if not (f.after or f.before) else None
    if m:
        days = int(m.group(2)) * UNIT_DAYS[m.group(3).lower()]
        cut = int(time.time()) - days * 86400
        word = (m.group(1) or "").lower()
        # "older than" and "not opened for" mean before the cut. "newer than" and "last N days" mean after it.
        if word == "older" or m.group(0).lower().startswith("not"):
            f.before = cut
            chip("date", f"older than {m.group(2)} {m.group(3).lower()}s", m.group(0))
        else:
            f.after = cut
            chip("date", f"last {m.group(2)} {m.group(3).lower()}s", m.group(0))
        text = text.replace(m.group(0), " ")

    m = MONTH_PHRASE.search(text)
    if m and not (f.after or f.before):
        month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith(m.group(1).lower()[:3]))
        now = datetime.now()
        year = int(m.group(2)) if m.group(2) else (now.year if month <= now.month else now.year - 1)
        f.after = _parse_date(f"{year}-{month:02d}", end=False)
        f.before = _parse_date(f"{year}-{month:02d}", end=True)
        chip("date", f"{MONTHS[month - 1].title()} {year}", m.group(0))
        text = text.replace(m.group(0), " ")

    m = SEMESTER.search(text)
    if m and not (f.after or f.before):
        # Indian universities: the odd semester runs July to December, the even one January to June.
        now = datetime.now()
        start = datetime(now.year, 1 if now.month <= 6 else 7, 1)
        if m.group(1).lower() != "this":
            start = datetime(start.year - 1, 7, 1) if start.month == 1 else datetime(start.year, 1, 1)
        end = datetime(start.year + (1 if start.month == 7 else 0), 1 if start.month == 7 else 7, 1)
        f.after, f.before = int(start.timestamp()), int(end.timestamp())
        chip("date", f"{m.group(1).lower()} semester", m.group(0))
        text = text.replace(m.group(0), " ")

    m = REL_DATE.search(text) if not (f.after or f.before) else None
    if m:
        now = datetime.now()
        today = datetime(now.year, now.month, now.day)
        key = m.group(1).lower()
        start = end = None
        if key == "today":
            start = today
        elif key == "yesterday":
            start, end = today - timedelta(days=1), today
        elif key == "this week":
            start = today - timedelta(days=today.weekday())
        elif key == "last week":
            end = today - timedelta(days=today.weekday())
            start = end - timedelta(days=7)
        elif key == "this month":
            start = today.replace(day=1)
        elif key == "last month":
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        elif key == "this year":
            start = today.replace(month=1, day=1)
        elif key == "last year":
            end = today.replace(month=1, day=1)
            start = end.replace(year=end.year - 1)
        f.after = int(start.timestamp()) if start else None
        f.before = int(end.timestamp()) if end else None
        chip("date", key, m.group(0))
        text = text.replace(m.group(0), " ")

    for tok in text.split():
        low = tok.lower()
        if ":" in low and not low.startswith(("http:", "https:")):
            key, _, val = low.partition(":")
            raw_val = tok.split(":", 1)[1]
            if key in ("type", "ext", "kind") and val:
                exts = []
                # "type:pdf,docx" names several types.
                for v in val.split(","):
                    if v:
                        exts += TYPE_GROUPS.get(v.rstrip("s"), TYPE_GROUPS.get(v, [v.lstrip(".")]))
                f.exts = sorted(set(f.exts) | set(exts))
                chip("type", val, tok)
                continue
            if key in ("after", "since") and val:
                f.after = _parse_date(val, end=False)
                chip("date", f"after {val}", tok)
                continue
            if key in ("before", "until") and val:
                f.before = _parse_date(val, end=False)
                chip("date", f"before {val}", tok)
                continue
            if key == "in" and val:
                f.folder = raw_val
                chip("folder", f"in {raw_val}", tok)
                continue
        m = SIZE_TOKEN.match(low)
        if m:
            n = _size(m.group(2), m.group(3))
            if m.group(1).startswith(">"):
                f.min_size = n
                chip("size", f"> {_human_size(n)}", tok)
            else:
                f.max_size = n
                chip("size", f"< {_human_size(n)}", tok)
            continue
        if low in TYPE_WORDS and not f.exts:
            f.exts = TYPE_GROUPS[TYPE_WORDS[low]]
            chip("type", TYPE_WORDS[low], tok)
            # Keep the word in the text too. It can carry meaning ("photos of the beach").
        words.append(tok)
    f.text = " ".join(words).strip()
    return f


def now_s() -> int:
    return int(time.time())
