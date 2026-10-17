"""Reclaim (PLAN2 P2-4): one total of the space Raven can free, one row per source, and a planner.

Each row: {id, source, line, bytes, safety, reversible, action, items[{path, size, checked, note}], admin}.
  safety  "safe" (the app or tool makes it again, or a copy stays), "check" (the user decides),
          "lossy" (not used here: lossy compression stays in Suggest mode).
  action  "trash" (journal, undo), "delete" (caches: permanent), "empty-trash", "optimize" (lossless, journal),
          "hardlink" (journal), "command" (a tool's own clean command, maybe as administrator).

Nothing changes in scan(). run() acts only on paths that the last scan listed for that row.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import db, events
from ..platform import disk_bytes
from ..fs import ops
from ..fs.atime import keep_atime
from . import protect

log = logging.getLogger("raven.reclaim")
DAY = 86400
HOME = str(Path.home())
MIN_CACHE = int(os.environ.get("RAVEN_RECLAIM_MIN_CACHE", 5 * 1024 * 1024))
LARGE = int(os.environ.get("RAVEN_RECLAIM_LARGE", 500 * 1024 * 1024))
OLD_DAYS = int(os.environ.get("RAVEN_RECLAIM_OLD_DAYS", 180))
TRASH_DAYS = 30
OPT_LIMIT = 300  # files per optimizer row

SAFETY_ORDER = {"safe": 0, "check": 1, "lossy": 2}
_last: dict = {"id": None, "rows": {}, "path": None}
_lock = threading.Lock()

# ~/.cache folders that belong to browsers: Raven cleans them only when the browser is closed.
BROWSERS = {
    "google-chrome": ("Chrome", ("chrome",)), "chromium": ("Chromium", ("chromium",)),
    "mozilla": ("Firefox", ("firefox", "firefox-bin")), "BraveSoftware": ("Brave", ("brave", "brave-browser")),
    "microsoft-edge": ("Edge", ("msedge", "microsoft-edge")), "zen": ("Zen", ("zen", "zen-bin")),
    "vivaldi": ("Vivaldi", ("vivaldi", "vivaldi-bin")), "opera": ("Opera", ("opera",)), "librewolf": ("LibreWolf", ("librewolf",)),
}
# Developer caches. (path below home, label, a clean command or None to delete the content, safety)
DEV_CACHES = [
    (".cache/pip", "pip", ["pip", "cache", "purge"], "safe"),
    (".cache/uv", "uv", ["uv", "cache", "clean"], "safe"),
    (".npm/_cacache", "npm", ["npm", "cache", "clean", "--force"], "safe"),
    (".local/share/pnpm/store", "pnpm", ["pnpm", "store", "prune"], "safe"),
    (".cache/yarn", "Yarn", ["yarn", "cache", "clean"], "safe"),
    (".bun/install/cache", "Bun", None, "safe"),
    (".cargo/registry/cache", "Cargo", None, "safe"),
    (".cargo/git/db", "Cargo git", None, "safe"),
    ("go/pkg/mod/cache/download", "Go modules", None, "safe"),
    (".cache/go-build", "Go build", ["go", "clean", "-cache"], "safe"),
    (".gradle/caches", "Gradle", None, "safe"),
    (".m2/repository", "Maven", None, "check"),
    (".cache/huggingface/hub", "Hugging Face models", None, "check"),
    (".conda/pkgs", "Conda packages", ["conda", "clean", "--all", "-y"], "safe"),
    (".nuget/packages", "NuGet", None, "safe"),
    (".cache/composer", "Composer", None, "safe"),
    (".cache/deno", "Deno", None, "safe"),
    (".cache/electron", "Electron downloads", None, "safe"),
    (".cache/ms-playwright", "Playwright browsers", None, "check"),
    (".cache/JetBrains", "JetBrains", None, "safe"),
    (".config/Code/CachedData", "VS Code", None, "safe"),
    (".config/Code/Cache", "VS Code", None, "safe"),
    (".config/Code/CachedExtensionVSIXs", "VS Code", None, "safe"),
    (".local/share/Steam/steamapps/shadercache", "Steam shaders", None, "safe"),
    (".cache/thumbnails", "Thumbnails", None, "safe"),
]
DEV_UNDER_CACHE = {p.split("/", 1)[1] for p, *_ in DEV_CACHES if p.startswith(".cache/")}
ARCHIVE_EXTS = ("zip", "7z", "rar", "tar", "tgz", "tar.gz", "tar.zst", "tar.xz", "tar.bz2")
READ_ONLY_KINDS = {"image", "video", "audio", "archive", "app", "disk", "font", "model3d"}


# ---------------------------------------------------------------- helpers


def du(path: str, budget_s: float = 8.0) -> int:
    """Bytes on disk below `path` (blocks, not apparent size), stopping after `budget_s` seconds."""
    total = 0
    t0 = time.monotonic()
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path):
        return disk_bytes(st)
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += disk_bytes(s)
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            continue
        if time.monotonic() - t0 > budget_s:
            break
    return total


def gb(n: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _running(names: tuple[str, ...]) -> bool:
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
            except OSError:
                continue
            if comm in names:
                return True
    except OSError:
        return False
    return False


def _inside(path: str, root: str) -> bool:
    from ..platform import inside

    return inside(path, root)


def _row(rid, source, line, items, safety, action, reversible, **kw) -> dict:
    checked = [i for i in items if i.get("checked")]
    return {"id": rid, "source": source, "line": line, "items": items, "safety": safety, "action": action,
            "reversible": reversible, "bytes": sum(i["size"] for i in checked), "total": sum(i["size"] for i in items), **kw}


# ---------------------------------------------------------------- sources


def app_caches(home: str = HOME) -> list[dict]:
    base = os.path.join(home, ".cache")
    items, browser_items = [], []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return []
    for e in entries:
        if not e.is_dir(follow_symlinks=False) or e.name in DEV_UNDER_CACHE or e.name in ("raven",):
            continue
        size = du(e.path, 4)
        if size < MIN_CACHE:
            continue
        if e.name in BROWSERS:
            label, procs = BROWSERS[e.name]
            busy = _running(procs)
            browser_items.append({"path": e.path, "size": size, "checked": not busy,
                                  "note": f"{label}: close it first" if busy else label, "blocked": busy})
        else:
            items.append({"path": e.path, "size": size, "checked": True, "note": e.name})
    rows = []
    if items:
        items.sort(key=lambda i: -i["size"])
        rows.append(_row("caches", "App caches", f"{len(items)} app caches", items, "safe", "delete", False))
    if browser_items:
        browser_items.sort(key=lambda i: -i["size"])
        rows.append(_row("browsers", "Browser caches", f"{len(browser_items)} browser caches", browser_items, "safe", "delete", False))
    return rows


def dev_caches(home: str = HOME) -> list[dict]:
    items = []
    for rel, label, cmd, safety in DEV_CACHES:
        p = os.path.join(home, rel)
        if not os.path.isdir(p):
            continue
        size = du(p, 6)
        if size < MIN_CACHE:
            continue
        tool = cmd and shutil.which(cmd[0])
        items.append({"path": p, "size": size, "checked": safety == "safe", "note": label, "cmd": cmd if tool else None})
    if not items:
        return []
    items.sort(key=lambda i: -i["size"])
    return [_row("dev-caches", "Developer caches", f"{len(items)} tool caches", items, "safe", "delete", False)]


def _trash_dirs() -> list[Path]:
    from ..platform import trash_home

    out = [trash_home()]
    if hasattr(os, "getuid"):
        from ..platform import mounts

        for _, mnt, fstype in mounts():
            if fstype in ("ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "f2fs"):
                td = Path(mnt) / f".Trash-{os.getuid()}"
                if td.is_dir() and td not in out:
                    out.append(td)
    return out


def trash_items(days: int = TRASH_DAYS) -> list[dict]:
    now = time.time()
    out = []
    for td in _trash_dirs():
        info = td / "info"
        if not info.is_dir():
            continue
        for ip in info.glob("*.trashinfo"):
            try:
                txt = ip.read_text()
            except OSError:
                continue
            m = re.search(r"DeletionDate=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", txt)
            when = time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) if m else now
            f = td / "files" / ip.name[: -len(".trashinfo")]
            if not os.path.lexists(f):
                continue
            age = (now - when) / DAY
            out.append({"path": str(f), "size": du(str(f), 2), "checked": age >= days, "note": f"{int(age)} days in the trash",
                        "info": str(ip)})
    return out


def trash_row() -> list[dict]:
    items = trash_items()
    if not items:
        return []
    items.sort(key=lambda i: -i["size"])
    old = [i for i in items if i["checked"]]
    line = f"{len(items)} items in the trash" + (f", {len(old)} older than {TRASH_DAYS} days" if old else "")
    return [_row("trash", "Trash", line, items, "check", "empty-trash", False)]


def _files(where: str, args: tuple, root: str | None) -> list:
    q = f"SELECT id, path, name, ext, size, mtime, atime FROM files WHERE is_dir=0 AND light=0 AND {where}"
    if root:
        like = root.rstrip("/").replace("%", "\\%").replace("_", "\\_") + "/%"
        q += " AND path LIKE ? ESCAPE '\\'"
        args = (*args, like)
    return db.conn().execute(q, args).fetchall()


def large_old(root: str | None) -> list[dict]:
    cut = time.time() - OLD_DAYS * DAY
    rows = _files("size >= ? AND MAX(COALESCE(atime, 0), COALESCE(mtime, 0)) < ?", (LARGE, cut), root)
    items = []
    for r in rows:
        if not os.path.isfile(r["path"]) or protect.is_software_dir(os.path.dirname(r["path"])):
            continue
        days = int((time.time() - max(r["atime"] or 0, r["mtime"] or 0)) / DAY)
        items.append({"path": r["path"], "size": r["size"], "checked": False, "note": f"not opened for {days} days"})
    if not items:
        return []
    items.sort(key=lambda i: -i["size"])
    return [_row("large-old", "Large and old files", f"{len(items)} files over {gb(LARGE)}, not opened for {OLD_DAYS} days",
                 items, "check", "trash", True, offload=True)]


def _archive_stem(name: str) -> str | None:
    low = name.lower()
    for ext in sorted(ARCHIVE_EXTS, key=len, reverse=True):
        if low.endswith("." + ext):
            return name[: -len(ext) - 1]
    return None


def archive_pairs(root: str | None) -> list[dict]:
    """An archive next to the folder that holds its extracted content: the archive is a second copy."""
    from ..fs import archive

    rows = _files("(" + " OR ".join("name LIKE ?" for _ in ARCHIVE_EXTS) + ")", tuple(f"%.{e}" for e in ARCHIVE_EXTS), root)
    items = []
    for r in rows:
        stem = _archive_stem(r["name"])
        if not stem:
            continue
        folder = os.path.join(os.path.dirname(r["path"]), re.sub(r"\s*\(\d+\)$", "", stem))
        if not os.path.isdir(folder):
            continue
        try:
            members = [m for m in archive._members(r["path"]) if not m["is_dir"]]
        except Exception:  # noqa: BLE001 - a damaged archive is not a pair
            continue
        if not members:
            continue
        top = os.path.basename(folder)
        hit = 0
        for m in members:
            name = m["name"]
            cands = [os.path.join(folder, name)]
            if name.split("/", 1)[0] == top and "/" in name:
                cands.append(os.path.join(folder, name.split("/", 1)[1]))
            if any(os.path.isfile(c) and os.path.getsize(c) == m["size"] for c in cands):
                hit += 1
        if hit / len(members) >= 0.9:
            items.append({"path": r["path"], "size": r["size"], "checked": True,
                          "note": f"its {hit} files are in {top}/"})
    if not items:
        return []
    items.sort(key=lambda i: -i["size"])
    return [_row("archive-pairs", "Archives already extracted", f"{len(items)} archives next to their extracted folders",
                 items, "safe", "trash", True)]


def two_formats(root: str | None) -> list[dict]:
    """report.docx and report.pdf with the same text: the PDF is often an export."""
    rows = _files("ext IN ('pdf','docx','doc','odt','pptx','ppt','odp','xlsx','ods')", (), root)
    by_key: dict[tuple[str, str], list] = {}
    for r in rows:
        stem = r["name"].rsplit(".", 1)[0].lower()
        by_key.setdefault((os.path.dirname(r["path"]), stem), []).append(r)
    items = []
    c = db.conn()

    def words(fid):
        t = " ".join(x[0] or "" for x in c.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id LIMIT 4", (fid,)))
        return set(re.findall(r"[a-z]{3,}", t.lower()))

    for (_, _), group in by_key.items():
        pdfs = [g for g in group if g["ext"] == "pdf"]
        srcs = [g for g in group if g["ext"] != "pdf"]
        if not pdfs or not srcs:
            continue
        a, b = words(pdfs[0]["id"]), words(srcs[0]["id"])
        if not a or not b:
            continue
        sim = len(a & b) / max(1, len(a | b))
        if sim >= 0.6:
            items.append({"path": pdfs[0]["path"], "size": pdfs[0]["size"], "checked": False,
                          "note": f"same text as {srcs[0]['name']} ({int(sim * 100)}%)"})
    if not items:
        return []
    return [_row("two-formats", "Same document twice", f"{len(items)} PDFs with the same text as a document next to them",
                 items, "check", "trash", True)]


def duplicate_folders(root: str | None) -> list[dict]:
    """Folders whose files (names and sizes) are the same, checked with a quick hash of every file."""
    from .dupes import quick_hash

    q = ("SELECT parent, group_concat(name || ':' || size, '|') sig, SUM(size) total, COUNT(*) n FROM "
         "(SELECT parent, name, size FROM files WHERE is_dir=0 AND light=0 ORDER BY parent, name) GROUP BY parent HAVING n >= 2 AND total >= ?")
    groups: dict[str, list] = {}
    for parent, sig, total, n in db.conn().execute(q, (1024 * 1024,)):
        if root and not _inside(parent, root):
            continue
        groups.setdefault(sig, []).append((parent, total, n))
    items = []
    for sig, dirs in groups.items():
        if len(dirs) < 2:
            continue
        names = [s.rsplit(":", 1)[0] for s in sig.split("|")]

        def fp(d):
            try:
                return tuple(quick_hash(os.path.join(d, nm), os.path.getsize(os.path.join(d, nm))) for nm in names)
            except OSError:
                return None

        dirs.sort(key=lambda x: (len(x[0]), x[0]))
        keep = dirs[0]
        kf = fp(keep[0])
        if kf is None:
            continue
        for d, total, n in dirs[1:]:
            if _inside(d, keep[0]) or _inside(keep[0], d):
                continue
            if fp(d) == kf:
                items.append({"path": d, "size": total, "checked": False, "is_dir": True,
                              "note": f"same {n} files as {os.path.relpath(keep[0], HOME) if keep[0].startswith(HOME) else keep[0]}"})
    # Report the top folder only: a copy of a copy's subfolder is part of it.
    items.sort(key=lambda i: len(i["path"]))
    top: list[dict] = []
    for it in items:
        if not any(_inside(it["path"], t["path"]) for t in top):
            top.append(it)
    if not top:
        return []
    return [_row("dup-folders", "Duplicate folders", f"{len(top)} folders that copy another folder", top, "check", "trash", True)]


OPT_RULES = {  # ext -> (minimum size, expected gain for the estimate, label)
    "png": (200 * 1024, 0.15, "PNG, smaller with the same pixels"),
    "jpg": (300 * 1024, 0.05, "JPEG, smaller with the same pixels (jpegtran)"),
    "jpeg": (300 * 1024, 0.05, "JPEG, smaller with the same pixels (jpegtran)"),
    "pdf": (1024 * 1024, 0.10, "PDF, same pages, less overhead"),
    "wav": (5 * 1024 * 1024, 0.45, "WAV to FLAC, the same sound"),
}


def optimizable(root: str | None) -> list[dict]:
    exts = [e for e in OPT_RULES if e not in ("jpg", "jpeg") or shutil.which("jpegtran")]
    if "wav" in exts and not shutil.which("ffmpeg"):
        exts.remove("wav")
    rows = _files(f"ext IN ({','.join('?' * len(exts))})", tuple(exts), root)
    done = _optimized()
    items = []
    for r in rows:
        mn, gain, label = OPT_RULES[r["ext"]]
        if (r["size"] or 0) < mn or r["path"] in done or protect.is_software_dir(os.path.dirname(r["path"])):
            continue
        items.append({"path": r["path"], "size": int(r["size"] * gain), "checked": True, "note": label, "file_size": r["size"]})
    if not items:
        return []
    items.sort(key=lambda i: -i["size"])
    items = items[:OPT_LIMIT]
    return [_row("optimize", "Lossless optimizer", f"{len(items)} files can be smaller with no change in what they show",
                 items, "safe", "optimize", True, estimate=True)]


def hardlinks(root: str | None) -> list[dict]:
    from ..fs.kinds import group_of
    from .dupes import job

    items = []
    for g in job.groups:
        if g.get("kind") == "near":
            continue
        keep = next((f for f in g["files"] if f["keep"]), None)
        if not keep:
            continue
        try:
            dev, ino = os.stat(keep["path"]).st_dev, os.stat(keep["path"]).st_ino
        except OSError:
            continue
        for f in g["files"]:
            if f["keep"] or (root and not _inside(f["path"], root)):
                continue
            if group_of(os.path.basename(f["path"]), False) not in READ_ONLY_KINDS:
                continue
            try:
                st = os.stat(f["path"])
            except OSError:
                continue
            if st.st_dev != dev or st.st_ino == ino:
                continue
            items.append({"path": f["path"], "size": f["size"], "checked": False, "keep": keep["path"],
                          "note": f"one file with {os.path.basename(keep['path'])}"})
    if not items:
        return []
    # An alternative to "Duplicates" (trash the copies): the same files, so it does not add to the total.
    return [_row("hardlinks", "Duplicates as one file", f"{len(items)} duplicates can share one copy on disk (hard links)",
                 items, "check", "hardlink", True, alternative=True)]


VIDEO_SAME = 0.97  # cosine of the mean keyframe vectors (SigLIP 2) of two videos


def similar_videos(root: str | None) -> list[dict]:
    """The same video twice (another format, size or cut): the mean of the keyframe vectors is almost the
    same, and so is the length. Raven keeps the larger file (usually the better quality)."""
    import numpy as np

    c = db.conn()
    try:
        rows = c.execute(
            "SELECT x.file_id, f.path, f.size, MAX(x.at) len, v.embedding FROM visual x JOIN files f ON f.id = x.file_id "
            "JOIN visual_vec v ON v.rowid = x.id WHERE x.kind='frame' GROUP BY x.id ORDER BY x.file_id"
        ).fetchall()
    except Exception:  # noqa: BLE001 - no visual index yet
        return []
    by: dict[int, dict] = {}
    for fid, path, size, at, emb in rows:
        if root and not _inside(path, root):
            continue
        d = by.setdefault(fid, {"path": path, "size": size or 0, "len": 0.0, "vecs": []})
        d["vecs"].append(np.frombuffer(emb, dtype=np.float32))
        d["len"] = max(d["len"], at or 0.0)
    vids = [d for d in by.values() if len(d["vecs"]) >= 2 and os.path.isfile(d["path"])]
    for d in vids:
        m = np.mean(d["vecs"], axis=0)
        d["mean"] = m / (np.linalg.norm(m) or 1)
    items, used = [], set()
    vids.sort(key=lambda d: -d["size"])
    for i, a in enumerate(vids):
        if a["path"] in used:
            continue
        for b in vids[i + 1:]:
            if b["path"] in used:
                continue
            if abs(a["len"] - b["len"]) > max(2.0, 0.1 * max(a["len"], b["len"])):
                continue
            if float(a["mean"] @ b["mean"]) >= VIDEO_SAME:
                used.add(b["path"])
                items.append({"path": b["path"], "size": b["size"], "checked": False,
                              "note": f"the same video as {os.path.basename(a['path'])}"})
    if not items:
        return []
    return [_row("similar-videos", "Same video twice", f"{len(items)} videos that look the same as a larger copy",
                 items, "check", "trash", True)]


def _cmd_size(cmd: list[str], timeout: int = 10) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _parse_size(s: str) -> int:
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)i?B?", s, re.I)
    if not m:
        return 0
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


def docker_rows() -> list[dict]:
    if not shutil.which("docker"):
        return []
    out = _cmd_size(["docker", "system", "df", "--format", "{{json .}}"], 15)
    if not out:
        return []
    items = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rec = _parse_size(str(d.get("Reclaimable", "0")).split(" ")[0])
        t = d.get("Type", "")
        if rec < 50 * 1024 * 1024:
            continue
        if t == "Images":
            items.append({"path": "docker:images", "size": rec, "checked": False, "note": "images that no container uses",
                          "cmd": ["docker", "image", "prune", "-af"]})
        elif t == "Build Cache":
            items.append({"path": "docker:build-cache", "size": rec, "checked": True, "note": "build cache",
                          "cmd": ["docker", "builder", "prune", "-af"]})
    if not items:
        return []
    return [_row("docker", "Docker", "Docker images and build cache", items, "check", "command", False)]


def system_rows() -> list[dict]:
    """System logs and package caches. The clean commands need an administrator (pkexec)."""
    items = []
    out = _cmd_size(["journalctl", "--disk-usage"])
    if out:
        m = re.search(r"take up ([\d.]+[KMGT]?)", out)
        size = _parse_size(m.group(1)) if m else 0
        if size > 300 * 1024 * 1024:
            items.append({"path": "system:journal", "size": size - 200 * 1024 * 1024, "checked": True,
                          "note": "system logs: keep the newest 200 MB", "cmd": ["journalctl", "--vacuum-size=200M"]})
    for path, cmd in (("/var/cache/pacman/pkg", ["paccache", "-rk2"] if shutil.which("paccache") else ["pacman", "-Sc", "--noconfirm"]),
                      ("/var/cache/apt/archives", ["apt-get", "clean"]), ("/var/cache/dnf", ["dnf", "clean", "packages"])):
        if os.path.isdir(path) and shutil.which(cmd[0]):
            size = du(path, 4)
            if size > 100 * 1024 * 1024:
                share = 0.6 if cmd[0] == "paccache" else 1.0  # paccache keeps the 2 newest versions
                items.append({"path": f"system:{path}", "size": int(size * share), "checked": True,
                              "note": f"package cache in {path}", "cmd": cmd})
    for path in ("/var/lib/systemd/coredump", "/var/crash"):
        if os.path.isdir(path):
            size = du(path, 3)
            if size > 20 * 1024 * 1024:
                items.append({"path": f"system:{path}", "size": size, "checked": True, "note": "crash dumps",
                              "cmd": ["find", path, "-type", "f", "-delete"]})
    if shutil.which("flatpak"):
        items.append({"path": "system:flatpak", "size": 0, "checked": False, "note": "Flatpak runtimes that no app uses",
                      "cmd": ["flatpak", "uninstall", "--unused", "--noninteractive"], "user_cmd": True})
    if not items:
        return []
    return [_row("system", "System logs and package caches", f"{len(items)} system items", items, "safe", "command", False,
                 admin=True)]


def fs_compression(root: str | None) -> list[dict]:
    """btrfs: turn on transparent compression for cold folders. ext4 has none, so the row does not show there."""
    from ..platform import mounts

    target = root or HOME
    best = ("", "")
    for _, mnt, fstype in mounts():
        if _inside(target, mnt) and len(mnt) > len(best[0]):
            best = (mnt, fstype)
    if best[1] != "btrfs" or not shutil.which("btrfs"):
        return []
    items = [{"path": target, "size": 0, "checked": False, "note": "zstd compression for new and rewritten files",
              "cmd": ["btrfs", "property", "set", target, "compression", "zstd"], "user_cmd": True}]
    return [_row("fs-compress", "File system compression", "btrfs can compress files on the fly", items, "safe", "command", False)]


# The order sets which row keeps a path that two sources find: the larger saving first, the optimizer last.
SOURCES = [app_caches, dev_caches, trash_row, docker_rows, system_rows]
ROOT_SOURCES = [large_old, archive_pairs, "junk", hardlinks, similar_videos, two_formats, duplicate_folders, optimizable,
                fs_compression]


# ---------------------------------------------------------------- scan, plan, run


def _junk_rows(root: str) -> list[dict]:
    """The Space scan's junk and duplicate rows (suggest.py), in this row format."""
    from .suggest import suggestions

    out = []
    try:
        rows = suggestions(root)
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        if r.get("action") != "trash" or not r.get("items"):
            continue
        items = [{"path": i["path"], "size": i["size"], "checked": i["checked"], "note": i.get("note", ""), "is_dir": i.get("is_dir")}
                 for i in r["items"]]
        out.append(_row(r["id"], "Junk" if r["id"].startswith("junk:") else "Duplicates", r["sentence"], items,
                        "safe" if r["id"].startswith("junk:") else "check", "trash", True))
    return out


