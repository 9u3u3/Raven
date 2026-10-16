"""Copy queue (section 6.1): copy and move jobs with progress, pause and cancel.

One worker runs the jobs in order, as file managers do. Each job is one journal group, so one undo
reverses it. A cancelled job keeps the items that are complete (they are in the journal) and removes
the item that was in progress. The renderer gets `copy_job` events with byte progress.

Conflicts: before a job starts, the renderer asks `conflicts()` and shows the dialog. The job then gets
one choice per source path: replace, skip or keep_both.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .. import events
from . import ops
from .ops import _NOT_SUPPORTED, copystat_safe

log = logging.getLogger("raven.jobs")

BLOCK = 1024 * 1024
EVENT_EVERY = 0.15


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str  # copy | move
    srcs: list[str]
    dst: str
    choices: dict[str, str]
    default: str = "keep_both"
    status: str = "queued"  # queued, running, paused, done, cancelled, error
    bytes_total: int = 0
    bytes_done: int = 0
    files_total: int = 0
    files_done: int = 0
    current: str | None = None
    error: str | None = None
    results: list[str] = field(default_factory=list)
    skipped: int = 0
    started: float = 0.0
    ended: float = 0.0
    pause: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        speed = self.bytes_done / max(time.time() - self.started, 0.001) if self.started and self.status == "running" else 0
        left = (self.bytes_total - self.bytes_done) / speed if speed else None
        return {
            "id": self.id, "kind": self.kind, "dst": self.dst, "count": len(self.srcs), "srcs": self.srcs[:500], "status": self.status,
            "bytes_total": self.bytes_total, "bytes_done": self.bytes_done, "files_total": self.files_total,
            "files_done": self.files_done, "current": self.current, "error": self.error, "results": self.results,
            "skipped": self.skipped, "speed": round(speed), "eta": round(left) if left is not None else None,
        }


def _measure(paths: list[str]) -> tuple[int, int]:
    total = files = 0
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            for root, _dirs, names in os.walk(p):
                for n in names:
                    try:
                        total += os.lstat(os.path.join(root, n)).st_size
                    except OSError:
                        pass
                    files += 1
        else:
            try:
                total += os.lstat(p).st_size
            except OSError:
                pass
            files += 1
    return total, files


def conflicts(paths: list[str], dst: str) -> list[dict]:
    """The sources whose name already exists in `dst`, with both sides' size and date."""
    out = []
    for p in paths:
        target = os.path.join(dst, os.path.basename(p.rstrip("/")))
        if os.path.abspath(os.path.dirname(p)) == os.path.abspath(dst) or not os.path.lexists(target):
            continue
        a, b = os.lstat(p), os.lstat(target)
        out.append({
            "src": p, "target": target, "name": os.path.basename(target),
            "src_size": a.st_size, "src_mtime": int(a.st_mtime * 1000), "src_dir": os.path.isdir(p),
            "dst_size": b.st_size, "dst_mtime": int(b.st_mtime * 1000), "dst_dir": os.path.isdir(target),
        })
    return out


