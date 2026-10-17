"""Names for unnamed files (PLAN2 P2-3.2).

1. Detect: a generic name (Untitled, IMG_2044, WhatsApp Image ..., Screenshot_..., Book1, a hash, a number).
2. Find a name, from the first source that gives a good one:
   - a document: its title, first heading or first good line
   - a screenshot or a scan: the best line of its OCR text
   - a photo: the date (from the name, EXIF or the file) and a SigLIP 2 label ("Whiteboard", "Receipt")
   - audio and video: the transcript (the local model makes a short title), else the date
   - anything else: the local model reads the start of the text
3. Style: the user's naming style (learn.naming_style), else "YYYY-MM-DD Title" for media and "Title" for
   documents. No category prefix: a wrong guess of the classifier ("Invoice - Insurance Policy") misleads.
4. Review: every rename opens in the review view. Apply runs them as one journal group, so one undo
   reverts all. A file never leaves its folder here.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime

from . import db
from .fs import ops
from .fs.kinds import ext_of, kind_info

log = logging.getLogger("raven.naming")

# Each pattern matches the stem (the name without its extension).
_GENERIC = [
    r"(untitled|document|download|downloads|scan|scanned|img|image|file|new file|doc|copy of|final|output|unnamed|"
    r"new document|new text document|new microsoft \w+ document|new folder|noname|no name|temp|tmp|test|draft|"
    r"attachment|export|print|printout|page|picture|photo|video|audio|recording|record|voice|memo|clip|movie|track|"
    r"sound|song|screen recording|screencast|capture|snapshot)"
    r"[\s_\-()\d.]*([a-z]{0,4}\d*)?[\s_\-()\d]*",
    r"(img|dsc|dscn|dscf|pxl|vid|mvimg|mov|gopr|gp|dji|sam|p|wp|imag|photo|vlc-record|signal|telegram|pict|cimg|sdc|"
    r"hpim|kimg|mvc|clip|rec|voice|audio|ptt|aud|sound|recording|screenshot|screen ?shot|screen[-_ ]?recording|"
    r"screencast|scrot|capture|snip|snapshot)[-_ ]*\d[\d_\-. ]*(?:[a-z]{1,4}\d*)?(?:[-_ (]*\d+\)?)?(?:[-_ ~]\w{1,6})?",
    r"(screenshot|screen ?shot|screen recording|screencast)(?:[\s_\-]+from)?[\s_\-]+\d{2,4}[\d\s_\-.:,at]*(?:[ap]\.?m\.?)?"
    r"(?:[\s_\-]*\(\d+\))?",
    r"whatsapp (image|video|audio|ptt|document)[\s\d\-_.:at]*(?:\(\d+\))?",
    r"(img|vid|aud|ptt|doc)-\d{8}-wa\d{3,5}(?:\s*\(\d+\))?",
    r"(doc|book|presentation|drawing|publication|sheet|workbook|untitled spreadsheet|untitled document|"
    r"untitled presentation)\s*\d*(?:\s*\(\d+\))?",
    r"(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{10,}",  # a hash (digits and letters)
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # a UUID
    r"[\d_\-. ()]+",  # numbers only (a date, a counter)
    r"file[_\- ]?example[\w\-]*",
    r"(dl|download|attachment|file)[_\-]?\d+",
]
GENERIC = re.compile(r"^(?:" + "|".join(f"(?:{p})" for p in _GENERIC) + r")$", re.I)

_DATE_IN_NAME = [
    re.compile(r"(?<!\d)(20\d{2}|19\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])[-_.](0[1-9]|1[0-2])[-_.](20\d{2})(?!\d)"),
]
MEDIA_GROUPS = {"image", "video", "audio"}
# Labels for photos (SigLIP 2 zero-shot). The prompt is "a photo of <label>".
PHOTO_LABELS = {
    "Whiteboard": "a whiteboard with writing", "Receipt": "a shop receipt", "Document": "a printed document page",
    "Notes": "handwritten notes on paper", "Slide": "a presentation slide on a projector screen",
    "Screen": "a computer screen", "Beach": "a beach and the sea", "Mountains": "mountains", "Sunset": "a sunset",
    "City": "a city street", "Food": "a plate of food", "Group photo": "a group of people posing",
    "Selfie": "a selfie of a person", "Pet": "a pet dog or cat", "Car": "a car", "Building": "a building",
    "Flowers": "flowers", "Nature": "trees and a forest", "Event": "a crowd at a concert or event",
    "Book": "a book page", "ID card": "an identity card", "Ticket": "a printed ticket", "Chart": "a chart or graph",
    "Map": "a map", "Meme": "a meme with text", "Sky": "the sky and clouds", "Room": "the inside of a room",
    "Sports": "people playing sports", "Baby": "a baby", "Party": "a birthday party with a cake",
}
LABEL_MIN = 0.09  # SigLIP 2 cosine. Clear photos score 0.11 to 0.14 on testdata. Patterns stay under 0.09 or close to a second label
LABEL_GAP = 0.01
PLANS: dict[str, dict] = {}
_label_vecs: dict = {}
_label_lock = threading.Lock()
SMALL_WORDS = {"of", "the", "and", "for", "to", "in", "a", "an", "on", "at", "by", "with", "from"}


def is_generic(name: str) -> bool:
    stem = name
    if "." in name.lstrip("."):
        stem = name.rsplit(".", 1)[0]
        # Compound extensions: archive.tar.gz -> archive
        if stem.lower().endswith(".tar"):
            stem = stem[:-4]
    stem = stem.strip()
    if not stem:
        return False
    return bool(GENERIC.match(stem))


def clean_title(title: str, words: int = 7) -> str:
    t = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", title or "").strip(" .-_,;")
    t = re.sub(r"\s+", " ", t)
    t = " ".join(t.split()[:words])
    if t.isupper() or t.islower():
        t = " ".join(w.lower() if i and w.lower() in SMALL_WORDS else (w[:1].upper() + w[1:].lower() if w.isalpha() else w)
                     for i, w in enumerate(t.split()))
    return t[:70].strip(" .-_,;")


def _good_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 4 or len(s) > 90:
        return False
    letters = sum(ch.isalpha() for ch in s)
    if letters < 4 or letters / max(1, len(s)) < 0.6:
        return False
    if re.match(r"^(page \d|\d{1,2}:\d{2}|http|www\.|©)", s, re.I):
        return False
    return len(s.split()) >= 2 or (letters >= 6 and s[:1].isupper())


def best_line(text: str) -> str:
    """The first line that reads like a title: mostly letters, 2 or more words, not a clock or a URL."""
    for line in (text or "").splitlines()[:25]:
        if _good_line(line):
            return line.strip()
    return ""


def text_title(text: str) -> str:
    """A title from OCR or document text. A short first line ("Order placed") joins the next good line,
    because the subject ("Blue Heron Bookstore") often follows a status phrase."""
    good = [ln.strip() for ln in (text or "").splitlines()[:25] if _good_line(ln)]
    if not good:
        return ""
    t = good[0]
    if len(t.split()) <= 2 and len(good) > 1:
        t = f"{t} - {good[1]}"
    return clean_title(t, 8)


def date_from_name(stem: str) -> str | None:
    for i, pat in enumerate(_DATE_IN_NAME):
        m = pat.search(stem)
        if m:
            y, mo, d = (m.group(1), m.group(2), m.group(3)) if i == 0 else (m.group(3), m.group(2), m.group(1))
            try:
                datetime(int(y), int(mo), int(d))
            except ValueError:
                continue
            if 1990 <= int(y) <= datetime.now().year + 1:
                return f"{y}-{mo}-{d}"
    return None


def exif_date(path: str) -> str | None:
    try:
        from PIL import Image

        from .fs.atime import keep_atime

        with keep_atime(path), Image.open(path) as im:
            ex = im.getexif()
            raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
    except Exception:  # noqa: BLE001 - no EXIF is normal
        return None
    if not raw:
        return None
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})", str(raw))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m and m.group(1) != "0000" else None


def _label_vectors():
    with _label_lock:
        if not _label_vecs:
            import numpy as np

            from .media import siglip

            names = list(PHOTO_LABELS)
            vecs = np.stack([siglip.model.embed_text(f"a photo of {PHOTO_LABELS[n]}") for n in names])
            _label_vecs.update(names=names, vecs=vecs)
        return _label_vecs["names"], _label_vecs["vecs"]


def photo_label(file_id: int) -> str | None:
    """The best label for an indexed image from its SigLIP 2 vector, or None when no label is clear."""
    try:
        import numpy as np

        from .media import siglip

        if not siglip.available():
            return None
        row = db.conn().execute(
            "SELECT v.embedding FROM visual_vec v JOIN visual x ON x.id = v.rowid WHERE x.file_id=? AND x.kind='image' LIMIT 1",
            (file_id,),
        ).fetchone()
        if row is None:
            return None
        vec = np.frombuffer(row[0], dtype=np.float32)
        names, vecs = _label_vectors()
        sims = vecs @ vec
        order = np.argsort(-sims)
        best, second = float(sims[order[0]]), float(sims[order[1]])
        if best < LABEL_MIN or best - second < LABEL_GAP:
            return None
        return names[int(order[0])]
    except Exception as e:  # noqa: BLE001 - a label is optional
        log.debug("photo label: %s", e)
        return None


TITLE_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
TITLE_SYSTEM = (
    "You name a file. Read the start of its text and give a short, plain title of 2 to 6 words, as a person would "
    "name the file. No dates, no file extension, no quotes. Use the main subject."
)


def llm_title(text: str, what: str) -> str | None:
    from .router import local

    if not text.strip() or not local.available():
        return None
    try:
        data = local.chat(TITLE_SYSTEM, f"This is {what}. Text:\n{text[:700]}", TITLE_SCHEMA, max_tokens=40)
    except Exception as e:  # noqa: BLE001
        log.debug("llm title: %s", e)
        return None
    t = clean_title(str(data.get("title") or ""), 6)
    return t if len(t) >= 3 else None


def _text_of(file_id: int, transcript: bool = False, limit: int = 3) -> str:
    q = "SELECT text FROM chunks WHERE file_id=? AND at IS " + ("NOT NULL" if transcript else "NULL") + " ORDER BY id LIMIT ?"
    return "\n".join(r[0] or "" for r in db.conn().execute(q, (file_id, limit)))


def style() -> dict:
    from . import learn

    try:
        return learn.naming_style()
    except Exception:  # noqa: BLE001 - before learn has a style
        return {}


def compose(title: str | None, date: str | None, ext: str, media: bool, st: dict | None = None) -> str | None:
    """Join the parts in the user's style. None when there is nothing to say."""
    st = st or {}
    sep = st.get("sep", " ")
    case = st.get("case")
    date_first = st.get("date", "first" if media else "none")
    t = (title or "").strip()
    if case == "lower":
        t = t.lower()
    elif case == "title":
        t = " ".join(w if i and w.lower() in SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(t.split()))
    if sep != " ":
        t = re.sub(r"\s+", sep, t)
    parts = []
    if date and date_first == "first":
        parts.append(date)
    if t:
        parts.append(t)
    if date and date_first == "last":
        parts.append(date)
    if not parts or (not t and not date):
        return None
    joiner = sep if sep != " " else " "
    out = joiner.join(parts)
    return out + (f".{ext}" if ext else "")


