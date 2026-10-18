"""Space scanner (section 9.6): folder sizes, categories, ages, largest files and junk.

One scan walks a folder tree on one device and keeps a tree of folders in memory.
Each folder node holds its total size, a size for each category and age bucket,
and its largest files. Small files only add to the totals, so memory stays small
even for a large home folder.
"""

from __future__ import annotations

import heapq
import logging
import os
import re
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .. import db, events
from ..fs.kinds import ext_of
from . import protect
from ..platform import below, disk_bytes, inside, like_below

log = logging.getLogger("raven.space")

CATEGORIES = ["Video", "Photos", "Documents", "Archives", "Installers", "Code dependencies", "Caches", "Other"]
VIDEO, PHOTOS, DOCS, ARCHIVES, INSTALLERS, CODEDEPS, CACHES, OTHER = range(8)
AGES = ["Last month", "1 to 6 months", "6 to 12 months", "Over 12 months"]

_EXT_CAT = {}
for _c, _exts in (
    (VIDEO, "mp4 mkv webm mov avi m4v wmv flv mpg mpeg ts"),
    (PHOTOS, "jpg jpeg png gif webp bmp tif tiff heic heif avif jxl raw cr2 nef arw dng svg psd"),
    (DOCS, "pdf doc docx odt rtf txt md markdown ppt pptx odp xls xlsx ods csv epub tex"),
    (ARCHIVES, "zip 7z tar gz tgz zst xz bz2 rar iso img"),
    (INSTALLERS, "appimage deb rpm exe msi dmg pkg flatpakref snap apk"),
):
    for _e in _exts.split():
        _EXT_CAT[_e] = _c

# Folders whose whole content is code dependencies or caches.
CODEDEP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "site-packages", ".tox", ".gradle", ".pytest_cache",
                ".mypy_cache", ".ruff_cache", ".next", ".nuxt", ".parcel-cache", ".turbo", ".dart_tool", "bower_components"}
CACHE_DIRS = {".cache", "cache", "Cache", "caches", "Caches", "CachedData", "GPUCache", "Code Cache", "thumbnails",
              ".npm", "_cacache", ".yarn-cache", "ShaderCache"}

TEMP_NAME = re.compile(r"(~$|\.tmp$|\.temp$|\.part$|\.crdownload$|^\.DS_Store$|^Thumbs\.db$)", re.I)
# Junk only. Each one is safe to delete: the tool builds it again.
TOOL_CACHES = {".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next", ".nuxt", ".parcel-cache", ".turbo", ".dart_tool"}
PACKAGE_CACHES = {".cache/pip", ".npm/_cacache", ".cache/yarn", ".cache/pnpm", ".local/share/pnpm/store", ".cache/uv",
                  ".cache/go-build", ".gradle/caches", ".cache/thumbnails"}
INSTALLER_EXTS = {"appimage", "deb", "rpm", "exe", "msi", "dmg", "pkg"}
DAY = 86400
# The entry names that _dir_junk and the project rule read. A refresh keeps them for a folder that it does not read.
MARK_NAMES = frozenset(protect.PROJECT_MARKERS) | {"package.json", "setup.py", "pyproject.toml", "CMakeLists.txt",
                                                 "Makefile", "Cargo.toml"}

TOP_FILES_PER_DIR = 12
TOP_FILES_GLOBAL = 600


@dataclass(slots=True)
class Node:
    name: str
    path: str
    size: int = 0
    files: int = 0
    dirs: int = 0
    entries: int = 0  # direct entries; 0 means an empty folder
    mtime: int = 0
    cats: list = field(default_factory=lambda: [0] * 8)
    ages: list = field(default_factory=lambda: [0] * 4)
    children: list = field(default_factory=list)
    top: list = field(default_factory=list)  # min-heap of (size, name, mtime, atime, cat)
    unreadable: bool = False
    # A refresh (Scanner(root, base=...)) reads a folder again only when its change time (ns) differs.
    stamp: int = 0
    # The names next to the folder's entries that the junk and project rules read (MARK_NAMES).
    marks: frozenset = frozenset()


