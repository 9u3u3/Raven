"""Search by appearance (sections 9.2 step 3, 9.2.1, 13.12 items 4 and 5).

One SigLIP 2 vector space holds three kinds of rows:
  image  a photo, a screenshot or a drawing;
  page   a rendered PDF page, so "the page with the bar chart" finds the page with no OCR;
  frame  a video keyframe with its time, so a query opens the video at the matched moment.

The job runs in the background after text and meaning, at the lowest priority, and stops for interactive work.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import sqlite_vec

from .. import db
from ..config import THUMB_DIR
from ..fs.atime import keep_atime
from ..fs.kinds import kind_of
from . import siglip

log = logging.getLogger("raven.visual")

# Raise this when what the job writes changes. Files with an older signature are done again.
VERSION = 1
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "avif", "heic", "jxl"}
VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "3gp"}
MIN_IMAGE = 1500
MAX_IMAGE = 60 * 1024 * 1024
MAX_PAGES = 40
MAX_FRAMES = 300
MIN_GAP = 2.0
FRAME_EVERY = 10.0
# A keyframe that looks like the previous kept keyframe adds nothing (9.2.1 step 3).
SAME_FRAME = 0.95
BATCH = 8
FRAME_PX = 320
# SigLIP similarities are low in absolute terms. Calibrated on testdata and a real photo folder.
MIN_SIM = 0.075
REL_SIM = 0.72
SIMILAR_MIN = 0.62

FRAME_DIR = THUMB_DIR / "frames"
PAGE_DIR = THUMB_DIR / "pages"

SCHEMA = """
CREATE TABLE IF NOT EXISTS visual (
  id INTEGER PRIMARY KEY,
  file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
  kind TEXT, at REAL, thumb TEXT
);
CREATE INDEX IF NOT EXISTS visual_file ON visual(file_id);
"""


def init() -> None:
    c = db.conn()
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(files)")}
    if "visual_sig" not in cols:
        c.execute("ALTER TABLE files ADD COLUMN visual_sig TEXT")
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='visual_vec'").fetchone() is None:
        c.execute(f"CREATE VIRTUAL TABLE visual_vec USING vec0(embedding float[{siglip.DIM}] distance_metric=cosine)")
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Frames and page images that are gone (encryption deletes them, or a user cleared the cache): index those
    # files again, so the keyframe strip and the result tiles have pictures.
    gone = {fid for fid, thumb in c.execute("SELECT DISTINCT file_id, thumb FROM visual WHERE thumb IS NOT NULL")
            if not os.path.exists(thumb)}
    if gone:
        with db.tx() as tc:
            tc.execute(f"UPDATE files SET visual_sig = NULL WHERE id IN ({','.join('?' * len(gone))})", list(gone))
        log.info("visual: %d files lost their frames or page images, so Raven indexes them again", len(gone))


def kind_for(ext: str) -> str | None:
    if ext in IMAGE_EXTS:
        return "image"
    if ext == "pdf":
        return "page"
    if ext in VIDEO_EXTS:
        return "frame"
    return None


def _sig(row) -> str:
    return f"{VERSION}:{row['mtime']}:{row['size']}"


# ---------------------------------------------------------------- decoding


def open_image(path: str):
    """Open any image Raven indexes as a PIL image. JPEG XL and HEIC go through their CLI decoders."""
    from PIL import Image

    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("jxl", "heic", "avif"):
        tool = {"jxl": ["djxl", path], "heic": ["heif-convert", path], "avif": ["magick", path]}[ext]
        if shutil.which(tool[0]) is None:
            raise RuntimeError(f"{tool[0]} is not installed")
        with tempfile.TemporaryDirectory(prefix="raven-img-") as tmp:
            out = os.path.join(tmp, "x.png")
            subprocess.run([*tool, out], capture_output=True, timeout=60, check=True)
            img = Image.open(out)
            img.load()
            return img
    img = Image.open(path)
    img.draft("RGB", (448, 448))
    if getattr(img, "is_animated", False):
        img.seek(0)
    img.load()
    return img


from ..pdflock import locked as _pdf_locked


@_pdf_locked
def _page_images(path: str) -> list[tuple[float, object, str]]:
    from .. import pdf

    out = []
    key = hashlib.sha1(path.encode()).hexdigest()[:16]
    with pdf.open_pdf(path) as doc:
        for i in range(min(len(doc), MAX_PAGES)):
            img = pdf.render_fit(doc[i], 448)
            thumb = PAGE_DIR / f"{key}_{i + 1}.webp"
            img.save(thumb, "WEBP", quality=75)
            out.append((float(i + 1), img, str(thumb)))
    return out


def duration(path: str) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


_PTS = re.compile(r"pts_time:([0-9.]+)")


def _frames(path: str) -> list[tuple[float, object, str]]:
    """Keyframes only (fast: no full decode), at most one each MIN_GAP seconds and MAX_FRAMES in all."""
    from PIL import Image

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed")
    dur = duration(path) or 0
    gap = max(MIN_GAP, dur / MAX_FRAMES) if dur else MIN_GAP
    key = hashlib.sha1(path.encode()).hexdigest()[:16]
    with tempfile.TemporaryDirectory(prefix="raven-frames-") as tmp:
        vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{gap:.2f})',"
              f"scale={FRAME_PX}:{FRAME_PX}:force_original_aspect_ratio=decrease,showinfo")
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-skip_frame", "nokey", "-i", path, "-an", "-vf", vf,
             "-fps_mode", "vfr", "-frames:v", str(MAX_FRAMES), "-q:v", "5", os.path.join(tmp, "%04d.jpg")],
            capture_output=True, text=True, timeout=600,
        )
        times = [float(t) for t in _PTS.findall(r.stderr)]
        files = sorted(Path(tmp).glob("*.jpg"))
        if not files:
            raise RuntimeError((r.stderr.strip().splitlines() or ["ffmpeg made no frames"])[-1][:200])
        out = []
        for i, f in enumerate(files):
            t = times[i] if i < len(times) else i * gap
            thumb = FRAME_DIR / f"{key}_{int(t * 1000)}.jpg"
            shutil.copyfile(f, thumb)
            img = Image.open(thumb)
            img.load()
            out.append((round(t, 2), img, str(thumb)))
        return out


# ---------------------------------------------------------------- the job


class VisualJob:
    def __init__(self) -> None:
        self.state = {"pending": 0, "done": 0, "current": None, "error": None, "waiting": None}

    def _pending(self, c, limit: int):
        exts = ",".join(f"'{e}'" for e in sorted(IMAGE_EXTS | VIDEO_EXTS | {"pdf"}))
        return c.execute(
            f"SELECT id, path, ext, size, mtime, visual_sig FROM files WHERE is_dir=0 AND light=0 AND ext IN ({exts}) "
            f"AND size >= {MIN_IMAGE} AND (visual_sig IS NULL OR visual_sig != ('{VERSION}:' || mtime || ':' || size)) "
            # Photos first, then pages, then videos (9.2.1 budget).
            "ORDER BY CASE WHEN ext='pdf' THEN 1 WHEN ext IN ('mp4','mkv','webm','mov','avi','m4v','3gp') THEN 2 ELSE 0 END, id "
            "LIMIT ?",
            (limit,),
        ).fetchall()

    def count_total(self) -> int:
        """Every file that the image stage reads: images, PDF pages and videos."""
        exts = ",".join(f"'{e}'" for e in sorted(IMAGE_EXTS | VIDEO_EXTS | {"pdf"}))
        return db.conn().execute(f"SELECT COUNT(*) FROM files WHERE is_dir=0 AND light=0 AND ext IN ({exts}) AND size >= {MIN_IMAGE}").fetchone()[0]

    def count_pending(self) -> int:
        exts = ",".join(f"'{e}'" for e in sorted(IMAGE_EXTS | VIDEO_EXTS | {"pdf"}))
        return db.conn().execute(
            f"SELECT COUNT(*) FROM files WHERE is_dir=0 AND light=0 AND ext IN ({exts}) AND size >= {MIN_IMAGE} "
            f"AND (visual_sig IS NULL OR visual_sig != ('{VERSION}:' || mtime || ':' || size))"
        ).fetchone()[0]

    def loop(self, ix) -> None:
        if not siglip.available():
            self.state["error"] = "SigLIP 2 model files are not in models/siglip2"
            log.info(self.state["error"])
            return
        from .. import lite

        while not ix.stop.is_set():
            if not lite.enabled("image") or lite.paused():
                ix.stop.wait(15)  # lite mode: off until the user turns it on
                continue
            try:
                self._cleanup()
                c = db.conn()
                # Lowest priority: text, meaning and Ask go first.
                busy = c.execute(
                    "SELECT (SELECT COUNT(*) FROM files WHERE index_stage=1) + (SELECT COUNT(*) FROM chunks WHERE embedded=0)"
                ).fetchone()[0]
                if busy or ix.interactive.is_set():
                    # Say what the stage waits for, with the real count, so "0 pending" never hides work.
                    self.state["pending"] = self.count_pending()
                    self.state["waiting"] = "text and meaning stages" if busy else "a search or an answer"
                    ix.stop.wait(3)
                    continue
                self.state["waiting"] = None
                rows = self._pending(c, 4)
                self.state["pending"] = self.count_pending()
                if not rows:
                    self.state["current"] = None
                    ix.stop.wait(8)
                    continue
                for r in rows:
                    if ix.stop.is_set() or ix.interactive.is_set():
                        break
                    self._one(r, ix)
            except Exception:
                log.exception("visual job failed")
                ix.stop.wait(10)

    def _one(self, r, ix) -> None:
        path, kind = r["path"], kind_for(r["ext"])
        self.state["current"] = path
        items: list[tuple[float | None, object, str | None]] = []
        error = None
        try:
            from ..privacy.scope import require_ai

            require_ai(path)  # a private folder (PLAN4 7.6): the photo model never opens it
            with keep_atime(path):
                if kind == "image":
                    if r["size"] <= MAX_IMAGE:
                        items = [(None, open_image(path), None)]
                elif kind == "page":
                    items = _page_images(path)
                elif kind == "frame":
                    items = _frames(path)
        except Exception as e:  # noqa: BLE001
            error = str(e)[:200]
            log.info("visual: skip %s: %s", path, error)
        vecs: list[np.ndarray] = []
        for i in range(0, len(items), BATCH):
            # Wait for interactive work between batches, so a query never waits for a whole video.
            while ix.interactive.is_set() and not ix.stop.is_set():
                ix.stop.wait(0.2)
            part = items[i:i + BATCH]
            vecs += list(siglip.model.embed_images([siglip.pixels(img) for _, img, _ in part]))
        rows = []
        last = None
        for (at, _, thumb), v in zip(items, vecs):
            if kind == "frame" and last is not None and float(v @ last) > SAME_FRAME:
                if thumb:
                    Path(thumb).unlink(missing_ok=True)
                continue
            last = v
            rows.append((at, thumb, v))
        with db.tx() as c:
            cur = c.execute("SELECT mtime, size FROM files WHERE id=?", (r["id"],)).fetchone()
            if cur is None:
                return
            old_rows = c.execute("SELECT id, thumb FROM visual WHERE file_id=?", (r["id"],)).fetchall()
            old = [x[0] for x in old_rows]
            keep = {t for _, t, _ in rows}
            for x in old_rows:
                if x[1] and x[1] not in keep:
                    Path(x[1]).unlink(missing_ok=True)
            if old:
                c.execute(f"DELETE FROM visual_vec WHERE rowid IN ({','.join('?' * len(old))})", old)
                c.execute("DELETE FROM visual WHERE file_id=?", (r["id"],))
            for at, thumb, v in rows:
                vid = c.execute(
                    "INSERT INTO visual(file_id, kind, at, thumb) VALUES (?,?,?,?)", (r["id"], kind, at, thumb)
                ).lastrowid
                c.execute("INSERT INTO visual_vec(rowid, embedding) VALUES (?, ?)",
                          (vid, sqlite_vec.serialize_float32(v.astype(np.float32))))
            c.execute("UPDATE files SET visual_sig=? WHERE id=?", (f"{VERSION}:{cur['mtime']}:{cur['size']}", r["id"]))
        self.state["done"] += 1
        if error:
            self.state["error"] = f"{os.path.basename(path)}: {error}"

    def _cleanup(self) -> None:
        """Rows of deleted files: the visual rows go with the file (cascade), the vectors go here."""
        c = db.conn()
        # Files that became names only (a code project or software folder): their pictures leave the index.
        light = [r[0] for r in c.execute(
            "SELECT v.id FROM visual v JOIN files f ON f.id = v.file_id WHERE f.light = 1 LIMIT 500")]
        if light:
            with db.tx() as tc:
                tc.execute(f"DELETE FROM visual WHERE id IN ({','.join('?' * len(light))})", light)
        orphan = c.execute("SELECT rowid FROM visual_vec WHERE rowid NOT IN (SELECT id FROM visual) LIMIT 500").fetchall()
        if orphan:
            with db.tx() as tc:
                tc.execute(f"DELETE FROM visual_vec WHERE rowid IN ({','.join('?' * len(orphan))})", [o[0] for o in orphan])

    def status(self) -> dict:
        c = db.conn()
        counts = {k: n for k, n in c.execute("SELECT kind, COUNT(*) FROM visual GROUP BY kind")}
        return {**self.state, "available": siglip.available(), "rows": counts, "model": siglip.model.loaded()}


job = VisualJob()


# ---------------------------------------------------------------- search


def _knn(vec: np.ndarray, k: int) -> list:
    blob = sqlite_vec.serialize_float32(vec.astype(np.float32))
    return db.conn().execute(
        "SELECT v.id vid, v.file_id fid, v.kind, v.at, v.thumb, k.distance d FROM "
        "(SELECT rowid, distance FROM visual_vec WHERE embedding MATCH ? AND k = ?) k "
        "JOIN visual v ON v.id = k.rowid ORDER BY k.distance",
        (blob, k),
    ).fetchall()


def _group(rows, skip: int | None = None, min_sim: float = MIN_SIM, rel: float = REL_SIM) -> list[dict]:
    """Best row per file. A video keeps up to 5 moments, best first (the strip under the preview)."""
    out: dict[int, dict] = {}
    best = None
    for r in rows:
        sim = 1 - r["d"]
        if r["fid"] == skip or sim < min_sim:
            continue
        if best is None:
            best = sim
        if sim < best * rel:
            break
        e = out.get(r["fid"])
        if e is None:
            out[r["fid"]] = {"fid": r["fid"], "kind": r["kind"], "at": r["at"], "thumb": r["thumb"], "sim": round(sim, 3),
                             "moments": [r["at"]] if r["kind"] == "frame" else []}
        elif r["kind"] == "frame" and len(e["moments"]) < 5:
            e["moments"].append(r["at"])
    return list(out.values())


def _decorate(hits: list[dict], filters=None) -> list[dict]:
    if not hits:
        return []
    ids = [h["fid"] for h in hits]
    rows = {r["id"]: r for r in db.conn().execute(
        f"SELECT id, path, parent, name, size, mtime, ext FROM files WHERE id IN ({','.join('?' * len(ids))})", ids
    )}
    out = []
    for h in hits:
        r = rows.get(h["fid"])
        if r is None or (filters is not None and filters.active and not filters.match(dict(r))):
            continue
        out.append({
            "id": r["id"], "path": r["path"], "name": r["name"], "parent": r["parent"], "size": r["size"],
            "mtime": (r["mtime"] or 0) * 1000, "kind": kind_of(r["name"], False),
            "match": h["kind"], "at": h["at"], "thumb": h["thumb"], "sim": h["sim"], "moments": h["moments"],
        })
    return out


VISUAL_WORDS = re.compile(
    r"\b(photos?|pictures?|pics?|images?|screenshots?|selfies?|charts?|graphs?|plots?|diagrams?|figures?|drawings?|"
    r"sketch(es)?|logos?|maps?|slides?|tables?|clips?|videos?|scenes?|frames?|shows?|showing|looks?|looking|"
    r"colou?r(ed|ful)?|red|blue|green|yellow|black|white|orange|purple|pink|dark|bright)\b", re.I)
SHORT_WORDS = 3
SHORT_MIN_SIM = 0.10
_WORD = re.compile(r"[a-z0-9]+", re.I)


def mode_for(text: str, filters=None) -> str | None:
    """How a query uses the image index (9.2 step 3): "all" kinds, "photos" only, or None.

    A query with a visual word, or an image or video type filter, looks at photos, pages and frames.
    A short query ("sunset", "cake") looks at photos and video moments with a higher threshold.
    A long text query ("invoice for the website design work") skips it: text search answers it better.
    """
    types = set(getattr(filters, "exts", None) or [])
    if VISUAL_WORDS.search(text) or types & (IMAGE_EXTS | VIDEO_EXTS):
        return "all"
    words = [w for w in _WORD.findall(text.lower()) if w not in {"a", "an", "the", "of", "my", "with", "and", "in", "on", "from", "for"}]
    return "photos" if 0 < len(words) <= SHORT_WORDS else None


def search(text: str, limit: int = 24, filters=None, mode: str | None = "auto") -> dict:
    t0 = time.perf_counter()
    if mode == "auto":
        mode = mode_for(text, filters)
    if not text.strip() or not siglip.available() or mode is None:
        return {"results": [], "ms": 0, "mode": mode}
    if db.conn().execute("SELECT 1 FROM visual LIMIT 1").fetchone() is None:
        return {"results": [], "ms": 0, "mode": mode}
    vec = siglip.model.embed_text(text)
    rows = _knn(vec, 200)
    if mode == "photos":
        hits = _group([r for r in rows if r["kind"] != "page"], min_sim=SHORT_MIN_SIM)
    else:
        hits = _group(rows)
    return {"results": _decorate(hits, filters)[:limit], "ms": round((time.perf_counter() - t0) * 1000, 1), "mode": mode}


def similar(path: str, limit: int = 24) -> dict:
    """Files that look like this one: the mean of its vectors (a video: its keyframes) as the query."""
    c = db.conn()
    row = c.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    if row is None:
        return {"results": [], "error": "This file is not indexed."}
    blobs = c.execute(
        "SELECT vv.embedding FROM visual v JOIN visual_vec vv ON vv.rowid = v.id WHERE v.file_id=?", (row["id"],)
    ).fetchall()
    if not blobs:
        pending = c.execute("SELECT visual_sig FROM files WHERE id=?", (row["id"],)).fetchone()[0] is None
        return {"results": [], "error": "Raven did not look at this file yet." if pending else "Raven cannot read this file as an image."}
    m = np.mean([np.frombuffer(b[0], dtype=np.float32) for b in blobs], axis=0)
    m /= np.linalg.norm(m) or 1
    hits = _group(_knn(m, 300), skip=row["id"], min_sim=SIMILAR_MIN, rel=0.0)
    return {"results": _decorate(hits)[:limit]}


def moments(path: str) -> list[dict]:
    """All kept keyframes of a video, for the strip under the preview."""
    return [dict(r) for r in db.conn().execute(
        "SELECT v.at, v.thumb FROM visual v JOIN files f ON f.id=v.file_id WHERE f.path=? AND v.kind='frame' ORDER BY v.at",
        (path,),
    )]

