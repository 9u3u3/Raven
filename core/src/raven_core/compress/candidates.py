"""Compression candidates (section 9.8 step 1).

Signals: cold (not opened or changed for N days), large, compressible (a sample test
with zstd -3), similar group (one folder), not in use (no recent git activity, not
excluded, not inside dependency or cache folders).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

from .. import config
from ..fs.atime import keep_atime
from ..storage import protect
from ..storage.scan import CACHES, CODEDEPS, store
from ..platform import below, inside

DAY = 86400
SAMPLE = 1024 * 1024
MIN_FOLDER = 1 * 1024 * 1024
MIN_FILE = 1 * 1024 * 1024
MIN_PHOTO = 100 * 1024
# Already compressed: an archive gains almost nothing (section 9.8).
COMPRESSED_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "jxl", "mp4", "mkv", "webm", "mov", "mp3", "m4a",
                   "ogg", "opus", "flac", "zip", "7z", "gz", "zst", "xz", "bz2", "rar", "docx", "xlsx", "pptx", "odt",
                   "ods", "odp", "epub", "apk", "appimage", "deb", "rpm"}
VIDEO_EXTS = {"mp4", "mkv", "mov", "avi", "m4v", "wmv"}
MIN_VIDEO = 200 * 1024 * 1024
# Lossy choices (section 9.8 step 2): Suggest mode only, and the review warns first.
PHOTO_METHODS = ["jxl", "avif", "jxl-q"]
TEXTLIKE = {"log", "csv", "tsv", "json", "jsonl", "sql", "txt", "xml", "ndjson"}

_ratio_cache: dict[str, float] = {}
_ratio_lock = threading.Lock()
_cand_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[1].lower() if "." in name.lstrip(".") else ""


def sample_ratio(path: str, ext: str) -> float:
    """compressed / original for a 1 MB sample of this file type. Cached per type."""
    with _ratio_lock:
        if ext in _ratio_cache:
            return _ratio_cache[ext]
    if ext in COMPRESSED_EXTS:
        ratio = 0.98
    else:
        try:
            with keep_atime(path), open(path, "rb") as f:
                data = f.read(SAMPLE)
            if len(data) < 4096:
                return 0.5  # too small to measure; do not cache
            out = subprocess.run(["zstd", "-3", "-c", "-q"], input=data, stdout=subprocess.PIPE, timeout=30).stdout
            ratio = min(1.0, len(out) / len(data))
        except (OSError, subprocess.SubprocessError):
            return 0.9
    with _ratio_lock:
        _ratio_cache[ext] = ratio
    return ratio


def _cold_files(top: str, cold_days: int) -> tuple[list[tuple[str, int, int, str]], bool]:
    """All files under `top` as (path, size, last_use, ext). The flag says every file is cold."""
    now = time.time()
    out = []
    all_cold = True
    for dp, dns, fns in os.walk(top):
        dns[:] = [d for d in dns if d not in config.EXCLUDE_NAMES]
        if ".git" in dns or os.path.isdir(os.path.join(dp, ".git")):
            # A git working tree with recent commits is in use.
            head = os.path.join(dp, ".git", "HEAD")
            if os.path.exists(head) and now - os.stat(head).st_mtime < cold_days * DAY:
                return [], False
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            last = max(st.st_atime, st.st_mtime)
            if now - last < cold_days * DAY:
                all_cold = False
            out.append((p, st.st_size, int(last), _ext(fn)))
    return out, all_cold


def _gb(n: float) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{int(n)} B"


def candidates(path: str, cold_days: int = 90, limit: int = 12) -> list[dict]:
    """Suggestion rows with action 'compress'. Each row is one job."""
    path = os.path.abspath(path)
    key = (path, cold_days)
    hit = _cand_cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    found = store.find(path)
    if not found:
        return []
    res, node = found
    now = time.time()
    rows: list[dict] = []
    taken: list[str] = []

    # Cold folders: walk the tree largest first and take the highest folder that is all cold.
    def walk(n, depth: int, hidden: bool):
        if len(rows) >= limit:
            return
        for ch in sorted(n.children, key=lambda c: c.size, reverse=True):
            if ch.size < MIN_FOLDER:
                break
            if ch.name.startswith(".") or hidden:
                continue
            cat = max(range(8), key=lambda i: ch.cats[i])
            if cat in (CODEDEPS, CACHES) or ch.name in config.EXCLUDE_NAMES:
                continue
            # Software and SDK folders are never candidates, and Raven does not look inside them.
            if protect.is_software_dir(ch.path):
                continue
            project = protect.is_project_root(ch.path)
            # Recently used content: no file of it was opened in the last month.
            # A project is one unit: archive all of it or nothing, never a folder inside it.
            if ch.ages[0] > 0 or depth < 1:
                if not project:
                    walk(ch, depth + 1, hidden)
                continue
            files, all_cold = _cold_files(ch.path, cold_days)
            if not files:
                continue
            if not all_cold:
                if not project:
                    walk(ch, depth + 1, hidden)
                continue
            size = sum(f[1] for f in files)
            est_after = sum(f[1] * sample_ratio(f[0], f[3]) for f in files)
            gain = size - est_after
            if gain < size * 0.15 or gain < 256 * 1024:
                # An archive gains little here (photos, video). JPEG files can still go to JPEG XL below.
                walk(ch, depth + 1, hidden)
                continue
            idle = int((now - max(f[2] for f in files)) / DAY)
            rel = os.path.relpath(ch.path, path)
            rows.append({
                "id": f"compress:{ch.path}",
                "sentence": f"{len(files)} files in {rel} were not opened for {idle} days. Archive to .7z.",
                "bytes": int(gain), "total": int(gain), "reversible": True, "action": "compress", "method": "7z",
                "methods": ["7z", "tar.zst", "zip"], "root": os.path.dirname(ch.path), "unit": [ch.path],
                "items": [{"path": f[0], "size": f[1], "checked": True, "note": ""} for f in sorted(files, key=lambda f: -f[1])],
            })
            taken.append(ch.path)

    # A folder in view that is itself inside software or a project gets no compression rows.
    if protect.blocked(path, _stop_at(path)):
        _cand_cache[key] = (time.time(), [])
        return []
    walk(node, 0, False)

    # JPEG photos -> lossless JPEG XL, one row per folder. Large text-like files -> zstd.
    photos: dict[str, list] = {}
    singles = []
    videos = []
    # Files from the folder in view: the largest files of each of its folders. The global list of
    # largest files is not enough, because a scan of a whole drive fills it with other files.
    local_files = []
    stack = [(node, False)]
    while stack:
        n, hidden = stack.pop()
        for size, name, mtime, atime, cat in n.top:
            local_files.append((size, os.path.join(n.path, name), mtime, atime, cat))
        for ch in n.children:
            if not hidden and not ch.name.startswith(".") and ch.name not in config.EXCLUDE_NAMES:
                stack.append((ch, False))
    local_files.sort(reverse=True)
    for size, p, mtime, atime, cat in local_files[:5000]:
        if any(below(p, t) for t in taken):
            continue
        if protect.blocked(p, path):
            continue
        if "/." in p[len(path):]:
            continue
        ext = _ext(p)
        try:
            st = os.stat(p)
        except OSError:
            continue
        last = max(st.st_atime, st.st_mtime)
        if now - last < cold_days * DAY:
            continue
        if ext in ("jpg", "jpeg") and st.st_size >= MIN_PHOTO:
            photos.setdefault(os.path.dirname(p), []).append((p, st.st_size, last))
        elif ext in TEXTLIKE and st.st_size >= MIN_FILE:
            singles.append((p, st.st_size, last, ext))
        elif ext in VIDEO_EXTS and st.st_size >= MIN_VIDEO and len(videos) < 20:
            videos.append((p, st.st_size, last))
    for d, files in photos.items():
        files = files[:200]
        size = sum(f[1] for f in files)
        gain = size * 0.2  # lossless JPEG XL saves about 20% (section 9.8). The run measures the real number.
        idle = int((now - max(f[2] for f in files)) / DAY)
        rows.append({
            "id": f"jxl:{d}",
            "sentence": f"{len(files)} photos in {os.path.relpath(d, path)} were not opened for {idle} days. Convert to lossless JPEG XL. djxl gives back the exact JPEG.",
            "bytes": int(gain), "total": int(gain), "reversible": True, "action": "compress", "method": "jxl",
            "methods": PHOTO_METHODS, "root": d, "unit": [f[0] for f in files],
            "items": [{"path": f[0], "size": f[1], "checked": True, "note": ""} for f in files],
        })
    for p, size, last, ext in singles:
        gain = size * (1 - sample_ratio(p, ext) * 0.8)  # zstd -19 does better than the -3 sample
        rows.append({
            "id": f"zst:{p}",
            "sentence": f"{os.path.basename(p)} was not opened for {int((now - last) / DAY)} days. Compress with zstd.",
            "bytes": int(gain), "total": int(gain), "reversible": True, "action": "compress", "method": "zst",
            "methods": ["zst"], "root": os.path.dirname(p), "unit": [p],
            "items": [{"path": p, "size": size, "checked": True, "note": ""}],
        })
    for p, size, last in videos:
        if _video_codec(p) in ("hevc", "av1", None):
            continue  # already efficient, or ffprobe cannot read it
        gain = size * 0.5  # H.265 at CRF 26 is often about half of H.264. The run measures the real number.
        rows.append({
            "id": f"hevc:{p}",
            "sentence": f"{os.path.basename(p)} was not opened for {int((now - last) / DAY)} days. Re-encode to H.265. This loses some quality and is slow.",
            "bytes": int(gain), "total": int(gain), "reversible": False, "action": "compress", "method": "hevc",
            "methods": ["hevc"], "root": os.path.dirname(p), "unit": [p],
            "items": [{"path": p, "size": size, "checked": True, "note": ""}],
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    rows = rows[:limit]
    _cand_cache[key] = (time.time(), rows)
    return rows


def _video_codec(path: str) -> str | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode().strip() or None


def _stop_at(path: str) -> str:
    """Where the upward check for software and projects stops: the indexed root that holds
    `path` (the user declared it as content), else the home folder, else the root."""
    from ..index.indexer import roots

    held = [r for r in roots() if inside(path, r)]
    if held:
        return os.path.dirname(max(held, key=len))
    home = os.path.expanduser("~")
    return home if below(path, home) else os.path.abspath(os.sep)


def forget_cache() -> None:
    _cand_cache.clear()
    protect.forget()


def gb(n: float) -> str:
    return _gb(n)
