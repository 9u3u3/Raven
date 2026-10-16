"""Exact duplicates (section 9.7): size, then a quick hash, then a full BLAKE3 hash."""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import blake3
import xxhash

from .. import config, events
from ..fs.atime import keep_atime
from . import protect

log = logging.getLogger("raven.dupes")

QUICK = 64 * 1024
COPY_NAME = re.compile(r"(\(\d+\)|\bcopy\b|\bCopy\b|_copy|-copy| \d+$)", re.I)
TEMP_DIRS = ("/Downloads/", "/tmp/", "/Temp/", "/.Trash")


def quick_hash(path: str, size: int) -> str:
    h = xxhash.xxh3_64()
    with keep_atime(path), open(path, "rb") as f:
        h.update(f.read(QUICK))
        if size > 2 * QUICK:
            f.seek(size - QUICK)
            h.update(f.read(QUICK))
    return h.hexdigest()


def full_hash(path: str) -> str:
    h = blake3.blake3(max_threads=blake3.blake3.AUTO)
    with keep_atime(path), open(path, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _keep_score(path: str, mtime: int) -> tuple:
    """Lower is better: a copy outside Downloads, with the original name, the oldest."""
    name = os.path.basename(path).rsplit(".", 1)[0]
    in_temp = any(t in path for t in TEMP_DIRS)
    copy_name = bool(COPY_NAME.search(name))
    return (in_temp, copy_name, mtime, len(path))


def _reason(best: dict, others: list[dict]) -> str:
    parts = []
    if not any(t in best["path"] for t in TEMP_DIRS) and any(any(t in o["path"] for t in TEMP_DIRS) for o in others):
        parts.append("not in Downloads")
    if not COPY_NAME.search(os.path.basename(best["path"]).rsplit(".", 1)[0]) and any(
        COPY_NAME.search(os.path.basename(o["path"]).rsplit(".", 1)[0]) for o in others
    ):
        parts.append("original name")
    if all(best["mtime"] <= o["mtime"] for o in others):
        parts.append("oldest copy")
    return ", ".join(parts) or "first copy"


PHOTO_EXTS = {"jpg", "jpeg", "png", "webp", "heic", "bmp", "tif", "tiff"}
NEAR_MAX = 6  # Hamming distance of two 64-bit pHashes for "near duplicate" (9.7 step 4)
NEAR_MIN_SIZE = 20 * 1024
NEAR_LIMIT = 8000


COLOR_MAX = 40  # distance of the average colors (0 to 441): the hash sees shapes only
_DCT = None


def _dct2(g):
    """The 2D DCT-II with orthonormal scaling (as scipy.fft.dctn(g, norm="ortho")) for a 32 x 32 block. NumPy
    only: SciPy came into the package only through UMAP and scikit-learn (PLAN3 P1-4)."""
    import numpy as np

    global _DCT
    if _DCT is None:
        n = g.shape[0]
        k, i = np.arange(n)[:, None], np.arange(n)[None, :]
        d = np.sqrt(2 / n) * np.cos(np.pi * (2 * i + 1) * k / (2 * n))
        d[0] /= np.sqrt(2)
        _DCT = d.astype(np.float64)
    return _DCT @ g @ _DCT.T


def phash(path: str) -> tuple[int, int, int, int] | None:
    """64-bit DCT perceptual hash, the image width and height, and the average color (r << 16 | g << 8 | b).
    None for an image Raven cannot read. The hash ignores color, so two photos of one view at day and at
    night match: the average color tells them apart."""
    import numpy as np
    from PIL import Image

    try:
        with Image.open(path) as im:
            w, h = im.size
            im.draft("RGB", (64, 64))
            small = im.convert("RGB").resize((32, 32), Image.LANCZOS)
            g = np.asarray(small.convert("L"), dtype=np.float32)
            r, gg, b = (int(x) for x in np.asarray(small, dtype=np.float32).reshape(-1, 3).mean(axis=0))
    except Exception:  # noqa: BLE001
        return None
    d = _dct2(g.astype(np.float64))[:8, :8].flatten()
    bits = d[1:] > np.median(d[1:])
    v = 0
    for bit in bits:
        v = (v << 1) | int(bit)
    return v, w, h, (r << 16) | (gg << 8) | b


BURST_S = 2


def sharpness(path: str) -> float:
    """Edge strength of a photo (the variance of an edge image). A blurred shot scores lower."""
    try:
        from PIL import Image, ImageFilter, ImageStat

        with keep_atime(path), Image.open(path) as im:
            im.draft("L", (640, 640))
            g = im.convert("L")
            g.thumbnail((640, 640))
            return float(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0])
    except Exception:  # noqa: BLE001
        return 0.0


