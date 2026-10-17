"""Classification cascade (section 9.4): rules, then embedding prototypes, then the local LLM.

Each file gets a category, the source (rule, embedding, llm), a confidence and a one-line
reason. Most files stop at the rules. The cheaper step always runs first.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import threading

from . import db, events
from .fs.kinds import ext_of

log = logging.getLogger("raven.classify")

VERSION = 3
# Cosine similarity to the best prototype, and the gap to the second one, for an embedding answer.
EMB_MIN = 0.32
EMB_GAP = 0.05
LLM_BATCH = 10

# Category -> a description for its embedding prototype. The LLM sees the same list.
CATEGORIES: dict[str, str] = {
    "Lecture notes": "lecture notes for a university course unit: definitions, topics, examples and explanations",
    "Assignment": "a homework assignment or problem sheet with numbered questions to answer",
    "Exam": "an exam hall ticket, admit card, timetable of papers, or question paper",
    "Syllabus": "a course syllabus with units, topics, credits and reference books",
    "Resume": "a resume or curriculum vitae with skills, experience, education and contact details",
    "Cover letter": "a cover letter to a hiring manager to apply for a job",
    "Invoice": "an invoice for work done with items, amounts, tax and payment terms",
    "Receipt": "a receipt or order confirmation for a purchase with the price paid",
    "Bank statement": "a bank account statement with transactions, balance and account number",
    "Bill": "a utility or phone bill with charges, due date and amount payable",
    "Ticket": "a travel ticket or booking for a train, flight or bus with passenger and seat",
    "Lease": "a rent or lease agreement between a landlord and a tenant with rent and deposit",
    "Report": "a project report or essay with an introduction, sections and a conclusion",
    "Recipe": "a cooking recipe with ingredients and steps",
    "Travel plan": "a travel itinerary or trip plan with days, places and hotels",
    "Meeting notes": "minutes or notes from a meeting with attendees, decisions and action items",
    "Personal notes": "a personal to-do list, shopping list or quick notes",
    "Notice": "a notice or circular from a society, college or office",
    "Certificate": "a certificate of participation, achievement, completion or merit",
}
# Rule-only categories (no prototype): types where the extension or the name says enough.
RULE_ONLY = ["Photo", "Screenshot", "Image", "Video", "Audio", "Installer", "Archive", "Code", "Log", "Data",
             "Spreadsheet", "Presentation", "Ebook", "Font", "Disk image", "Project file", "Secret"]

# Categories that add to the Kind column. "Video" next to "MP4 video" says nothing new.
SHOWN = {*CATEGORIES, "Screenshot", "Secret", "Photo"}

_EXT = {
    **{e: "Video" for e in ("mp4", "mkv", "webm", "mov", "avi", "m4v", "part")},
    **{e: "Audio" for e in ("mp3", "wav", "flac", "ogg", "m4a", "opus", "aac")},
    **{e: "Installer" for e in ("deb", "rpm", "appimage", "exe", "msi", "dmg", "pkg", "apk", "flatpakref", "run")},
    **{e: "Archive" for e in ("zip", "7z", "tar", "gz", "tgz", "zst", "xz", "bz2", "rar")},
    **{e: "Code" for e in ("py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "java", "kt", "go", "rs", "rb", "sh",
                            "sql", "dart", "swift", "php", "cs", "lua", "ipynb", "css", "scss", "vue", "svelte")},
    **{e: "Log" for e in ("log",)},
    **{e: "Data" for e in ("json", "xml", "yaml", "yml", "toml", "parquet", "db", "sqlite", "npy")},
    **{e: "Spreadsheet" for e in ("xlsx", "xls", "ods")},
    **{e: "Presentation" for e in ("pptx", "ppt", "odp", "key")},
    **{e: "Ebook" for e in ("epub", "mobi", "azw3", "djvu")},
    **{e: "Font" for e in ("ttf", "otf", "woff", "woff2")},
    **{e: "Disk image" for e in ("iso", "img", "qcow2", "vdi", "vmdk")},
    **{e: "Image" for e in ("svg", "gif", "bmp", "tif", "tiff", "ico", "psd", "xcf")},
}
PHOTO_EXTS = {"jpg", "jpeg", "heic", "jxl", "avif", "png", "webp", "raw", "cr2", "nef", "arw", "dng"}
_NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(screen ?shot|screenshot|scrot|^screen[-_ ]?\d)", re.I), "Screenshot"),
    (re.compile(r"^(img|dsc|dscn|pxl|photo|wp|mvimg)[-_ ]?\d", re.I), "Photo"),
    (re.compile(r"\b(resume|cv|curriculum[-_ ]vitae)\b", re.I), "Resume"),
    (re.compile(r"cover[-_ ]?letter", re.I), "Cover letter"),
    (re.compile(r"\binvoice", re.I), "Invoice"),
    (re.compile(r"\b(receipt|order[-_ ]confirm)", re.I), "Receipt"),
    (re.compile(r"\bstatement\b|\bpassbook\b", re.I), "Bank statement"),
    (re.compile(r"\bsyllabus\b", re.I), "Syllabus"),
    (re.compile(r"hall[-_ ]?ticket|admit[-_ ]?card|\bexam\b|question[-_ ]paper", re.I), "Exam"),
    (re.compile(r"\b(assignment|homework|hw\d+|lab[-_ ]?\d+)\b", re.I), "Assignment"),
    (re.compile(r"\b(lecture|unit[-_ ]?\d+|_unit\d+|notes?[-_ ]unit)", re.I), "Lecture notes"),
    (re.compile(r"\b(lease|rent[-_ ]agreement)\b", re.I), "Lease"),
    (re.compile(r"\brecipe", re.I), "Recipe"),
    (re.compile(r"\b(itinerary|trip[-_ ]plan)\b", re.I), "Travel plan"),
    (re.compile(r"\b(meeting|minutes)\b", re.I), "Meeting notes"),
    (re.compile(r"\b(passwords?|passwd|secrets?|credentials?|\.env)\b", re.I), "Secret"),
    (re.compile(r"\b(todo|to-do|grocery|shopping[-_ ]list|quick[-_ ]notes)\b", re.I), "Personal notes"),
    (re.compile(r"\bnotice\b|\bcircular\b", re.I), "Notice"),
    (re.compile(r"\bcertificate\b", re.I), "Certificate"),
    (re.compile(r"\breport\b|\bessay\b", re.I), "Report"),
]
PROJECT_MARKERS = ("/node_modules/", "/.git/", "/__pycache__/", "/venv/", "/.venv/", "/build/", "/dist/", "/target/")


def rule(path: str, name: str) -> tuple[str, float, str] | None:
    """Step 1. Return (category, confidence, reason) or None."""
    ext = ext_of(name)
    stem = name.rsplit(".", 1)[0]
    if any(m in path for m in PROJECT_MARKERS):
        return "Project file", 0.95, "inside a project build or dependency folder"
    from .storage.protect import is_project_root
    if is_project_root(os.path.dirname(path)):
        return "Project file", 0.9, "in a project folder"
    if ext in ("part", "crdownload", "partial", "tmp"):
        return "Other", 0.9, "an unfinished download"
    for pat, cat in _NAME_RULES:
        m = pat.search(stem.replace("_", " ")) or pat.search(stem)
        if m:
            if cat in ("Screenshot", "Photo") and ext not in PHOTO_EXTS:
                continue
            return cat, 0.9, f"the name has '{m.group(0).strip()}'"
    if ext in _EXT:
        return _EXT[ext], 0.95, f".{ext} file"
    if ext in PHOTO_EXTS:
        return "Photo" if ext in ("jpg", "jpeg", "heic", "jxl", "raw", "cr2", "nef", "arw", "dng") else "Image", 0.8, f".{ext} image"
    if ext == "csv":
        return "Data", 0.8, ".csv table"
    return None


# ---------------------------------------------------------------- embeddings


_protos: dict[str, list[float]] = {}
_protos_model = ""
_lock = threading.Lock()


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def prototypes() -> dict[str, list[float]]:
    from .index import embed

    global _protos_model
    with _lock:
        if _protos and _protos_model == embed.model():
            return _protos
        _protos.clear()
        for cat, desc in CATEGORIES.items():
            _protos[cat] = embed.embed_query(desc)
        _protos_model = embed.model()
        return _protos


def file_vector(file_id: int) -> list[float] | None:
    rows = db.conn().execute(
        "SELECT v.embedding FROM chunks c JOIN chunk_vec v ON v.rowid = c.id WHERE c.file_id=? ORDER BY c.id LIMIT 4",
        (file_id,),
    ).fetchall()
    if not rows:
        return None
    vecs = [_unpack(r[0]) for r in rows]
    return [sum(col) / len(vecs) for col in zip(*vecs)]


def by_embedding(file_id: int) -> tuple[str, float, str] | None:
    """Step 2. Accept when the best prototype is close and clearly ahead of the second."""
    vec = file_vector(file_id)
    if vec is None:
        return None
    scores = sorted(((_cos(vec, p), c) for c, p in prototypes().items()), reverse=True)
    (s1, c1), (s2, c2) = scores[0], scores[1]
    if s1 >= EMB_MIN and s1 - s2 >= EMB_GAP:
        return c1, round(s1, 3), f"close in meaning to {c1.lower()} ({s1:.2f}, next {c2.lower()} {s2:.2f})"
    return None


# ---------------------------------------------------------------- LLM


LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "category": {"type": "string", "enum": [*CATEGORIES, "Other"]},
                    "reason": {"type": "string"},
                },
                "required": ["i", "category", "reason"],
            },
        }
    },
    "required": ["items"],
}
LLM_SYSTEM = (
    "You sort files on a student's laptop into categories. For each numbered file you get its name and the start "
    "of its text. Pick one category from the list, or Other. Give a reason of at most eight words.\nCategories: "
    + "; ".join(f"{c}: {d}" for c, d in CATEGORIES.items())
)


def by_llm(rows: list[dict]) -> dict[int, tuple[str, float, str]]:
    """Step 3. One local LLM call for up to LLM_BATCH files. Returns file id -> result."""
    from .router import local

    c = db.conn()
    lines = []
    for n, r in enumerate(rows, 1):
        ch = c.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id LIMIT 1", (r["id"],)).fetchone()
        text = " ".join((ch[0] if ch else "").split())[:300]
        lines.append(f"{n}. {r['name']}: {text or '(no text)'}")
    data = local.chat(LLM_SYSTEM, "\n".join(lines), LLM_SCHEMA, max_tokens=60 * len(rows), background=True)
    out = {}
    for it in data.get("items", []):
        try:
            r = rows[int(it["i"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        cat = str(it.get("category", "Other"))
        if cat not in CATEGORIES and cat != "Other":
            cat = "Other"
        out[r["id"]] = (cat, 0.6, str(it.get("reason", ""))[:120])
    return out


# ---------------------------------------------------------------- background loop


def _save(results: dict[int, tuple[str, float, str]], source: str) -> None:
    with db.tx() as c:
        c.executemany(
            "UPDATE files SET category=?, category_source=?, category_conf=?, category_reason=? WHERE id=?",
            [(cat, source, conf, reason, fid) for fid, (cat, conf, reason) in results.items()],
        )


def reset_if_new_version() -> None:
    if db.get_setting("classify_version") != VERSION:
        with db.tx() as c:
            c.execute("UPDATE files SET category=NULL, category_source=NULL, category_conf=NULL, category_reason=NULL")
        db.set_setting("classify_version", VERSION)


def step(indexer) -> bool:
    """One unit of background work. Return True when it did something."""
    c = db.conn()
    # Rules first, for every file: a cheap SQL pass in batches.
    rows = c.execute("SELECT id, path, name FROM files WHERE is_dir=0 AND light=0 AND category IS NULL AND category_source IS NULL LIMIT 500").fetchall()
    if rows:
        res, rest = {}, []
        for r in rows:
            got = rule(r["path"], r["name"])
            if got:
                res[r["id"]] = got
            else:
                rest.append(r["id"])
        _save(res, "rule")
        # The rest wait for meaning: mark them, so this pass does not see them again.
        with db.tx() as tc:
            tc.executemany("UPDATE files SET category_source='pending' WHERE id=?", [(i,) for i in rest])
        return True
    # Embeddings next, when the file's chunks have vectors.
    busy = c.execute("SELECT COUNT(*) FROM chunks WHERE embedded=0").fetchone()[0]
    if busy or indexer.interactive.is_set() or not indexer.state.get("embed_ready"):
        return False
    rows = c.execute(
        "SELECT f.id, f.name FROM files f WHERE f.category_source='pending' AND EXISTS (SELECT 1 FROM chunks c WHERE c.file_id=f.id) LIMIT 50"
    ).fetchall()
    rows_nochunk = c.execute(
        "SELECT f.id FROM files f WHERE f.category_source='pending' AND f.index_stage>=2 AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.file_id=f.id) LIMIT 200"
    ).fetchall()
    if rows_nochunk:
        _save({r[0]: ("Other", 0.3, "no text to read") for r in rows_nochunk}, "rule")
    if rows:
        try:
            prototypes()
        except Exception as e:  # noqa: BLE001
            log.warning("prototypes failed: %s", e)
            return False
        from . import learn

        res, learned, rest = {}, {}, []
        for r in rows:
            # Step 1b: a file close to one that the user labeled gets that label (learn.py).
            vec = file_vector(r["id"])
            ex = learn.by_example(vec) if vec is not None else None
            if ex:
                learned[r["id"]] = ex
                continue
            got = by_embedding(r["id"])
            if got:
                res[r["id"]] = got
            else:
                rest.append(r["id"])
        _save(learned, "learned")
        _save(res, "embedding")
        with db.tx() as tc:
            tc.executemany("UPDATE files SET category_source='llm-pending' WHERE id=?", [(i,) for i in rest])
        return True
    # The local LLM last, in small batches.
    rows = c.execute("SELECT id, name FROM files WHERE category_source='llm-pending' LIMIT ?", (LLM_BATCH,)).fetchall()
    if not rows:
        return bool(rows_nochunk)
    from .router import local

    if not local.background_allowed() or not local.available():
        return False
    try:
        res = by_llm([dict(r) for r in rows])
    except Exception as e:  # noqa: BLE001
        log.warning("llm classification failed: %s", e)
        res = {}
    missing = {r["id"]: ("Other", 0.3, "the local model gave no answer") for r in rows if r["id"] not in res}
    _save(res, "llm")
    _save(missing, "llm")
    events.publish("classified", count=len(rows))
    return True


def stats() -> dict:
    c = db.conn()
    rows = c.execute(
        "SELECT COALESCE(category_source, 'waiting') s, COUNT(*) n FROM files WHERE is_dir=0 AND light=0 GROUP BY s"
    ).fetchall()
    by_source = {r["s"]: r["n"] for r in rows}
    cats = c.execute(
        "SELECT category, COUNT(*) n FROM files WHERE is_dir=0 AND category IS NOT NULL GROUP BY category ORDER BY n DESC"
    ).fetchall()
    total = sum(by_source.values()) or 1
    done = sum(v for k, v in by_source.items() if k in ("rule", "embedding", "llm", "learned", "user"))
    return {
        "total": total, "done": done,
        "share": {k: round(v / total * 100, 1) for k, v in by_source.items()},
        "counts": by_source,
        "categories": {r["category"]: r["n"] for r in cats},
    }


def loop(indexer) -> None:
    reset_if_new_version()
    while not indexer.stop.is_set():
        try:
            did = step(indexer)
        except Exception:  # noqa: BLE001
            log.exception("classification step failed")
            did = False
        indexer.stop.wait(0.05 if did else 5)


def explain(path: str) -> dict | None:
    r = db.conn().execute("SELECT category, category_source, category_conf, category_reason FROM files WHERE path=?", (path,)).fetchone()
    return dict(r) if r else None



