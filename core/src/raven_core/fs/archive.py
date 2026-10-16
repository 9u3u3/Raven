"""Archives as folders (sections 6.1 and 13.12): browse, open, extract and create.

A path such as `/home/u/notes.zip/sem5/os.pdf` is virtual: the part up to `notes.zip` is a real archive
file, the rest is a member inside it. Raven lists members without extracting them. To open or preview a
member, Raven extracts that one member to a cache folder and serves the copy. Raven never writes inside
an archive.

Formats: zip (Python), tar, tar.gz, tar.bz2, tar.xz (Python), tar.zst (zstd + Python), and 7z, rar,
iso and the rest through the `7z` tool.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from ..config import DATA_DIR
from .kinds import kind_of
from . import ops

CACHE = DATA_DIR / "archive-cache"
SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst", ".tar", ".zip", ".7z",
            ".rar", ".iso", ".jar", ".apk", ".cbz")
MAX_MEMBERS = 50_000
CACHE_MAX = 2 * 1024**3
# Limits for one extraction (zip bombs, PLAN3 phase 8). The free disk space is a limit too.
EXTRACT_MAX_BYTES = 100 * 1024**3
EXTRACT_MAX_FILES = 500_000
DISK_MARGIN = 1024**3
_lock = threading.Lock()
_index: dict[str, tuple[float, list[dict]]] = {}


class ArchiveError(ops.OpError):
    pass


def is_archive(name: str) -> bool:
    return name.lower().endswith(SUFFIXES)


def split(path: str) -> tuple[str, str] | None:
    """(archive file, member path) for a virtual path, or None for a normal path."""
    p = PurePosixPath(path)
    parts = p.parts
    for i in range(1, len(parts) + 1):
        head = str(PurePosixPath(*parts[:i]))
        if is_archive(parts[i - 1]) and os.path.isfile(head):
            return head, "/".join(parts[i:])
        if not os.path.isdir(head) and i < len(parts):
            return None
    return None


def _fmt(path: str) -> str:
    n = path.lower()
    if n.endswith((".zip", ".jar", ".apk", ".cbz")):
        return "zip"
    if n.endswith((".tar.zst", ".tzst")):
        return "tzst"
    if n.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    return "7z"


def _tool(name: str) -> None:
    if shutil.which(name) is None:
        pkg = {"7z": "7zip (or p7zip)", "zstd": "zstd"}.get(name, name)
        raise ArchiveError(f"{name} is not installed, so Raven cannot read this archive. Install {pkg}.", 424)


def _members(archive: str) -> list[dict]:
    """Every member: {name (posix path), is_dir, size, mtime ms}. Cached by the archive's mtime."""
    st = os.stat(archive)
    key = f"{archive}|{st.st_mtime_ns}|{st.st_size}"
    with _lock:
        hit = _index.get(archive)
        if hit and hit[0] == key:
            return hit[1]
    fmt = _fmt(archive)
    out: list[dict] = []
    from .network import readable

    src = readable(archive)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(src) as z:
                for i in z.infolist()[:MAX_MEMBERS]:
                    out.append({"name": i.filename.rstrip("/"), "is_dir": i.is_dir(), "size": i.file_size,
                                "mtime": int(time.mktime((*i.date_time, 0, 0, -1)) * 1000)})
        elif fmt in ("tar", "tzst"):
            for _, m in _tar_iter(src):
                out.append({"name": m.name.lstrip("./").rstrip("/"), "is_dir": m.isdir(), "size": m.size, "mtime": int(m.mtime * 1000)})
                if len(out) >= MAX_MEMBERS:
                    break
        else:
            _tool("7z")
            r = subprocess.run(["7z", "l", "-slt", "-ba", src], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise ArchiveError(f"7z cannot read this archive: {(r.stderr or r.stdout).strip().splitlines()[-1:]}", 422)
            cur: dict = {}
            for line in r.stdout.splitlines() + [""]:
                if not line.strip():
                    if cur.get("Path"):
                        out.append({"name": cur["Path"].replace("\\", "/"), "is_dir": cur.get("Folder") == "+" or "D" in cur.get("Attributes", "")[:1],
                                    "size": int(cur.get("Size") or 0), "mtime": _7z_time(cur.get("Modified", ""))})
                    cur = {}
                    continue
                k, _, v = line.partition(" = ")
                cur[k.strip()] = v.strip()
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
        raise ArchiveError(f"The archive is damaged or not complete: {e}", 422) from e
    # Some archives have no rows for their folders. Add them, so every member has a parent.
    names = {m["name"] for m in out}
    for m in list(out):
        parts = m["name"].split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d and d not in names:
                names.add(d)
                out.append({"name": d, "is_dir": True, "size": 0, "mtime": m["mtime"]})
    with _lock:
        _index[archive] = (key, out)
    return out


def _7z_time(s: str) -> int:
    try:
        return int(time.mktime(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S")) * 1000)
    except ValueError:
        return 0


def _tar_iter(archive: str):
    if _fmt(archive) == "tzst":
        _tool("zstd")
        proc = subprocess.Popen(["zstd", "-dc", archive], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as t:
                for m in t:
                    yield t, m
        finally:
            proc.kill()
    else:
        with tarfile.open(archive) as t:
            for m in t:
                yield t, m


def list_dir(path: str) -> dict:
    """A listing of a folder inside an archive, in the shape of listing.list_dir."""
    sp = split(path)
    if sp is None:
        raise FileNotFoundError(path)
    archive, inner = sp
    inner = inner.strip("/")
    members = _members(archive)
    if inner and not any(m["name"] == inner and m["is_dir"] for m in members):
        raise FileNotFoundError(path)
    prefix = inner + "/" if inner else ""
    entries = []
    counts: dict[str, int] = {}
    for m in members:
        if not m["name"].startswith(prefix):
            continue
        rest = m["name"][len(prefix):]
        if not rest:
            continue
        if "/" in rest:
            top = rest.split("/", 1)[0]
            counts[top] = counts.get(top, 0) + 1
            continue
        name = rest
        entries.append({
            "name": name, "path": f"{archive}/{prefix}{name}", "is_dir": m["is_dir"], "is_link": False,
            "is_hidden": name.startswith("."), "size": None if m["is_dir"] else m["size"], "mtime": m["mtime"],
            "ctime": m["mtime"], "atime": m["mtime"], "kind": kind_of(name, m["is_dir"]),
            "ext": "" if m["is_dir"] else (name.rsplit(".", 1)[1].lower() if "." in name.lstrip(".") else ""),
            "in_archive": True,
        })
    for e in entries:
        if e["is_dir"]:
            e["child_count"] = sum(1 for m in members if m["name"].startswith(f"{prefix}{e['name']}/") and "/" not in m["name"][len(prefix) + len(e["name"]) + 1:])
    parent = f"{archive}/{inner.rsplit('/', 1)[0]}" if "/" in inner else (archive if inner else os.path.dirname(archive))
    return {"path": path.rstrip("/"), "parent": parent if inner else os.path.dirname(archive), "entries": entries,
            "writable": False, "archive": archive}


def member_file(path: str) -> str:
    """Extract one member to the cache and return the real path of the copy."""
    sp = split(path)
    if sp is None:
        return path
    archive, inner = sp
    inner = inner.strip("/")
    st = os.stat(archive)
    key = hashlib.sha1(f"{archive}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
    out = CACHE / key / inner
    if out.exists():
        return str(out)
    hit = next((m for m in _members(archive) if m["name"] == inner and not m["is_dir"]), None)
    if hit is None:
        raise FileNotFoundError(path)
    if hit["size"] > CACHE_MAX:
        raise ArchiveError(f"This member is {hit['size'] / 1024**3:,.1f} GB. Extract the archive to open it.", 413)
    check_room(hit["size"], 1, CACHE.parent)
    if ".." in PurePosixPath(inner).parts:
        raise ArchiveError("Raven does not extract a member whose path leaves the archive", 400)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    fmt = _fmt(archive)
    from .network import readable

    local = readable(archive)
    if fmt == "zip":
        with zipfile.ZipFile(local) as z, z.open(inner) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    elif fmt in ("tar", "tzst"):
        for t, m in _tar_iter(local):
            if m.name.lstrip("./").rstrip("/") == inner:
                f = t.extractfile(m)
                if f is None:
                    raise ArchiveError("This member is not a regular file", 422)
                with open(tmp, "wb") as dst:
                    shutil.copyfileobj(f, dst)
                break
    else:
        _tool("7z")
        with open(tmp, "wb") as dst:
            r = subprocess.run(["7z", "e", "-so", archive, inner], stdout=dst, stderr=subprocess.PIPE, timeout=600)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise ArchiveError(f"7z cannot extract this member: {r.stderr.decode(errors='replace').strip()[-200:]}", 422)
    os.replace(tmp, out)
    _trim_cache()
    return str(out)


def _trim_cache() -> None:
    files = sorted((p for p in CACHE.rglob("*") if p.is_file()), key=lambda p: p.stat().st_atime)
    total = sum(p.stat().st_size for p in files)
    while files and total > CACHE_MAX:
        p = files.pop(0)
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def info(path: str) -> dict:
    sp = split(path)
    if sp is None:
        raise FileNotFoundError(path)
    archive, inner = sp
    inner = inner.strip("/")
    m = next((m for m in _members(archive) if m["name"] == inner), None)
    if m is None:
        raise FileNotFoundError(path)
    name = inner.rsplit("/", 1)[-1]
    from .kinds import mime_of

    return {"name": name, "path": path, "is_dir": m["is_dir"], "is_link": False, "is_hidden": name.startswith("."),
            "size": None if m["is_dir"] else m["size"], "mtime": m["mtime"], "ctime": m["mtime"], "atime": m["mtime"],
            "kind": kind_of(name, m["is_dir"]), "ext": name.rsplit(".", 1)[1].lower() if "." in name.lstrip(".") else "",
            "mode": "", "mode_octal": "", "owner": "", "group": "", "mime": "inode/directory" if m["is_dir"] else mime_of(name),
            "inode": 0, "target": None, "in_archive": archive}


# ---------------------------------------------------------------- extract and create


def check_room(total: int, files: int, dst: str | Path) -> None:
    """Refuse an extraction that writes more than the limits or more than the free disk space."""
    if files > EXTRACT_MAX_FILES:
        raise ArchiveError(f"The archive has {files:,} files. Raven extracts at most {EXTRACT_MAX_FILES:,}.", 413)
    if total > EXTRACT_MAX_BYTES:
        raise ArchiveError(f"The archive expands to {total / 1024**3:,.1f} GB. Raven extracts at most "
                           f"{EXTRACT_MAX_BYTES // 1024**3} GB.", 413)
    free = shutil.disk_usage(dst).free
    if total > free - DISK_MARGIN:
        raise ArchiveError(f"The archive expands to {total / 1024**3:,.1f} GB, but the disk has only "
                           f"{max(free, 0) / 1024**3:,.1f} GB free.", 507)


def totals_7z(archive: str) -> tuple[int, int]:
    """The expanded size and the file count from the 7z headers."""
    _tool("7z")
    r = subprocess.run(["7z", "l", "-slt", "-ba", archive], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ArchiveError(f"7z cannot read this archive: {(r.stderr or r.stdout).strip().splitlines()[-1:]}", 422)
    total = files = 0
    for line in r.stdout.splitlines():
        k, _, v = line.partition(" = ")
        if k == "Path":
            files += 1
        elif k == "Size" and v.strip().isdigit():
            total += int(v)
    return total, files


class _Budget:
    """Counts the members of a streamed tar while they arrive, because a stream has no index first."""

    def __init__(self, dst: Path):
        self.dst, self.total, self.files = dst, 0, 0

    def add(self, m: tarfile.TarInfo) -> None:
        self.files += 1
        self.total += max(m.size, 0)
        check_room(self.total, self.files, self.dst)


def _safe_members_zip(z: zipfile.ZipFile, dst: Path) -> None:
    for i in z.infolist():
        target = (dst / i.filename).resolve()
        if not str(target).startswith(str(dst.resolve()) + os.sep) and target != dst.resolve():
            raise ArchiveError(f"Raven does not extract '{i.filename}': its path leaves the target folder", 400)


def extract(archive: str, dst_dir: str | None = None) -> str:
    """Extract all members into a new folder next to the archive (one undo removes it)."""
    a = Path(archive)
    if not a.is_file():
        raise FileNotFoundError(archive)
    name = a.name
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
            break
    target = Path(ops.unique_path(str(Path(dst_dir or a.parent) / (name or "archive"))))
    tmp = target.with_name(f".raven-extract-{target.name}")
    tmp.mkdir()
    try:
        fmt = _fmt(archive)
        from .network import readable

        archive = readable(archive)  # a share: read a local copy (random access)
        if fmt == "zip":
            with zipfile.ZipFile(archive) as z:
                _safe_members_zip(z, tmp)
                infos = z.infolist()
                # zipfile stops each member at its declared size, so the header sum is a true limit.
                check_room(sum(i.file_size for i in infos), len(infos), tmp)
                z.extractall(tmp)
        elif fmt == "tar":
            with tarfile.open(archive) as t:
                ms = t.getmembers()
                check_room(sum(max(m.size, 0) for m in ms), len(ms), tmp)
                t.extractall(tmp, members=ms, filter="data")
        elif fmt == "tzst":
            _tool("zstd")
            proc = subprocess.Popen(["zstd", "-dc", archive], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            budget = _Budget(tmp)
            try:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as t:
                    for m in t:
                        budget.add(m)
                        t.extract(m, tmp, filter="data")
            finally:
                proc.kill()
                proc.wait()
        else:
            check_room(*totals_7z(archive), tmp)
            _tool("7z")
            r = subprocess.run(["7z", "x", "-y", f"-o{tmp}", archive], capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise ArchiveError(f"7z failed: {(r.stderr or r.stdout).strip().splitlines()[-1:]}", 422)
        # One folder at the top: it becomes the result, so "a.zip" with "a/" inside does not give "a/a".
        kids = list(tmp.iterdir())
        if len(kids) == 1 and kids[0].is_dir():
            target = Path(ops.unique_path(str(target.parent / kids[0].name)))
            os.rename(kids[0], target)
            tmp.rmdir()
        else:
            os.rename(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    g = ops.Group("manual", f"Extract {a.name}")
    g.add("create", str(a), str(target))
    g.commit()
    return str(target)


def create(paths: list[str], fmt: str, name: str | None = None) -> str:
    """A new archive of these items next to them. The originals stay (smart compression is compress/)."""
    if fmt not in ("zip", "7z", "tar.zst", "tar.gz"):
        raise ArchiveError("The format must be zip, 7z, tar.zst or tar.gz")
    if not paths:
        raise ArchiveError("Nothing to put in the archive")
    parent = Path(paths[0]).parent
    if any(Path(p).parent != parent for p in paths):
        raise ArchiveError("All items must be in one folder")
    base = name or (Path(paths[0]).name if len(paths) == 1 else parent.name or "archive")
    target = Path(ops.unique_path(str(parent / f"{base}.{fmt}")))
    rels = [Path(p).name for p in paths]
    from .network import scratch_for

    tmp = scratch_for(target) / f".raven-part-{os.getpid()}-{target.name}"
    try:
        if fmt == "zip":
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for rel in rels:
                    src = parent / rel
                    if src.is_dir():
                        for root, dirs, files in os.walk(src):
                            for d in dirs:
                                z.write(Path(root) / d, str((Path(root) / d).relative_to(parent)) + "/")
                            for f in files:
                                z.write(Path(root) / f, str((Path(root) / f).relative_to(parent)))
                    else:
                        z.write(src, rel)
        elif fmt == "tar.gz":
            with tarfile.open(tmp, "w:gz") as t:
                for rel in rels:
                    t.add(parent / rel, rel)
        elif fmt == "tar.zst":
            _tool("zstd")
            with open(tmp, "wb") as out:
                tar = subprocess.Popen(["tar", "-C", str(parent), "-cf", "-", "--", *rels], stdout=subprocess.PIPE)
                z = subprocess.run(["zstd", "-q", "-T0", "-19"], stdin=tar.stdout, stdout=out)
                tar.wait()
                if tar.returncode or z.returncode:
                    raise ArchiveError("tar or zstd failed", 500)
        else:
            _tool("7z")
            r = subprocess.run(["7z", "a", "-y", str(tmp.with_suffix(".7z").absolute()), *rels], cwd=parent, capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise ArchiveError(f"7z failed: {(r.stderr or r.stdout).strip().splitlines()[-1:]}", 500)
            tmp = tmp.with_suffix(".7z")
        if tmp.parent == target.parent:
            os.rename(tmp, target)
        else:
            ops.move_safe(str(tmp), str(target))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    g = ops.Group("manual", f"Create {target.name}")
    g.add("create", None, str(target))
    g.commit()
    return str(target)