def scan(root: str | None = None) -> dict:
    t0 = time.perf_counter()
    root = os.path.abspath(os.path.expanduser(root)) if root else HOME
    rows: list[dict] = []
    timings = {}
    for fn in SOURCES:
        t = time.perf_counter()
        try:
            rows += fn()
        except Exception:  # noqa: BLE001 - one source must not hide the others
            log.exception("reclaim source %s failed", fn.__name__)
        timings[fn.__name__] = round((time.perf_counter() - t) * 1000)
    for fn in ROOT_SOURCES:
        t = time.perf_counter()
        name = fn if isinstance(fn, str) else fn.__name__
        try:
            rows += _junk_rows(root) if fn == "junk" else fn(root)
        except Exception:  # noqa: BLE001
            log.exception("reclaim source %s failed", name)
        timings[name] = round((time.perf_counter() - t) * 1000)
    # One path in one row only: the first source wins (caches before junk).
    seen: set[str] = set()

    def covered(p: str) -> bool:
        cur = p
        while True:
            if cur in seen:
                return True
            up = os.path.dirname(cur)
            if up == cur or not up:
                return False
            cur = up

    for r in rows:
        if r.get("alternative"):
            continue
        keep = []
        for i in r["items"]:
            if covered(i["path"]):
                continue
            keep.append(i)
        for i in keep:
            seen.add(i["path"])
        r["items"] = keep
        r["bytes"] = sum(i["size"] for i in keep if i.get("checked"))
        r["total"] = sum(i["size"] for i in keep)
    # A folder rule "protect" (PLAN2 P2-5) keeps its files out of every row.
    for r in rows:
        r["items"] = [i for i in r["items"] if i["path"].startswith(("docker:", "system:")) or not protect.user_protected(i["path"])]
        r["bytes"] = sum(i["size"] for i in r["items"] if i.get("checked"))
        r["total"] = sum(i["size"] for i in r["items"])
    rows = [r for r in rows if r["items"]]
    # PLAN2 P2-8.3: a row you rejected stays away for 30 days; a source you always reject stops.
    from .. import habits

    q = habits.quiet()
    quiet_n = sum(1 for r in rows if r["id"] in q["hidden"] or r["id"].split(":", 1)[0] in q["muted"])
    rows = [r for r in rows if r["id"] not in q["hidden"] and r["id"].split(":", 1)[0] not in q["muted"]]
    rows.sort(key=lambda r: (SAFETY_ORDER.get(r["safety"], 3), not r["reversible"], -r["total"]))
    sid = f"{int(time.time() * 1000):x}"
    with _lock:
        _last.update(id=sid, rows={r["id"]: r for r in rows}, path=root)
    disk = shutil.disk_usage(root)
    return {"id": sid, "path": root, "rows": rows, "total": sum(r["total"] for r in rows if not r.get("alternative")),
            "checked": sum(r["bytes"] for r in rows if not r.get("alternative")), "quiet": quiet_n, "ms": round((time.perf_counter() - t0) * 1000), "timings": timings,
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free}, "forecast": forecast(root)}