def suggest_one(path: str, row: dict | None = None, use_llm: bool = True, st: dict | None = None,
                force: bool = False) -> dict | None:
    """A new name for one file: {src, name, source} or None. `force`: also for names that are not generic."""
    name = os.path.basename(path)
    if not force and not is_generic(name):
        return None
    if row is None:
        r = db.conn().execute("SELECT id, title, category, mtime FROM files WHERE path=?", (path,)).fetchone()
        row = dict(r) if r else {}
    ext = ext_of(name)
    label, group = kind_info(name, False)
    media = group in MEDIA_GROUPS
    stem = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    fid = row.get("id")
    title, source = None, None
    cat = row.get("category")
    if group == "image":
        ocr = _text_of(fid) if fid else ""
        line = text_title(ocr)
        if line:
            title, source = line, "from the text in the image"
        if not title and fid:
            lab = photo_label(fid)
            if lab:
                title, source = lab, "from what the image shows"
        if not title and cat == "Screenshot":
            title, source = "Screenshot", "screenshot"
    elif group in ("video", "audio"):
        tr = _text_of(fid, transcript=True, limit=4) if fid else ""
        if tr.strip():
            t = llm_title(tr, f"the transcript of a {group} recording") if use_llm else None
            if t:
                title, source = t, "from what is said"
            else:
                words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", tr)][:5]
                if len(words) >= 3:
                    title, source = clean_title(" ".join(words), 5), "from the first words said"
        if not title:
            title = "Recording" if group == "audio" else ("Screen recording" if re.match(r"(?i)screen", stem) else "Video")
            source = "the date"
    else:
        t = row.get("title") or ""
        if path.lower().endswith(".pdf"):
            from .index.extract import pdf_title

            t = pdf_title(path) or t  # the largest text on page 1: the index may hold an older title
        if t and _good_line(t):
            title, source = clean_title(t), "from the title"
        elif fid:
            text = _text_of(fid)
            line = best_line(text)
            if line:
                title, source = clean_title(line), "from the first line"
            elif use_llm and text.strip():
                t2 = llm_title(text, "a document")
                if t2:
                    title, source = t2, "from the local model"
    if not title:
        return None
    date = None
    if media:
        date = date_from_name(stem) or (exif_date(path) if group == "image" else None)
        if not date:
            try:
                date = time.strftime("%Y-%m-%d", time.localtime(os.stat(path).st_mtime))
            except OSError:
                date = None
    new = compose(title, date, ext, media, st if st is not None else style())
    if not new or new == name:
        return None
    return {"src": path, "name": new, "source": source}