class Queue:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._order: deque[str] = deque()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="copy-queue", daemon=True).start()

    # ------------------------------------------------------------ API

    def add(self, kind: str, srcs: list[str], dst: str, choices: dict[str, str] | None = None, default: str = "keep_both") -> Job:
        if kind not in ("copy", "move"):
            raise ops.OpError("kind must be copy or move")
        if not os.path.isdir(dst):
            raise ops.OpError(f"Folder not found: {dst}", 404)
        from . import archive

        real = []
        for s in srcs:
            if not os.path.lexists(s) and archive.split(s):
                # A member of an archive: copy its extracted copy (a move would change the archive, so it copies).
                if archive.info(s)["is_dir"]:
                    raise ops.OpError("Raven copies single files out of an archive. To copy a folder, extract the archive first.")
                cached = archive.member_file(s)
                named = os.path.join(os.path.dirname(cached), ".out", os.path.basename(s))
                os.makedirs(os.path.dirname(named), exist_ok=True)
                shutil.copyfile(cached, named)
                real.append(named)
                kind = "copy"
            else:
                real.append(s)
        srcs = real
        for s in srcs:
            if not os.path.lexists(s):
                raise ops.OpError(f"Not found: {s}", 404)
            sp = Path(s)
            if sp.is_dir() and not sp.is_symlink():
                ops._check_inside(sp, Path(dst))
        job = Job(uuid.uuid4().hex[:10], kind, list(srcs), dst, dict(choices or {}), default)
        with self._lock:
            self.jobs[job.id] = job
            self._order.append(job.id)
            # Keep the last 30 finished jobs only.
            done = [j for j in self.jobs.values() if j.status in ("done", "cancelled", "error")]
            for old in sorted(done, key=lambda j: j.ended)[:-30]:
                self.jobs.pop(old.id, None)
        self._emit(job)
        self._wake.set()
        return job

    def control(self, job_id: str, action: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise ops.OpError("No such job", 404)
        if action == "pause" and job.status in ("running", "queued"):
            job.pause.set()
            job.status = "paused"
        elif action == "resume" and job.status == "paused":
            job.pause.clear()
            job.status = "running" if job.started else "queued"
            self._wake.set()
        elif action == "cancel" and job.status in ("running", "queued", "paused"):
            job.cancel.set()
            job.pause.clear()
            if not job.started:
                job.status = "cancelled"
                job.ended = time.time()
        self._emit(job)
        return job

    def list(self) -> list[dict]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.started or time.time(), reverse=True)]

    # ------------------------------------------------------------ worker

    def _emit(self, job: Job) -> None:
        events.publish("copy_job", job=job.public())

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    job = next((self.jobs[i] for i in self._order if i in self.jobs and self.jobs[i].status in ("queued", "paused")
                                and not self.jobs[i].pause.is_set()), None)
                    if job is not None:
                        self._order.remove(job.id)
                if job is None:
                    break
                if job.cancel.is_set():
                    continue
                self._do(job)

    def _do(self, job: Job) -> None:
        job.status = "running"
        job.started = time.time()
        job.bytes_total, job.files_total = _measure(job.srcs)
        self._emit(job)
        n = len(job.srcs)
        g = ops.Group("manual", f"{'Copy' if job.kind == 'copy' else 'Move'} {n} item{'s' if n != 1 else ''}")
        self._last = 0.0
        try:
            for s in job.srcs:
                self._item(job, g, s)
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except OSError as e:
            job.status = "error"
            job.error = f"{e.strerror or e}: {e.filename or job.current or ''}"
            if e.errno == errno.ENOSPC:
                job.error = f"The disk is full. Raven stopped at {os.path.basename(job.current or '')}."
        except ops.OpError as e:
            job.status = "error"
            job.error = str(e)
        finally:
            job.current = None
            job.ended = time.time()
            g.commit()
            self._emit(job)

    def _tick(self, job: Job, n: int) -> None:
        job.bytes_done += n
        while job.pause.is_set() and not job.cancel.is_set():
            if job.status != "paused":
                job.status = "paused"
                self._emit(job)
            time.sleep(0.1)
        if job.status == "paused":
            job.status = "running"
        if job.cancel.is_set():
            raise Cancelled()
        now = time.monotonic()
        if now - self._last > EVENT_EVERY:
            self._last = now
            self._emit(job)

    def _item(self, job: Job, g: ops.Group, s: str) -> None:
        sp = Path(s)
        if not os.path.lexists(sp):
            raise ops.OpError(f"Not found: {s}", 404)
        if job.kind == "move" and sp.parent.resolve() == Path(job.dst).resolve():
            return
        choice = job.choices.get(s, job.default)
        target = ops._resolve_conflict(str(Path(job.dst) / sp.name), choice, g)
        if target is None:
            job.skipped += 1
            size, files = _measure([s])
            job.bytes_done += size
            job.files_done += files
            return
        job.current = s
        if job.kind == "move":
            try:
                os.rename(sp, target)  # same file system: instant
                size, files = _measure([target])
                job.bytes_done += size
                job.files_done += files
                g.add("move", str(sp), target)
                job.results.append(target)
                return
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
        try:
            self._copy_any(job, sp, Path(target))
        except BaseException:
            # The item in progress is half copied: remove it. Complete items stay (they are in the journal).
            if os.path.lexists(target):
                shutil.rmtree(target, ignore_errors=True) if os.path.isdir(target) and not os.path.islink(target) else os.unlink(target)
            raise
        if job.kind == "move":
            # Cross-device move: the copy is complete, so the source can go.
            shutil.rmtree(sp) if sp.is_dir() and not sp.is_symlink() else sp.unlink()
            g.add("move", str(sp), target)
        else:
            g.add("create", str(sp), target)
        job.results.append(target)

    def _copy_any(self, job: Job, src: Path, dst: Path) -> None:
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
            job.files_done += 1
        elif src.is_dir():
            dst.mkdir()
            for child in sorted(os.listdir(src)):
                self._copy_any(job, src / child, dst / child)
            copystat_safe(src, dst, follow_symlinks=False)
        else:
            self._copy_file(job, src, dst)
            job.files_done += 1

    def _copy_file(self, job: Job, src: Path, dst: Path) -> None:
        """Copy into `<name>.raven.part`, then rename. A copy that stops halfway (a share goes away) never
        leaves a broken file with the real name, and Space finds a stale .part file as junk."""
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "File exists", str(dst))
        part = dst.with_name(dst.name + PART)
        try:
            with open(src, "rb") as fi, _open_new(part) as fo:
                while True:
                    buf = fi.read(BLOCK)
                    if not buf:
                        break
                    fo.write(buf)
                    self._tick(job, len(buf))
            copystat_safe(src, part)
            os.rename(part, dst)
        except BaseException:
            try:
                os.unlink(part)
            except OSError:
                pass
            raise


PART = ".raven.part"


def _open_new(path: Path):
    """Open a new file for writing, never over an old one. gvfs FTP has no O_EXCL: check, then open."""
    try:
        return open(path, "xb")
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno not in _NOT_SUPPORTED or os.path.lexists(path):
            raise
        return open(path, "wb")


queue = Queue()