def plan(target: int, scan_id: str | None = None) -> dict:
    """The safest set of items that frees at least `target` bytes: safe rows first, then check rows,
    reversible before permanent, larger items first. Blocked items (an open browser) never count."""
    with _lock:
        if scan_id and scan_id != _last["id"]:
            raise ValueError("The scan changed. Scan again.")
        rows = list(_last["rows"].values())
    rows.sort(key=lambda r: (SAFETY_ORDER.get(r["safety"], 3), not r["reversible"], r["action"] == "empty-trash", -r["total"]))
    picked: dict[str, list[str]] = {}
    got = 0
    for r in rows:
        if got >= target:
            break
        if (r["action"] == "command" and r.get("admin")) or r.get("alternative"):
            continue  # an administrator prompt is not a default, and an alternative counts its files twice
        for i in sorted(r["items"], key=lambda i: -i["size"]):
            if got >= target:
                break
            if i.get("blocked") or i["size"] <= 0:
                continue
            picked.setdefault(r["id"], []).append(i["path"])
            got += i["size"]
    return {"target": target, "bytes": got, "enough": got >= target, "rows": picked}


def _delete_content(path: str) -> int:
    """Delete what is inside a cache folder and keep the folder (some apps need it to exist)."""
    before = du(path, 30)
    for e in os.scandir(path):
        try:
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path, ignore_errors=True)
            else:
                os.unlink(e.path)
        except OSError:
            pass
    return max(0, before - du(path, 30))