def _color_close(a: int, b: int) -> bool:
    da = [((a >> s) & 255) - ((b >> s) & 255) for s in (16, 8, 0)]
    return (da[0] ** 2 + da[1] ** 2 + da[2] ** 2) ** 0.5 <= COLOR_MAX


def _near_groups(items: list[tuple[str, int, int, int, int, int]]) -> list[list[int]]:
    """items: (path, mtime, size, hash, w, h). Union-find over pairs within NEAR_MAX bits."""
    import numpy as np

    n = len(items)
    if n < 2:
        return []
    H = np.array([it[3] for it in items], dtype=np.uint64)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n - 1):
        x = np.bitwise_xor(H[i + 1:], H[i])
        # popcount of 64-bit values through the bytes
        d = np.unpackbits(x.view(np.uint8).reshape(-1, 8), axis=1).sum(axis=1)
        for j in np.nonzero(d <= NEAR_MAX)[0]:
            if len(items[i]) > 6 and not _color_close(items[i][6], items[i + 1 + int(j)][6]):
                continue
            a, b = find(i), find(i + 1 + int(j))
            if a != b:
                parent[b] = a
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


class DupeJob:
    def __init__(self) -> None:
        self.running = False
        self.path: str | None = None
        self.stage = ""
        self.done = 0
        self.total = 0
        self.groups: list[dict] = []
        self.finished: float | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {
            "running": self.running, "path": self.path, "stage": self.stage, "done": self.done, "total": self.total,
            "finished": int(self.finished * 1000) if self.finished else None, "error": self.error,
            "groups": len(self.groups), "wasted": sum(g["wasted"] for g in self.groups),
        }

    def start(self, path: str, min_size: int = 1024) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        self.path = os.path.abspath(path)
        self.error = None
        threading.Thread(target=self._run, args=(self.path, min_size), name="dupes", daemon=True).start()
        return True

    def _publish(self) -> None:
        events.publish("dupes_progress", **self.status())

    def _run(self, root: str, min_size: int) -> None:
        try:
            self.stage = "sizes"
            by_size: dict[int, list[tuple[str, int]]] = defaultdict(list)
            photos: list[tuple[str, int, int]] = []
            seen_inodes = set()
            stack = [root]
            n = 0
            while stack:
                d = stack.pop()
                try:
                    it = os.scandir(d)
                except OSError:
                    continue
                with it:
                    for de in it:
                        if de.name in config.EXCLUDE_NAMES or de.name.startswith("."):
                            continue
                        try:
                            st = de.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISDIR(st.st_mode):
                            # Copies inside software or projects are there on purpose. Removing one breaks the tool.
                            if protect.is_software_dir(de.path) or protect.is_project_root(de.path) or protect.is_backup_name(de.name) \
                                    or protect.user_protected(de.path):
                                continue
                            stack.append(de.path)
                        elif stat.S_ISREG(st.st_mode) and st.st_size >= min_size and not protect.is_backup_name(de.name):
                            key = (st.st_dev, st.st_ino)
                            if key in seen_inodes:
                                continue  # a hard link is not a duplicate copy
                            seen_inodes.add(key)
                            by_size[st.st_size].append((de.path, int(st.st_mtime)))
                            if st.st_size >= NEAR_MIN_SIZE and de.name.rsplit(".", 1)[-1].lower() in PHOTO_EXTS and len(photos) < NEAR_LIMIT:
                                photos.append((de.path, int(st.st_mtime), st.st_size))
                            n += 1
                if n % 2000 == 0:
                    self.done = n
                    self._publish()
            cands = {s: v for s, v in by_size.items() if len(v) > 1}

            self.stage = "quick hash"
            items = [(s, p, m) for s, v in cands.items() for p, m in v]
            self.total, self.done = len(items), 0
            quick: dict[tuple[int, str], list[tuple[str, int]]] = defaultdict(list)

            def qh(it):
                s, p, m = it
                try:
                    return s, p, m, quick_hash(p, s)
                except OSError:
                    return None

            with ThreadPoolExecutor(8) as pool:
                for r in pool.map(qh, items):
                    self.done += 1
                    if r:
                        quick[(r[0], r[3])].append((r[1], r[2]))
                    if self.done % 500 == 0:
                        self._publish()

            self.stage = "full hash"
            items = [(k[0], p, m) for k, v in quick.items() if len(v) > 1 for p, m in v]
            self.total, self.done = len(items), 0
            full: dict[tuple[int, str], list[tuple[str, int]]] = defaultdict(list)

            def fh(it):
                s, p, m = it
                try:
                    return s, p, m, full_hash(p)
                except OSError:
                    return None

            with ThreadPoolExecutor(4) as pool:
                for r in pool.map(fh, items):
                    self.done += 1
                    if r:
                        full[(r[0], r[3])].append((r[1], r[2]))
                    if self.done % 100 == 0:
                        self._publish()

            groups = []
            for (size, h), copies in full.items():
                if len(copies) < 2:
                    continue
                files = [{"path": p, "mtime": m * 1000, "size": size} for p, m in copies]
                files.sort(key=lambda f: _keep_score(f["path"], f["mtime"]))
                best = files[0]
                reason = _reason(best, files[1:])
                for i, f in enumerate(files):
                    f["keep"] = i == 0
                    f["reason"] = f"Keep: {reason}" if i == 0 else ""
                groups.append({"hash": h, "size": size, "count": len(files), "wasted": size * (len(files) - 1), "files": files})
            # The extra copies of an exact group stay out. The copy that Raven keeps can still be similar to other photos.
            groups.extend(self._near(photos, {f["path"] for g in groups for f in g["files"] if not f["keep"]}))
            groups.sort(key=lambda g: g["wasted"], reverse=True)
            self.groups = groups
            self.finished = time.time()
            log.info("duplicates in %s: %d groups, %.1f MB wasted", root, len(groups), sum(g["wasted"] for g in groups) / 1e6)
        except Exception as e:  # noqa: BLE001
            log.exception("duplicate scan failed")
            self.error = str(e)
        finally:
            self.running = False
            self.stage = "done"
            self._publish()


    def _near(self, photos: list[tuple[str, int, int]], exact: set[str]) -> list[dict]:
        """Near-duplicate photos (9.7 step 4): pHash within NEAR_MAX bits. Keep the highest resolution."""
        from .. import db

        self.stage = "similar photos"
        photos = [p for p in photos if p[0] not in exact]
        self.total, self.done = len(photos), 0
        cached = {}
        try:
            rows = db.conn().execute("SELECT path, mtime, phash FROM files WHERE phash IS NOT NULL").fetchall()
            cached = {r[0]: (r[1], r[2]) for r in rows}
        except Exception:  # noqa: BLE001
            pass
        items, fresh = [], []

        def one(p):
            path, mtime, size = p
            c = cached.get(path)
            if c and c[0] == mtime and c[1]:
                parts = [int(x) for x in c[1].split(":")]
                if len(parts) == 4:  # hash, width, height, color. An older cache (3 parts) is computed again.
                    return path, mtime, size, *parts
            r = phash(path)
            if r is None:
                return None
            fresh.append((f"{r[0]}:{r[1]}:{r[2]}:{r[3]}", path, mtime))
            return path, mtime, size, *r

        with ThreadPoolExecutor(4) as pool:
            for r in pool.map(one, photos):
                self.done += 1
                if r:
                    items.append(r)
                if self.done % 200 == 0:
                    self._publish()
        if fresh:
            try:
                with db.tx() as c:
                    c.executemany("UPDATE files SET phash=? WHERE path=? AND mtime=?", fresh)
            except Exception:  # noqa: BLE001
                pass
        out = []
        for idx in _near_groups(items):
            members = [items[i] for i in idx]
            members.sort(key=lambda m: (-(m[4] * m[5]), -m[2], m[1]))
            why = f"the highest resolution ({members[0][4]}x{members[0][5]})"
            # A burst (PLAN2 P2-4.3): one size, taken within BURST_S seconds. Keep the sharpest, not the first.
            if len({(m[4], m[5]) for m in members}) == 1 and max(m[1] for m in members) - min(m[1] for m in members) <= BURST_S:
                sharp = {m[0]: sharpness(m[0]) for m in members}
                members.sort(key=lambda m: -sharp[m[0]])
                why = "the sharpest photo of the burst"
            best = members[0]
            files = [{"path": m[0], "mtime": m[1] * 1000, "size": m[2], "keep": i == 0, "pixels": f"{m[4]}x{m[5]}",
                      "reason": f"Keep: {why}" if i == 0 else ""} for i, m in enumerate(members)]
            out.append({"hash": f"near:{best[3]:016x}", "kind": "near", "size": best[2], "count": len(files),
                        "wasted": sum(m[2] for m in members[1:]), "files": files})
        return out

    def forget(self, paths: set[str]) -> None:
        groups = []
        for g in self.groups:
            files = [f for f in g["files"] if f["path"] not in paths]
            if len(files) > 1:
                if not any(f["keep"] for f in files):
                    files[0]["keep"] = True
                groups.append({**g, "files": files, "count": len(files), "wasted": g["size"] * (len(files) - 1)})
        self.groups = groups


job = DupeJob()