@dataclass
class JunkItem:
    path: str
    size: int
    rule: str
    reason: str
    is_dir: bool
    mtime: int
    checked: bool = True


@dataclass
class ScanResult:
    root: Node
    started: float
    finished: float
    files: int
    largest: list  # [(size, path, mtime, atime, cat)] largest first
    junk: list[JunkItem]
    dev: int
    refreshed: bool = False  # a refresh of the base scan, not a full walk
    full: float = 0.0  # the end of the last full walk (a refresh keeps it)


def _age_bucket(now: float, atime: int, mtime: int) -> int:
    last = max(atime, mtime)
    days = (now - last) / DAY
    if days <= 31:
        return 0
    if days <= 183:
        return 1
    if days <= 365:
        return 2
    return 3


def _file_cat(ext: str, inherited: int | None) -> int:
    if inherited is not None:
        return inherited
    return _EXT_CAT.get(ext, OTHER)


class Scanner:
    """A full walk, or with `base` (the last scan of the same root) a refresh: a folder whose change time is the
    same keeps its own files from the base, and only changed folders are read again. A file that grows in
    place does not change its folder's time, so a refresh misses it. A full scan finds it."""

    def __init__(self, root: str, base: ScanResult | None = None) -> None:
        self.root = os.path.abspath(root)
        self.base = base
        self._kept: set[str] = set()  # folders whose own files came from the base
        self._kept_trees: set[str] = set()  # totals-only folders (deep in dependencies) taken whole from the base
        self.now = time.time()
        self.count = 0
        self.bytes = 0
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self._seen_inodes: set[tuple[int, int]] = set()
        self._largest: list = []
        self._junk: list[JunkItem] = []
        self.home = os.path.expanduser("~")

    # ------------------------------------------------------------ walk

    def _walk(self, path: str, name: str, dev: int, inherited: int | None, depth: int, pools: list[ThreadPoolExecutor],
              hidden: bool = False, inner: int = 0, proj: bool = False, old: Node | None = None) -> Node:
        """Walk one folder. `hidden`: a folder above this one starts with a dot.
        `inner`: how deep this folder is inside a code-dependency or cache folder.
        `old`: this folder in the base scan. Its subfolders are refreshed (_diff), not walked again."""
        node = Node(name=name, path=path)
        try:
            st = os.lstat(path)
            node.mtime = int(st.st_mtime)
            node.stamp = st.st_mtime_ns
            it = os.scandir(path)
        except OSError:
            node.unreadable = True
            return node
        subdirs: list[tuple[str, str, int | None]] = []
        sibling_names: set[str] = set()
        safe_zone = not hidden and inherited is None
        with it:
            for de in it:
                if self.cancel.is_set():
                    break
                node.entries += 1
                sibling_names.add(de.name)
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError:
                    continue
                mode = st.st_mode
                if stat.S_ISDIR(mode):
                    # Windows: DirEntry.stat() gives st_dev 0, so a mount point is not visible here.
                    if st.st_dev and st.st_dev != dev:
                        continue  # another file system (mount point)
                    child_cat = inherited
                    if child_cat is None:
                        if de.name in CODEDEP_DIRS:
                            child_cat = CODEDEPS
                        elif de.name in CACHE_DIRS:
                            child_cat = CACHES
                    subdirs.append((de.path, de.name, child_cat))
                    continue
                if not stat.S_ISREG(mode):
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    with self._lock:
                        if key in self._seen_inodes:
                            continue
                        self._seen_inodes.add(key)
                size = disk_bytes(st)
                ext = ext_of(de.name)
                cat = _file_cat(ext, inherited)
                mtime, atime = int(st.st_mtime), int(st.st_atime)
                node.size += size
                node.files += 1
                node.cats[cat] += size
                node.ages[_age_bucket(self.now, atime, mtime)] += size
                # Deep inside dependency or cache folders, keep totals only. It saves memory.
                if inner < 2:
                    item = (size, de.name, mtime, atime, cat)
                    if len(node.top) < TOP_FILES_PER_DIR:
                        heapq.heappush(node.top, item)
                    elif size > node.top[0][0]:
                        heapq.heapreplace(node.top, item)
                self._file_seen(de.path, de.name, ext, size, mtime, atime, cat, path, safe_zone)
        node.marks = frozenset(sibling_names & MARK_NAMES)
        # Inside a project or a software folder, the empty-folder rule does not apply.
        here_proj = proj or bool(sibling_names & protect.PROJECT_MARKERS) or name in protect.TOOL_NAMES
        if subdirs:
            prev = {c.name: c for c in old.children} if old else {}
            # Each of the first levels has its own pool. A task waits only on tasks of a deeper
            # pool, so the pools cannot deadlock.
            jobs = []
            for sp, sn, sc in subdirs:
                ch_hidden = hidden or sn.startswith(".")
                ch_inner = inner + 1 if sc is not None else 0
                args = (sp, sn, prev.get(sn), dev, sc, depth + 1, pools, ch_hidden, ch_inner, here_proj)
                if depth < len(pools):
                    jobs.append(pools[depth].submit(self._sub, *args))
                else:
                    jobs.append(self._sub(*args))
            for j in jobs:
                child = j if isinstance(j, Node) or j is None else j.result()
                if child is None:
                    continue
                node.size += child.size
                node.files += child.files
                node.dirs += 1 + child.dirs
                for i in range(8):
                    node.cats[i] += child.cats[i]
                for i in range(4):
                    node.ages[i] += child.ages[i]
                self._dir_junk(child, path, sibling_names, inherited, hidden, here_proj)
                if inner >= 2:
                    continue  # totals only, as above
                node.children.append(child)
        return node

    def _sub(self, path: str, name: str, old: Node | None, dev: int, inherited: int | None, depth: int,
             pools: list[ThreadPoolExecutor], hidden: bool, inner: int, proj: bool) -> Node | None:
        if old is None:
            return self._walk(path, name, dev, inherited, depth, pools, hidden, inner, proj)
        return self._diff(old, dev, inherited, depth, pools, hidden, inner, proj)

    def _diff(self, old: Node, dev: int, inherited: int | None, depth: int, pools: list[ThreadPoolExecutor],
              hidden: bool, inner: int, proj: bool) -> Node | None:
        """Refresh one folder of the base scan. Same change time: keep its own files and refresh its
        subfolders. Another time: read it again (_walk with the old node)."""
        if self.cancel.is_set():
            return old
        try:
            st = os.lstat(old.path)
        except OSError:
            return None
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev and st.st_dev != dev):
            return None
        if st.st_mtime_ns != old.stamp or not old.stamp:
            return self._walk(old.path, old.name, dev, inherited, depth, pools, hidden, inner, proj, old=old)
        if inner >= 2:
            # A totals-only folder deep in dependencies: the base kept no subfolders, so keep it whole.
            with self._lock:
                self._kept_trees.add(old.path)
                self.count += old.files
                self.bytes += old.size
            return old
        node = Node(name=old.name, path=old.path, mtime=old.mtime, entries=old.entries, top=list(old.top),
                    unreadable=old.unreadable, stamp=old.stamp, marks=old.marks)
        # Its own files: the totals without the subfolders.
        # max(0, ...): forget() patches only some totals, so a difference can drift below 0.
        node.size = max(0, old.size - sum(c.size for c in old.children))
        node.files = max(0, old.files - sum(c.files for c in old.children))
        node.cats = [max(0, old.cats[i] - sum(c.cats[i] for c in old.children)) for i in range(8)]
        node.ages = [max(0, old.ages[i] - sum(c.ages[i] for c in old.children)) for i in range(4)]
        node.dirs = max(0, old.dirs - sum(1 + c.dirs for c in old.children))
        with self._lock:
            self._kept.add(old.path)
            self.count += node.files
            self.bytes += node.size
        here_proj = proj or bool(old.marks & protect.PROJECT_MARKERS) or old.name in protect.TOOL_NAMES
        jobs = []
        for c in old.children:
            child_cat = inherited
            if child_cat is None:
                child_cat = CODEDEPS if c.name in CODEDEP_DIRS else CACHES if c.name in CACHE_DIRS else None
            ch_hidden = hidden or c.name.startswith(".")
            ch_inner = inner + 1 if child_cat is not None else 0
            args = (c, dev, child_cat, depth + 1, pools, ch_hidden, ch_inner, here_proj)
            jobs.append(pools[depth].submit(self._diff, *args) if depth < len(pools) else self._diff(*args))
        for j in jobs:
            child = j if isinstance(j, Node) or j is None else j.result()
            if child is None:
                continue
            node.size += child.size
            node.files += child.files
            node.dirs += 1 + child.dirs
            for i in range(8):
                node.cats[i] += child.cats[i]
            for i in range(4):
                node.ages[i] += child.ages[i]
            self._dir_junk(child, old.path, old.marks, inherited, hidden, here_proj)
            node.children.append(child)
        return node

    def _from_base(self, path: str) -> bool:
        """True when the folder of `path` was not read again: its files came from the base scan."""
        parent = os.path.dirname(path)
        if parent in self._kept:
            return True
        cur = parent
        while True:
            if cur in self._kept_trees:
                return True
            nxt = os.path.dirname(cur)
            if nxt == cur or len(nxt) < len(self.root):
                return False
            cur = nxt

    def _file_seen(self, path: str, name: str, ext: str, size: int, mtime: int, atime: int, cat: int, parent: str, safe_zone: bool) -> None:
        with self._lock:
            self.count += 1
            self.bytes += size
            entry = (size, path, mtime, atime, cat)
            if len(self._largest) < TOP_FILES_GLOBAL:
                heapq.heappush(self._largest, entry)
            elif size > self._largest[0][0]:
                heapq.heapreplace(self._largest, entry)
        if not safe_zone:
            return
        if TEMP_NAME.search(name) and self.now - mtime > DAY:
            self._add_junk(JunkItem(path, size, "temp", "Temporary or partial file, not changed for a day", False, mtime))
        elif ext in INSTALLER_EXTS and os.path.basename(parent) == "Downloads" and self.now - mtime > 90 * DAY:
            days = int((self.now - mtime) / DAY)
            self._add_junk(JunkItem(path, size, "installer", f"Installer in Downloads, {days} days old", False, mtime))

    def _dir_junk(self, child: Node, parent: str, siblings: set[str], inherited: int | None, hidden: bool, proj: bool = False) -> None:
        n = child.name
        idle_days = int((self.now - child.mtime) / DAY)
        rel = child.path[len(self.home) + 1:].replace("\\", "/") if below(child.path, self.home) else None
        if rel in PACKAGE_CACHES:
            self._add_junk(JunkItem(child.path, child.size, "package-cache", "Package manager or thumbnail cache. Apps download it again when they need it", True, child.mtime))
            return
        # The other rules apply only to user folders: not inside hidden app folders, not inside dependencies.
        if hidden or inherited is not None:
            return
        try:
            project_mtime = int(os.stat(parent).st_mtime)
        except OSError:
            project_mtime = child.mtime
        idle = int((self.now - max(project_mtime, child.mtime)) / DAY)
        when = f"Project not changed for {idle} days" if idle > 1 else "Project changed recently"
        if n == "node_modules" and "package.json" in siblings:
            self._add_junk(JunkItem(child.path, child.size, "node_modules", f"npm installs it again. {when}", True, child.mtime, checked=idle > 30))
        elif n in (".venv", "venv") and os.path.exists(os.path.join(child.path, "pyvenv.cfg")):
            self._add_junk(JunkItem(child.path, child.size, "venv", f"uv or pip builds it again. {when}", True, child.mtime, checked=idle > 30))
        elif n == "__pycache__":
            self._add_junk(JunkItem(child.path, child.size, "pycache", "Python builds it again", True, child.mtime))
        elif n in TOOL_CACHES:
            self._add_junk(JunkItem(child.path, child.size, "build-cache", f"The tool builds it again. {when}", True, child.mtime, checked=idle > 30))
        elif n in ("build", "dist") and siblings & {"package.json", "setup.py", "pyproject.toml", "CMakeLists.txt", "Makefile"}:
            self._add_junk(JunkItem(child.path, child.size, "build", f"Build output. {when}", True, child.mtime, checked=idle > 30))
        elif n == "target" and "Cargo.toml" in siblings:
            self._add_junk(JunkItem(child.path, child.size, "build", f"Rust build output. {when}", True, child.mtime, checked=idle > 30))
        elif child.entries == 0 and not child.unreadable and not n.startswith(".") and idle_days > 30 and not proj \
                and n not in protect.TOOL_NAMES:
            self._add_junk(JunkItem(child.path, 0, "empty", "Empty folder", True, child.mtime))

    def _add_junk(self, j: JunkItem) -> None:
        with self._lock:
            self._junk.append(j)

    # ------------------------------------------------------------ run

    def run(self) -> ScanResult:
        t0 = time.time()
        st = os.stat(self.root)
        name = os.path.basename(self.root.rstrip("/")) or "/"
        base = self.base if self.base and self.base.root.path == self.root and self.base.dev == st.st_dev else None
        with ThreadPoolExecutor(8, thread_name_prefix="space1") as p1, ThreadPoolExecutor(12, thread_name_prefix="space2") as p2:
            if base:
                root = self._diff(base.root, st.st_dev, None, 0, [p1, p2], False, 0, False) or Node(name=name, path=self.root)
            else:
                root = self._walk(self.root, name, st.st_dev, None, 0, [p1, p2])
        largest, junk = self._largest, self._junk
        if base:
            # Files in folders that the refresh did not read: their entries come from the base.
            largest = largest + [x for x in base.largest if self._from_base(x[1])]
            junk = junk + [j for j in base.junk if not j.is_dir and self._from_base(j.path)]
        largest = heapq.nlargest(TOP_FILES_GLOBAL, largest)
        return ScanResult(root, t0, time.time(), root.files, largest, junk, st.st_dev, refreshed=bool(base))