def _run_cmd(cmd: list[str], admin: bool) -> tuple[bool, str]:
    full = (["pkexec"] + cmd) if admin and os.geteuid() != 0 else cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"exit {r.returncode}"]
        if r.returncode in (126, 127) and admin:
            return False, "The administrator prompt was closed, or no polkit agent runs."
        return False, msg[0][:200]
    return True, ""


def run(scan_id: str, selection: dict[str, list[str]]) -> dict:
    with _lock:
        if scan_id != _last["id"]:
            raise ValueError("The scan changed. Scan again.")
        rows = dict(_last["rows"])
    disk_before = shutil.disk_usage(_last["path"] or HOME).free
    g = ops.Group("reclaim", "Free space")
    results, errors = [], []
    to_trash_bytes = 0
    try:
        for rid, paths in selection.items():
            r = rows.get(rid)
            if not r:
                errors.append(f"unknown row {rid}")
                continue
            by_path = {i["path"]: i for i in r["items"]}
            for p in paths:
                it = by_path.get(p)
                if it is None:
                    errors.append(f"{os.path.basename(p)} is not in the scan")
                    continue
                if it.get("blocked"):
                    errors.append(f"{it['note']}")
                    continue
                try:
                    if r["action"] == "trash":
                        if os.path.lexists(p):
                            ops.trash([p], source="reclaim", group=g)
                            to_trash_bytes += it["size"]
                    elif r["action"] == "delete":
                        ok = bool(it.get("cmd")) and _run_cmd(it["cmd"], False)[0]
                        if not ok and os.path.isdir(p):
                            _delete_content(p)  # no clean command, or it failed: delete the content
                    elif r["action"] == "empty-trash":
                        if os.path.isdir(p) and not os.path.islink(p):
                            shutil.rmtree(p)
                        elif os.path.lexists(p):
                            os.unlink(p)
                        if it.get("info"):
                            try:
                                os.unlink(it["info"])
                            except OSError:
                                pass
                    elif r["action"] == "optimize":
                        saved = optimize_one(p, g)
                        if saved is None:
                            continue
                        to_trash_bytes += it.get("file_size", 0)
                    elif r["action"] == "hardlink":
                        hardlink_one(it["keep"], p, g)
                        to_trash_bytes += it["size"]
                    elif r["action"] == "command":
                        ok, msg = _run_cmd(it["cmd"], bool(r.get("admin")) and not it.get("user_cmd"))
                        if not ok:
                            errors.append(f"{it['note']}: {msg}")
                            continue
                    results.append(p)
                except (OSError, ops.OpError, ValueError) as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
    finally:
        g.commit()
    freed = max(0, shutil.disk_usage(_last["path"] or HOME).free - disk_before)
    events.publish("space_changed", path=_last["path"])
    return {"done": len(results), "errors": errors, "freed": freed, "in_trash": to_trash_bytes, "journal": ops.peek(),
            "label": g.label}