def scan(root: str | None = None, use_llm: bool = True, limit: int = 2000) -> dict:
    """A rename plan for every file with a generic name below `root` (or in the whole index)."""
    t0 = time.perf_counter()
    c = db.conn()
    from .privacy.scope import is_private, require_ai

    if root:
        root = os.path.abspath(root)
        require_ai(root)  # a private folder (PLAN4 7.6)
        if os.path.isfile(root):
            rows = c.execute("SELECT id, path, name, title, category, mtime FROM files WHERE path=?", (root,)).fetchall()
            force = True
        else:
            like = root.rstrip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            rows = c.execute(
                "SELECT id, path, name, title, category, mtime FROM files WHERE is_dir=0 AND light=0 AND (parent=? OR path LIKE ? ESCAPE '\\')",
                (root, like),
            ).fetchall()
            force = False
    else:
        rows = c.execute("SELECT id, path, name, title, category, mtime FROM files WHERE is_dir=0 AND light=0").fetchall()
        force = False
    from .storage import protect

    rows = [r for r in rows if not is_private(r["path"])]  # rows from before the rule
    st = style()
    items, taken = [], set()
    for r in rows:
        if len(items) >= limit:
            break
        if not force and not is_generic(r["name"]):
            continue
        if r["category"] in ("Secret", "Project file") or not os.path.isfile(r["path"]):
            continue
        parent = os.path.dirname(r["path"])
        if protect.is_project_root(parent) or protect.is_software_dir(parent) or protect.user_protected(parent):
            continue
        s = suggest_one(r["path"], dict(r), use_llm=use_llm, st=st, force=force)
        if not s:
            continue
        dst = _unique(os.path.join(parent, s["name"]), taken)
        s["name"] = os.path.basename(dst)
        s["dst"] = dst
        items.append(s)
    plan = {"id": uuid.uuid4().hex[:10], "root": root, "items": items, "ms": round((time.perf_counter() - t0) * 1000)}
    for k in sorted(PLANS)[:-10]:
        PLANS.pop(k, None)
    PLANS[plan["id"]] = plan
    return plan