# ---------------------------------------------------------------- store


# A saved scan older than this gets a refresh when Space asks for the status. Older than FULL_AFTER: a full walk.
REFRESH_AFTER = 10 * 60
FULL_AFTER = 7 * DAY
SAVE_VERSION = 1
SAVE_DELAY = 60.0


class SpaceStore:
    """The latest scan per root, plus the state of a running scan. Each scan is saved in <data>/space and
    loaded at start, so Space does not walk the disk again after each start."""

    def __init__(self) -> None:
        self.results: dict[str, ScanResult] = {}
        self.running: Scanner | None = None
        self.running_root: str | None = None
        self.error: str | None = None
        self.pending: tuple[str, bool] | None = None
        # Set when no load runs. _start_services clears it and loads the saved scans in the background.
        self.loaded = threading.Event()
        self.loaded.set()
        self._lock = threading.Lock()
        self._save_timer: threading.Timer | None = None

    def status(self) -> dict:
        r = self.running
        return {
            "scanning": r is not None,
            "refreshing": bool(r and r.base),
            "loading": not self.loaded.is_set(),
            "path": self.running_root,
            "files": r.count if r else 0,
            "bytes": r.bytes if r else 0,
            "roots": [
                {"path": k, "size": v.root.size, "files": v.files, "finished": int(v.finished * 1000),
                 "seconds": round(v.finished - v.started, 2), "refreshed": v.refreshed, "full": int((v.full or v.finished) * 1000)}
                for k, v in self.results.items()
            ],
            "error": self.error,
        }

    def start(self, root: str, full: bool = False) -> bool:
        """Scan `root`. With a saved scan of the same root and not `full`: a refresh, which reads only changed folders."""
        self.loaded.wait(60)  # a saved scan can still be loading: a refresh needs it
        root = os.path.abspath(root)
        with self._lock:
            if self.running is not None:
                if self.running.root == root and (self.running.base is None or not full):
                    return False
                # A new request wins: stop the running scan and start this one when it ends.
                self.pending = (root, full)
                self.running.cancel.set()
                return True
            sc = Scanner(root, base=None if full else self.results.get(root))
            self.running = sc
            self.running_root = sc.root
            self.error = None
        threading.Thread(target=self._run, args=(sc,), name="space-scan", daemon=True).start()
        return True

    def maybe_refresh(self) -> None:
        """Refresh old scans when Space is in use (it asks for the status). Not at start: the start is busy."""
        if not self.loaded.is_set() or self.running is not None:
            return
        now = time.time()
        for root, res in list(self.results.items()):
            if now - (res.full or res.finished) > FULL_AFTER:
                self.start(root, full=True)
                return
            if now - res.finished > REFRESH_AFTER:
                self.start(root)
                return

    # ------------------------------------------------------------ saved scans

    @staticmethod
    def _save_dir():
        from .. import config

        d = config.DATA_DIR / "space"
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    def _save(self, res: ScanResult) -> None:
        """Write one scan (pickle, zlib). Only this user can read the data folder (0700)."""
        import hashlib
        import pickle
        import zlib

        try:
            d = self._save_dir()
            name = hashlib.sha1(res.root.path.encode()).hexdigest()[:16] + ".scan"
            data = zlib.compress(pickle.dumps((SAVE_VERSION, res), protocol=pickle.HIGHEST_PROTOCOL), 1)
            tmp = d / (name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, d / name)
        except Exception:  # noqa: BLE001 - a scan that is not saved is only slower at the next start
            log.exception("could not save the space scan of %s", res.root.path)

    def save_soon(self) -> None:
        """Save the scans after a change that patched them (forget, refresh_paths), at most once a minute."""
        with self._lock:
            if self._save_timer is not None:
                return

            def run():
                with self._lock:
                    self._save_timer = None
                for res in list(self.results.values()):
                    self._save(res)

            self._save_timer = threading.Timer(SAVE_DELAY, run)
            self._save_timer.daemon = True
            self._save_timer.start()

    def load_saved(self) -> None:
        """Load the saved scans (at start, in the background)."""
        import pickle
        import zlib

        try:
            for f in self._save_dir().glob("*.scan"):
                try:
                    version, res = pickle.loads(zlib.decompress(f.read_bytes()))
                    if version != SAVE_VERSION or os.stat(res.root.path).st_dev != res.dev:
                        f.unlink(missing_ok=True)
                        continue
                except Exception:  # noqa: BLE001 - an old or broken file: drop it, the next scan writes it again
                    f.unlink(missing_ok=True)
                    continue
                with self._lock:
                    if res.root.path not in self.results:
                        self.results[res.root.path] = res
                log.info("space scan %s loaded: %d files, scanned %d min ago", res.root.path, res.files,
                         (time.time() - res.finished) // 60)
        finally:
            self.loaded.set()

    def _run(self, sc: Scanner) -> None:
        stop = threading.Event()

        def progress():
            while not stop.wait(0.5):
                events.publish("space_progress", path=sc.root, files=sc.count, bytes=sc.bytes)

        threading.Thread(target=progress, daemon=True).start()
        try:
            res = sc.run()
            if sc.cancel.is_set():
                return  # a partial scan is wrong, so Raven drops it
            res.full = sc.base.full or sc.base.finished if sc.base else res.finished
            # A new scan of a folder replaces scans of folders inside it.
            for k in list(self.results):
                if inside(k, sc.root):
                    del self.results[k]
            self.results[sc.root] = res
            from ..compress.candidates import forget_cache

            forget_cache()  # candidates come from the scan, so a new scan makes them stale
            log.info("space %s %s: %d files, %.1f GB in %.1f s", "refresh" if sc.base else "scan", sc.root, res.files,
                     res.root.size / 1e9, res.finished - res.started)
            _persist(res)
            self._save(res)
        except Exception as e:  # noqa: BLE001
            log.exception("space scan failed")
            self.error = str(e)
        finally:
            stop.set()
            self.running = None
            nxt, self.pending = self.pending, None
            if nxt:
                self.start(*nxt)
            else:
                events.publish("space_done", path=sc.root, error=self.error)

    def find(self, path: str) -> tuple[ScanResult, Node] | None:
        """The scan and node that hold `path`."""
        path = os.path.abspath(path)
        best = None
        for root, res in self.results.items():
            if inside(path, root):
                if best is None or len(root) > len(best[0]):
                    best = (root, res)
        if best is None:
            return None
        root, res = best
        node = res.root
        if path == root:
            return res, node
        for part in os.path.relpath(path, root).split(os.sep):
            nxt = next((c for c in node.children if c.name == part), None)
            if nxt is None:
                # Deep in a cache or dependency folder the scan kept only a total. Build this folder from the disk.
                return (res, _disk_node(path, node)) if not node.children and node.size and os.path.isdir(path) else None
            node = nxt
        return res, node

    def forget(self, items: list[tuple[str, int]]) -> None:
        """Take removed items out of the scan, so the views stay right without a rescan."""
        gone = {p for p, _ in items}
        for path, size in items:
            parent = self.find(os.path.dirname(path))
            if not parent:
                continue
            res, pnode = parent
            node = next((c for c in pnode.children if c.path == path), None)
            removed = node.size if node else size
            if node:
                pnode.children.remove(node)
            else:
                pnode.top = [t for t in pnode.top if t[1] != os.path.basename(path)]
                heapq.heapify(pnode.top)
            # Subtract the size from every folder up to the scan root.
            cur = os.path.dirname(path)
            while True:
                f = self.find(cur)
                if not f:
                    break
                f[1].size = max(0, f[1].size - removed)
                if cur == res.root.path:
                    break
                cur = os.path.dirname(cur)
        for res in self.results.values():
            res.junk = [j for j in res.junk if j.path not in gone and not any(below(j.path, g) for g in gone)]
            res.largest = [x for x in res.largest if x[1] not in gone]
        self.save_soon()

    def refresh_paths(self, paths: list[str]) -> None:
        """Bring single items in the scan up to date: new archives, restored folders, removed items."""
        for path in paths:
            path = os.path.abspath(path)
            old = self.find(path)
            if old and old[1].path == path:
                self.forget([(path, old[1].size)])
            elif not os.path.lexists(path):
                self.forget([(path, 0)])
            parent = self.find(os.path.dirname(path))
            if not parent or not os.path.lexists(path):
                continue
            res, pnode = parent
            pnode.top = [t for t in pnode.top if t[1] != os.path.basename(path)]
            heapq.heapify(pnode.top)
            if os.path.isdir(path) and not os.path.islink(path):
                sub = Scanner(path).run()
                added, child = sub.root.size, sub.root
                pnode.children.append(child)
                res.junk.extend(sub.junk)
                cats, ages = child.cats, child.ages
            else:
                st = os.lstat(path)
                added = disk_bytes(st)
                cat = _file_cat(ext_of(path), None)
                heapq.heappush(pnode.top, (added, os.path.basename(path), int(st.st_mtime), int(st.st_atime), cat))
                cats = [added if i == cat else 0 for i in range(8)]
                ages = [0] * 4
                ages[_age_bucket(time.time(), int(st.st_atime), int(st.st_mtime))] = added
            cur = os.path.dirname(path)
            while True:
                f = self.find(cur)
                if not f:
                    break
                f[1].size += added
                for i in range(8):
                    f[1].cats[i] += cats[i]
                for i in range(4):
                    f[1].ages[i] += ages[i]
                if cur == res.root.path:
                    break
                cur = os.path.dirname(cur)
        self.save_soon()

    def dir_size(self, path: str) -> int | None:
        f = self.find(path)
        return f[1].size if f else None


def _persist(res: ScanResult) -> None:
    """Store folder sizes (for the Files list) and one size snapshot per day."""
    rows = []
    stack = [res.root]
    while stack:
        n = stack.pop()
        rows.append((n.path, n.size, n.files, int(res.finished)))
        stack.extend(n.children)
    today = int(time.time() // DAY * DAY)
    with db.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS dir_sizes (path TEXT PRIMARY KEY, size INTEGER, files INTEGER, scanned INTEGER)")
        c.execute("DELETE FROM dir_sizes WHERE path = ? OR path LIKE ? ESCAPE '\\'", (res.root.path, _like(res.root.path)))
        c.executemany("INSERT OR REPLACE INTO dir_sizes VALUES (?,?,?,?)", rows)
        c.execute("DELETE FROM size_snapshots WHERE path=? AND ts >= ?", (res.root.path, today))
        c.execute("INSERT INTO size_snapshots VALUES (?,?,?)", (int(res.finished), res.root.path, res.root.size))


def _like(p: str) -> str:
    return like_below(p)


def dir_sizes(paths: list[str]) -> dict[str, int]:
    if not paths:
        return {}
    c = db.conn()
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='dir_sizes'").fetchone():
        return {}
    out = {}
    for i in range(0, len(paths), 500):
        part = paths[i:i + 500]
        for r in c.execute(f"SELECT path, size FROM dir_sizes WHERE path IN ({','.join('?' * len(part))})", part):
            out[r[0]] = r[1]
    return out


# ---------------------------------------------------------------- views


def _du(path: str, deadline: float) -> int | None:
    """Bytes on disk below `path`, or None when the deadline passes first."""
    total, stack = 0, [path]
    while stack:
        if time.monotonic() > deadline:
            return None
        try:
            with os.scandir(stack.pop()) as it:
                for de in it:
                    try:
                        st = de.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(de.path)
                    elif stat.S_ISREG(st.st_mode):
                        total += disk_bytes(st)
        except OSError:
            continue
    return total


def _disk_children(path: str, deadline: float) -> list[dict]:
    """The real items of a folder that the scan kept as a total only (deep inside a cache or dependency folder).
    Folder sizes come from a walk that stops at the deadline: a folder not measured in time is left out, and
    its bytes stay in "Small items"."""
    out: list[dict] = []
    try:
        entries = list(os.scandir(path))
    except OSError:
        return out
    for de in entries:
        try:
            st = de.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            size = _du(de.path, deadline)
            if size:
                out.append({"name": de.name, "path": de.path, "size": size, "is_dir": True, "cat": OTHER, "mtime": int(st.st_mtime) * 1000})
        elif stat.S_ISREG(st.st_mode):
            size = disk_bytes(st)
            out.append({"name": de.name, "path": de.path, "size": size, "is_dir": False, "cat": _file_cat(ext_of(de.name), None),
                        "mtime": int(st.st_mtime) * 1000, "atime": int(st.st_atime) * 1000})
    return out


def _disk_node(path: str, ancestor: Node) -> Node:
    """A node for a folder that the scan did not keep: its size from the disk, in the category of its ancestor."""
    n = Node(name=os.path.basename(path), path=path)
    n.size = _du(path, time.monotonic() + 2.0) or 0
    n.files = 1 if n.size else 0
    cat = max(range(8), key=lambda i: ancestor.cats[i])
    n.cats[cat] = n.size
    return n


def node_json(node: Node, depth: int, max_children: int = 40, deadline: float | None = None) -> dict:
    out = {
        "name": node.name, "path": node.path, "size": node.size, "files": node.files, "dirs": node.dirs,
        "is_dir": True, "cats": node.cats, "ages": node.ages, "mtime": node.mtime * 1000,
        "cat": max(range(8), key=lambda i: node.cats[i]) if node.size else OTHER,
    }
    if depth <= 0:
        return out
    items: list[dict] = []
    for ch in node.children:
        if ch.size > 0 or depth > 0:
            items.append(node_json(ch, depth - 1, max_children, deadline) if ch.size else {**node_json(ch, 0), "children": []})
    for size, name, mtime, atime, cat in node.top:
        items.append({"name": name, "path": os.path.join(node.path, name), "size": size, "is_dir": False,
                      "cat": cat, "mtime": mtime * 1000, "atime": atime * 1000})
    if not items and node.size and node.files:
        # The scan kept only the total here: read the real items from the disk, within 2 s for the whole view.
        if deadline is None:
            deadline = time.monotonic() + 2.0
        items = _disk_children(node.path, deadline)
        cat = max(range(8), key=lambda i: node.cats[i])
        for x in items:
            x["cat"] = cat  # the scan counted everything here in one category (a cache, for example)
    items.sort(key=lambda x: x["size"], reverse=True)
    shown = [x for x in items if x["size"] > 0][:max_children]
    rest = node.size - sum(x["size"] for x in shown)
    if rest > 0 and node.size and rest / node.size > 0.003:
        shown.append({"name": "Small items", "path": node.path + "/\0rest", "size": rest, "is_dir": False, "cat": OTHER, "rest": True})
    out["children"] = shown
    return out


store = SpaceStore()