# ---------------------------------------------------------------- lossless optimizers and hard links


def _same_pixels(a: str, b: str) -> bool:
    from PIL import Image

    with Image.open(a) as x, Image.open(b) as y:
        if x.size != y.size:
            return False
        mode = "RGBA" if "A" in x.mode or "A" in y.mode or x.mode == "P" else "RGB"
        return x.convert(mode).tobytes() == y.convert(mode).tobytes()


def _pcm_md5(path: str) -> str | None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a", "-f", "md5", "-"],
                       capture_output=True, text=True, timeout=600)
    return r.stdout.strip() if r.returncode == 0 else None


def _pdf_text(path: str) -> list[str]:
    from .. import pdf

    with pdf.open_pdf(path) as d:
        return [pdf.page_text(p) for p in d]


def optimize_one(path: str, g: ops.Group) -> int | None:
    """Write a smaller copy, check that it shows the same thing, then swap: the original goes to the trash
    (one undo brings it back). Returns the bytes saved, or None when the copy is not smaller or not the same."""
    ext = path.rsplit(".", 1)[-1].lower()
    st = os.stat(path)
    out_ext = "flac" if ext == "wav" else ext
    fd, tmp = tempfile.mkstemp(suffix=f".{out_ext}", dir=os.path.dirname(path), prefix=".raven-opt-")
    os.close(fd)
    try:
        with keep_atime(path):
            if ext == "png":
                from PIL import Image

                with Image.open(path) as im:
                    im.save(tmp, "PNG", optimize=True, compress_level=9)
                same = _same_pixels(path, tmp)
            elif ext in ("jpg", "jpeg"):
                r = subprocess.run(["jpegtran", "-optimize", "-copy", "all", "-outfile", tmp, path], capture_output=True, timeout=120)
                same = r.returncode == 0 and _same_pixels(path, tmp)
            elif ext == "pdf":
                # pikepdf (qpdf, MPL-2.0) writes only the objects in use, with compressed streams.
                import pikepdf

                try:
                    with pikepdf.open(path) as d:
                        if d.is_encrypted:
                            return None
                        d.save(tmp, compress_streams=True, recompress_flate=True,
                               object_stream_mode=pikepdf.ObjectStreamMode.generate)
                except pikepdf.PasswordError:
                    return None
                same = _pdf_text(path) == _pdf_text(tmp)
            elif ext == "wav":
                r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0", "-c:a", "flac",
                                    "-compression_level", "8", tmp], capture_output=True, timeout=1200)
                a = _pcm_md5(path) if r.returncode == 0 else None
                same = a is not None and a == _pcm_md5(tmp)
            else:
                return None
        new_size = os.path.getsize(tmp)
        if not same or new_size >= st.st_size * 0.98:
            return None
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        try:
            os.chmod(tmp, st.st_mode & 0o7777)
        except OSError:
            pass
        final = path if out_ext == ext else path[: -len(ext)] + out_ext
        if final != path and os.path.lexists(final):
            final = ops.unique_path(final)
        ops.trash([path], source="reclaim", group=g)
        os.rename(tmp, final)
        g.add("create", None, final)
        tmp = None
        _note_optimized(final)
        return st.st_size - new_size
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def hardlink_one(keep: str, dup: str, g: ops.Group) -> None:
    from .dupes import full_hash

    a, b = os.stat(keep), os.stat(dup)
    if a.st_dev != b.st_dev or a.st_size != b.st_size:
        raise ValueError("not on the same disk, or not the same size")
    if full_hash(keep) != full_hash(dup):
        raise ValueError("the files differ now")
    ops.trash([dup], source="reclaim", group=g)
    os.link(keep, dup)
    g.add("create", None, dup)


