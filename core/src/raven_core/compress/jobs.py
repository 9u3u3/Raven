"""Compression jobs: safe execution, records and restore (section 9.8 steps 4 and 5).

Compress: plan -> free-space check -> create -> test -> extract to a temp folder and
compare BLAKE3 hashes -> manifest -> move the originals to the trash -> done.
If any step fails, Raven stops, keeps the originals and records the error.

The journal gets one group per job, so Ctrl+Z after a compression puts the originals
back and moves the archive to the trash.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import asdict

from .. import db, events
from ..fs import ops
from ..fs.trash import move_to_trash
from . import methods as m
from .methods import ARCHIVE_METHODS, FILE_METHODS, LOSSY_METHODS, LOSSY_TOOLS, CompressError, Item

log = logging.getLogger("raven.compress")


def _free(path: str) -> int | None:
    """Free bytes, or None when the file system does not say (gvfs FTP reports a size of 0)."""
    du = shutil.disk_usage(path)
    return None if du.total == 0 else du.free


def _archive_name(root: str, paths: list[str], label: str | None) -> str:
    if len(paths) == 1:
        return os.path.basename(paths[0].rstrip("/"))
    base = label or os.path.basename(root.rstrip("/")) or "archive"
    return "".join(ch if ch.isalnum() or ch in "-_ ." else "-" for ch in base).strip() + f"-{time.strftime('%Y%m%d')}"


def compress(root: str, paths: list[str], method: str, label: str | None = None, mode: str = "suggest",
             group: ops.Group | None = None, accept_loss: bool = False) -> dict:
    """Compress `paths` (absolute, all inside `root`) with one method. Return the job record.

    A lossy method needs accept_loss=True and mode "suggest": the user saw the warning in the review."""
    root = os.path.abspath(root)
    paths = [os.path.abspath(p) for p in paths]
    if not paths:
        raise CompressError("Nothing to compress")
    for p in paths:
        if not os.path.lexists(p):
            raise CompressError(f"Not found: {p}")
        if not (p == root or p.startswith(root.rstrip("/") + "/")):
            raise CompressError(f"{p} is not inside {root}")
    lossy = method in LOSSY_METHODS
    if method not in ARCHIVE_METHODS and method not in FILE_METHODS and not lossy:
        raise CompressError(f"Unknown method {method}")
    if lossy and (not accept_loss or mode != "suggest"):
        raise CompressError(f"{method} loses data. Raven runs it only after you accept the loss in the review")
    tool = {"7z": "7z", "tar.zst": "zstd", "zst": "zstd", "jxl": "cjxl", **LOSSY_TOOLS}.get(method)
    if tool and not m.tool_ok(tool):
        raise CompressError(f"{tool} is not installed, so Raven cannot use {method}")

    now = int(time.time())
    with db.tx() as c:
        job_id = c.execute(
            "INSERT INTO compress_jobs(mode, method, root, label, files_count, status, created, reversible) VALUES (?,?,?,?,?,?,?,?)",
            (mode, method, root, label, 0, "planned", now, 0 if lossy else 1),
        ).lastrowid
    events.publish("compress_changed", job=job_id, status="planned")
    outputs: list[str] = []
    try:
        root_mtime = os.stat(root).st_mtime_ns
        items = m.collect(root, paths)
        files = [i for i in items if i.kind == "file"]
        size_before = sum(i.size for i in files)
        # The new file needs room while the originals still exist.
        free = _free(root)
        if free is not None and free < size_before * 1.05 + 50 * 1024 * 1024:
            raise CompressError("Not enough free space for the temporary archive")
        _set(job_id, status="running", files_count=len(files), size_before=size_before)

        if method in ARCHIVE_METHODS:
            dst = ops.unique_path(os.path.join(root, _archive_name(root, paths, label) + ARCHIVE_METHODS[method]))
            rels = [os.path.relpath(p, root) for p in paths]
            manifest = m.manifest_json(method, root, items, root_mtime)
            m.create_archive(method, root, rels, dst, manifest)
            outputs.append(dst)
            _set(job_id, status="verifying")
            m.test_archive(method, dst)
            with tempfile.TemporaryDirectory(prefix=".raven-verify-", dir=root) as tmp:
                m.extract_archive(method, dst, tmp)
                m.verify_tree(tmp, items)
                if not os.path.isfile(os.path.join(tmp, m.MANIFEST)):
                    raise CompressError("Verify failed: the manifest is missing from the archive")
            out_by_item = {}
        elif lossy:
            if any(i.kind != "file" for i in items):
                raise CompressError(f"{method} works on single files only")
            out_by_item = {}
            _set(job_id, status="verifying")
            for it in items:
                src = os.path.join(root, it.rel)
                stem = it.rel[: -len(os.path.splitext(it.rel)[1])]
                dst = ops.unique_path(os.path.join(root, stem + LOSSY_METHODS[method]))
                m.create_lossy(method, src, dst)
                outputs.append(dst)
                out_by_item[it.rel] = os.path.relpath(dst, root)
                m.check_lossy(method, src, dst)
                # The new file keeps the time of the original, so "sort by date" stays the same.
                os.utime(dst, ns=(it.atime_ns, it.mtime_ns))
            manifest = json.dumps({**json.loads(m.manifest_json(method, root, items, root_mtime)), "outputs": out_by_item}, indent=1)
        else:
            if any(i.kind != "file" for i in items):
                raise CompressError(f"{method} works on single files only")
            out_by_item = {}
            _set(job_id, status="verifying")
            for it in items:
                src = os.path.join(root, it.rel)
                stem = it.rel[: -len(os.path.splitext(it.rel)[1])] if method == "jxl" else it.rel
                dst = ops.unique_path(os.path.join(root, stem + FILE_METHODS[method]))
                m.create_file(method, src, dst)
                outputs.append(dst)
                out_by_item[it.rel] = os.path.relpath(dst, root)
                with tempfile.TemporaryDirectory(prefix=".raven-verify-", dir=os.path.dirname(dst)) as tmp:
                    # djxl picks the output format from the extension.
                    back = os.path.join(tmp, "check.jpg" if method == "jxl" else "check")
                    m.extract_file(method, dst, back)
                    if m.full_hash(back) != it.blake3:
                        raise CompressError(f"Verify failed: {it.rel} does not decode to the original bytes")
            manifest = json.dumps({**json.loads(m.manifest_json(method, root, items, root_mtime)), "outputs": out_by_item}, indent=1)

        size_after = sum(os.path.getsize(o) for o in outputs)
        # The identity of each new file, so a later file with the same name does not count as this job's.
        man = json.loads(manifest)
        man["output_ids"] = {o: [os.stat(o).st_size, os.stat(o).st_mtime_ns] for o in outputs}
        manifest = json.dumps(man, indent=1)
        if size_after >= size_before:
            raise CompressError("The compressed file is not smaller, so Raven keeps the originals")

        # Originals go to the trash, never a real delete (section 15). One group: one undo.
        g = group or ops.Group("compress", f"Compress {len(files)} file{'s' if len(files) != 1 else ''} to {method}")
        for o in outputs:
            g.add("create", None, o)
        for p in paths:
            trashed, info = move_to_trash(p)
            g.add("trash", p, trashed, {"info": info})
        if group is None:
            g.commit()

        with db.tx() as c:
            c.executemany(
                "INSERT INTO compress_items(job_id, original_path, size, blake3, mtime, atime, mode_bits, kind) VALUES (?,?,?,?,?,?,?,?)",
                [(job_id, os.path.join(root, i.rel), i.size, i.blake3, i.mtime_ns, i.atime_ns, i.mode, i.kind) for i in items],
            )
        _set(job_id, status="done", size_after=size_after, archive_path=outputs[0], op_group=g.id, manifest=manifest)
        log.info("job %d: %s %d files %.1f MB -> %.1f MB", job_id, method, len(files), size_before / 1e6, size_after / 1e6)
    except Exception as e:
        # Stop, keep the originals, record the error.
        for o in outputs:
            try:
                os.unlink(o)
            except OSError:
                pass
        _set(job_id, status="failed", error=str(e)[:500])
        events.publish("compress_changed", job=job_id, status="failed")
        if isinstance(e, CompressError):
            raise
        raise CompressError(str(e)) from e
    events.publish("compress_changed", job=job_id, status="done")
    return get_job(job_id)


def _set(job_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as c:
        c.execute(f"UPDATE compress_jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def _items(job: dict) -> tuple[list[Item], dict]:
    man = json.loads(job["manifest"] or "{}")
    return [Item(**i) for i in man.get("items", [])], man.get("outputs", {})


def is_lossy(job: dict) -> bool:
    return job["method"] in LOSSY_METHODS


def outputs_of(job: dict) -> list[str]:
    _, outs = _items(job)
    if job["method"] in ARCHIVE_METHODS:
        return [job["archive_path"]] if job["archive_path"] else []
    return [os.path.join(job["root"], o) for o in outs.values()]


def _top_level(items: list[Item]) -> list[str]:
    rels = [i.rel for i in items]
    return [r for r in rels if not any(r.startswith(o + "/") for o in rels if o != r)]


def _group_live(group_id: str | None) -> bool:
    """True when the journal group exists and is not undone."""
    if not group_id:
        return False
    row = db.conn().execute("SELECT MIN(undone) FROM operations WHERE group_id=?", (group_id,)).fetchone()
    return row is not None and row[0] is not None and row[0] in (0, 3)


def _same_file(path: str, ident: list | None) -> bool:
    if not os.path.exists(path):
        return False
    if not ident:
        return True  # jobs from before identities were stored
    st = os.stat(path)
    return st.st_size == ident[0] and st.st_mtime_ns == ident[1]


def effective_status(job: dict) -> str:
    """The status from the disk and the journal. Undo and redo can change it."""
    if job["status"] not in ("done", "restored"):
        return job["status"]
    # A restore that is not undone wins: the archive is gone, even if a new file has its name now.
    if job["status"] == "restored" and _group_live(job.get("restore_group")):
        return "restored"
    if job["op_group"] and not _group_live(job["op_group"]) and job["status"] == "done":
        return "undone"
    items, _ = _items(job)
    ids = json.loads(job["manifest"] or "{}").get("output_ids", {})
    outs = outputs_of(job)
    outs_ok = bool(outs) and all(_same_file(o, ids.get(o)) for o in outs)
    tops = _top_level(items)
    origs_ok = bool(tops) and all(os.path.lexists(os.path.join(job["root"], t)) for t in tops)
    if outs_ok and not origs_ok:
        return "done"
    if origs_ok and not outs_ok:
        return "restored" if job["restored"] else "undone"
    if outs_ok and origs_ok:
        return "restored"
    return "missing"


def get_job(job_id: int) -> dict:
    row = db.conn().execute("SELECT * FROM compress_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise CompressError(f"No compression job {job_id}")
    return _public(dict(row))


def _public(job: dict) -> dict:
    st = effective_status(job)
    return {
        "id": job["id"], "mode": job["mode"], "method": job["method"], "root": job["root"], "label": job["label"],
        "archive": job["archive_path"], "outputs": outputs_of(job) if job["manifest"] else [],
        "files": job["files_count"], "size_before": job["size_before"] or 0, "size_after": job["size_after"] or 0,
        "gain": (job["size_before"] or 0) - (job["size_after"] or 0), "reversible": bool(job["reversible"]),
        "status": st, "error": job["error"], "created": (job["created"] or 0) * 1000,
        "restored": (job["restored"] or 0) * 1000 or None,
    }


def list_jobs(limit: int = 200) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM compress_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_public(dict(r)) for r in rows]


def summary() -> dict:
    """Ghost space: the extra space all archives need if you restore them (section 9.6)."""
    # A lossy job has no ghost: Raven cannot make the original from its output.
    jobs = [j for j in list_jobs(1000) if j["status"] == "done" and j["reversible"]]
    ghosts = {}
    for j in jobs:
        outs = j["outputs"]
        for o in outs:
            # One output: the whole job. Many outputs (photos): an even share each.
            ghosts[o] = j["size_before"] // max(1, len(outs))
    return {"ghost": sum(j["gain"] for j in jobs), "jobs": len(jobs), "outputs": ghosts}


def restore(job_id: int) -> dict:
    job_row = db.conn().execute("SELECT * FROM compress_jobs WHERE id=?", (job_id,)).fetchone()
    if not job_row:
        raise CompressError(f"No compression job {job_id}")
    job = dict(job_row)
    if is_lossy(job):
        raise CompressError("A lossy job keeps no exact original. Undo brings the originals back from the trash while the trash still holds them")
    if effective_status(job) != "done":
        raise CompressError("This job has no archive to restore")
    items, outs = _items(job)
    root = job["root"]
    need = (job["size_before"] or 0) - (job["size_after"] or 0)
    free = _free(root)
    if free is not None and free < need + 50 * 1024 * 1024:
        raise CompressError(f"Restore needs {need / 1e9:.1f} GB more free space. You have {free / 1e9:.1f} GB free")

    restored: list[str] = []
    renamed: list[str] = []
    g = ops.Group("compress", f"Restore {job['files_count']} file{'s' if job['files_count'] != 1 else ''} from {job['method']}")
    with tempfile.TemporaryDirectory(prefix=".raven-restore-", dir=root) as tmp:
        if job["method"] in ARCHIVE_METHODS:
            m.extract_archive(job["method"], job["archive_path"], tmp)
            m.verify_tree(tmp, items)
            for top in _top_level(items):
                dst = os.path.join(root, top)
                if os.path.lexists(dst):
                    dst = ops.unique_path(dst)
                    renamed.append(dst)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                ops.move_safe(os.path.join(tmp, top), dst)
                # Times and permissions, deepest first, so folder times stay right.
                base_rel = top
                for it in sorted((i for i in items if i.rel == base_rel or i.rel.startswith(base_rel + "/")), key=lambda i: -i.rel.count("/")):
                    m.apply_meta(os.path.join(dst, os.path.relpath(it.rel, base_rel)) if it.rel != base_rel else dst, it)
                g.add("create", None, dst)
                restored.append(dst)
        else:
            for it in items:
                out = os.path.join(root, outs[it.rel])
                back = os.path.join(tmp, "f.jpg" if job["method"] == "jxl" else "f")
                m.extract_file(job["method"], out, back)
                if m.full_hash(back) != it.blake3:
                    raise CompressError(f"Restore failed: {it.rel} does not match the original hash")
                dst = os.path.join(root, it.rel)
                if os.path.lexists(dst):
                    dst = ops.unique_path(dst)
                    renamed.append(dst)
                ops.move_safe(back, dst)
                m.apply_meta(dst, it)
                g.add("create", None, dst)
                restored.append(dst)
    # The archive is no longer needed. It goes to the trash, so undo can bring it back.
    for o in outputs_of(job):
        trashed, info = move_to_trash(o)
        g.add("trash", o, trashed, {"info": info})
    g.commit()
    root_mtime = json.loads(job["manifest"] or "{}").get("root_mtime_ns")
    if root_mtime:
        try:
            os.utime(root, ns=(os.stat(root).st_atime_ns, root_mtime))
        except OSError:
            pass
    _set(job_id, status="restored", restored=int(time.time()), restore_group=g.id)
    events.publish("compress_changed", job=job_id, status="restored")
    return {**get_job(job_id), "restored_paths": restored, "renamed": renamed}


def item_rows(job_id: int) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM compress_items WHERE job_id=?", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def as_dict(it: Item) -> dict:
    return asdict(it)