def count_generic(root: str | None = None) -> tuple[int, int]:
    """(files with a generic name, their total size) for the Suggestions row. Names only: fast."""
    c = db.conn()
    q = "SELECT name, size, path FROM files WHERE is_dir=0 AND light=0"
    n = size = 0
    for name, sz, path in c.execute(q):
        if root and not (path.startswith(root.rstrip("/") + "/")):
            continue
        if is_generic(name):
            n += 1
            size += sz or 0
    return n, size


def _unique(path: str, taken: set[str]) -> str:
    stem, ext = os.path.splitext(path)
    cand, n = path, 2
    while os.path.lexists(cand) or cand in taken:
        cand = f"{stem} ({n}){ext}"
        n += 1
    taken.add(cand)
    return cand


def apply(plan_id: str, items: list[dict]) -> dict:
    """Rename the kept items (the user may edit each name) as one journal group."""
    plan = PLANS.pop(plan_id, None)
    if plan is None:
        raise ValueError("This plan is gone. Suggest again.")
    allowed = {it["src"] for it in plan["items"]}
    g = ops.Group("name", f"Name {len(items)} file{'s' if len(items) != 1 else ''}")
    done, errors, renamed = 0, [], []
    try:
        for it in items:
            src, new = os.path.abspath(it["src"]), str(it.get("name") or "").strip()
            if src not in allowed:
                errors.append(f"{os.path.basename(src)} is not in the plan")
                continue
            if not new or "/" in new or "\\" in new:
                errors.append(f"{new!r} is not a valid name")
                continue
            if not os.path.lexists(src):
                errors.append(f"{os.path.basename(src)} is gone")
                continue
            dst = os.path.join(os.path.dirname(src), new)
            if os.path.lexists(dst) and os.path.basename(dst).lower() != os.path.basename(src).lower():
                new = os.path.basename(ops.unique_path(dst))
            try:
                renamed.append(ops.rename(src, new, source="name", group=g))
                done += 1
            except ops.OpError as e:
                errors.append(str(e))
    finally:
        g.commit()
    try:
        from .index.indexer import indexer

        indexer.update_paths([it["src"] for it in items] + renamed)
    except Exception:  # noqa: BLE001 - the watcher catches up
        pass
    return {"renamed": done, "errors": errors, "label": g.label, "journal": ops.peek()}