_OPT_SCHEMA = "CREATE TABLE IF NOT EXISTS optimized (path TEXT PRIMARY KEY, ts INTEGER)"


def _note_optimized(path: str) -> None:
    with db.tx() as c:
        c.execute(_OPT_SCHEMA)
        c.execute("INSERT OR REPLACE INTO optimized VALUES (?, ?)", (path, int(time.time())))


def _optimized() -> set[str]:
    c = db.conn()
    c.execute(_OPT_SCHEMA)
    return {r[0] for r in c.execute("SELECT path FROM optimized")}


# ---------------------------------------------------------------- offload


def offload(paths: list[str], target: str) -> dict:
    """Move cold files to another drive or a share, as one undo. Raven keeps a record, so Space can show
    where they went (P2-4.2 "Offload")."""
    target = os.path.abspath(os.path.expanduser(target))
    if not os.path.isdir(target):
        raise ValueError(f"Not a folder: {target}")
    g = ops.Group("reclaim", f"Move {len(paths)} files to {os.path.basename(target) or target}")
    moved, errors = [], []  # moved: (source, target) pairs
    try:
        for p in paths:
            if _inside(target, p):
                errors.append(f"{os.path.basename(p)}: the target is inside it")
                continue
            try:
                moved += [(p, d) for d in ops.move([p], target, "keep_both", "reclaim", group=g)]
            except (OSError, ops.OpError) as e:
                errors.append(f"{os.path.basename(p)}: {e}")
    finally:
        g.commit()
    with db.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS offloaded (src TEXT, dst TEXT, size INTEGER, ts INTEGER)")
        for p, d in moved:
            try:
                size = os.path.getsize(d)
            except OSError:
                size = 0
            c.execute("INSERT INTO offloaded VALUES (?,?,?,?)", (p, d, size, int(time.time())))
    return {"moved": len(moved), "errors": errors, "journal": ops.peek(), "label": g.label}


