"""A short description of a file or a folder for the Inspector, from what the index already knows. No model call.

Folder: the summary that the local model wrote at index time (search/folders.py), else a count of what it holds.
File:   speech (the first words said), an image (its text, else what it shows), a document (its title and its
        first lines). A file with secrets gets no text here.
"""

from __future__ import annotations

import os
import re
import sqlite3

from . import db

WORDS = {"doc": "documents", "image": "images", "video": "videos", "audio": "audio files", "archive": "archives",
         "sheet": "spreadsheets", "code": "code files", "app": "programs", "data": "data files", "font": "fonts"}
MAX = 220


def _clean(text: str) -> str:
    """Readable prose from a chunk: no Markdown front matter, table syntax, link brackets or escapes."""
    t = re.sub(r"^\s*---\s.*?\s---(?:\s|$)", " ", text, count=1, flags=re.S)  # YAML front matter
    t = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", t)  # [[wikilinks]], before the table pipes
    t = re.sub(r"\|\s*-{3,}\s*", " ", t)  # table rules
    t = t.replace("\\_", "_").replace("\\*", "*")
    t = re.sub(r"\s*\|\s*", ", ", t)
    t = re.sub(r"(?:,\s*){2,}", ", ", t).strip(", ")
    t = re.sub(r"[#*_`>]{2,}|^#+\s", " ", t)
    return t


def _cut(text: str, n: int = MAX) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _folder(path: str) -> dict:
    from .fs.kinds import group_of
    from .storage import protect

    try:
        row = db.conn().execute("SELECT summary FROM folders WHERE path=?", (path,)).fetchone()
    except sqlite3.OperationalError:  # no folder index yet
        row = None
    if row and (row["summary"] or "").strip():
        return {"text": row["summary"].strip(), "source": "summary"}
    counts: dict[str, int] = {}
    dirs = 0
    try:
        with os.scandir(path) as it:
            for i, de in enumerate(it):
                if i > 5000:
                    break
                if de.name.startswith("."):
                    continue
                if de.is_dir(follow_symlinks=False):
                    dirs += 1
                else:
                    g = WORDS.get(group_of(de.name, False), "other files")
                    counts[g] = counts.get(g, 0) + 1
    except OSError:
        return {}
    parts = ([f"{dirs} folder{'s' if dirs != 1 else ''}"] if dirs else []) + [
        f"{n} {w if n != 1 else w.rstrip('s')}" for w, n in sorted(counts.items(), key=lambda x: -x[1])[:4]]
    if not parts:
        return {"text": "An empty folder.", "source": "contents"}
    lead = "A code project. " if protect.is_project_root(path) else ""
    text = lead + "Holds " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]) + "."
    return {"text": text, "source": "contents"}


def _file(path: str) -> dict:
    from .fs.kinds import group_of

    c = db.conn()
    row = c.execute("SELECT id, name, title, category, category_reason FROM files WHERE path=?", (path,)).fetchone()
    group = group_of(os.path.basename(path), False)
    if row is None:
        return {}
    cat = row["category"] if row["category"] not in (None, "Other") else None
    out = {"category": cat, "reason": row["category_reason"] or ""}
    if row["category"] == "Secret":
        return {**out, "text": "Holds secrets, such as passwords. Raven does not show them here.", "source": "privacy"}
    if group in ("audio", "video"):
        r = c.execute("SELECT text FROM chunks WHERE file_id=? AND at IS NOT NULL ORDER BY at LIMIT 1", (row["id"],)).fetchone()
        if r and (r[0] or "").strip():
            return {**out, "text": f"“{_cut(r[0], 160)}”", "source": "speech"}
        return {**out, "text": "No speech found." if group == "audio" else "", "source": "speech"} if out["category"] else {}
    if group == "image":
        r = c.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id LIMIT 1", (row["id"],)).fetchone()
        if r and len((r[0] or "").strip()) > 15:
            return {**out, "text": _cut(r[0], 160), "source": "text in the image"}
        from .naming import photo_label

        label = photo_label(row["id"])
        return {**out, "text": f"Looks like: {label.lower()}." if label else "", "source": "appearance"}
    title = (row["title"] or "").strip()
    if path.lower().endswith(".pdf"):
        from .index.extract import pdf_title

        title = pdf_title(path) or title
    r = c.execute("SELECT text FROM chunks WHERE file_id=? AND at IS NULL ORDER BY id LIMIT 1", (row["id"],)).fetchone()
    body = (r[0] or "").strip() if r else ""
    k = body.find(title) if title else -1
    if 0 <= k < 400:
        body = body[k + len(title):]  # the text after the title: a paper's license note comes before it
    body = _clean(body)
    k = -1
    if 0 <= k < 400:
        body = body[k + len(title):]  # the text after the title: a paper's license note comes before it
    stem = os.path.splitext(row["name"])[0]
    lead = f"{title}. " if title and title.lower() != stem.lower() and len(title) > 3 else ""
    text = _cut(lead + body) if (lead or body) else ""
    return {**out, "text": text, "source": "text"} if text or cat else {}


def describe(path: str) -> dict:
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return _folder(path)
    if os.path.isfile(path):
        return _file(path)
    return {}