def offloaded() -> list[dict]:
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS offloaded (src TEXT, dst TEXT, size INTEGER, ts INTEGER)")
    return [{"src": s, "dst": d, "size": z, "ts": t, "there": os.path.exists(d)}
            for s, d, z, t in c.execute("SELECT src, dst, size, ts FROM offloaded ORDER BY ts DESC LIMIT 500")]


# ---------------------------------------------------------------- forecast


def snapshot_disk(path: str = HOME) -> None:
    """One used-space value per day for the disk that holds `path` (size_snapshots, path "disk:<mount>")."""
    du_ = shutil.disk_usage(path)
    key = f"disk:{_mount_of(path)}"
    today = int(time.time()) // DAY * DAY
    with db.tx() as c:
        c.execute("DELETE FROM size_snapshots WHERE path=? AND ts >= ?", (key, today))
        c.execute("INSERT INTO size_snapshots VALUES (?,?,?)", (int(time.time()), key, du_.used))
    if du_.used / du_.total >= 0.9:
        events.publish("disk_full_soon", path=path, used=du_.used, total=du_.total)


def _mount_of(path: str) -> str:
    from ..platform import mounts

    best = "/"
    for _, mnt, _ in mounts():
        if _inside(path, mnt) and len(mnt) > len(best):
            best = mnt
    return best


def forecast(path: str = HOME) -> dict:
    """Days until the disk is full, from a straight line through the daily used-space values (last 60 days).
    None when there are fewer than 3 days or the use does not grow."""
    du_ = shutil.disk_usage(path)
    key = f"disk:{_mount_of(path)}"
    since = time.time() - 60 * DAY
    pts = [(ts / DAY, used) for ts, used in db.conn().execute(
        "SELECT ts, size FROM size_snapshots WHERE path=? AND ts >= ? ORDER BY ts", (key, since))]
    out = {"used_pct": round(du_.used / du_.total * 100, 1), "warn": du_.used / du_.total >= 0.9, "days": None,
           "per_day": None, "points": len(pts)}
    if len(pts) >= 3 and pts[-1][0] - pts[0][0] >= 2:
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else 0
        out["per_day"] = int(slope)
        if slope > 1024 * 1024:  # more than 1 MB a day
            out["days"] = int(du_.free / slope)
    return out


THUMB_LIMIT = 1024**3


def trim_thumbs(limit: int = THUMB_LIMIT) -> int:
    """Keep the thumbnail cache under `limit`: delete the thumbnails used longest ago. Only the .webp files at
    the top of the cache: the frames and page images in its subfolders belong to the visual index."""
    from ..config import THUMB_DIR

    files = []
    for e in os.scandir(THUMB_DIR):
        if e.is_file() and e.name.endswith(".webp"):
            st = e.stat()
            files.append((max(st.st_atime, st.st_mtime), st.st_size, e.path))
    total = sum(f[1] for f in files)
    gone = 0
    for _, size, path in sorted(files):
        if total <= limit:
            break
        try:
            os.unlink(path)
            total -= size
            gone += 1
        except OSError:
            pass
    return gone


def disk_loop(stop: threading.Event) -> None:
    """A daily used-space value for the forecast, a warning at 90%, and the thumbnail limit. Every 30 minutes."""
    while not stop.is_set():
        try:
            snapshot_disk(HOME)
            trim_thumbs()
        except Exception:  # noqa: BLE001
            log.exception("disk snapshot failed")
        stop.wait(1800)
