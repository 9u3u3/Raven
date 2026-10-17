"""Space API: scans, the size tree, largest files, duplicates and suggestions."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import scan
from .dupes import job as dupe_job
from .scan import AGES, CATEGORIES, node_json, store
from .suggest import suggestions

router = APIRouter(prefix="/space")


class PathBody(BaseModel):
    path: str
    min_size: int = 1024


@router.get("/meta")
def meta():
    return {"categories": CATEGORIES, "ages": AGES}


@router.post("/scan")
def start_scan(b: PathBody):
    p = os.path.abspath(os.path.expanduser(b.path))
    if not os.path.isdir(p):
        raise HTTPException(400, f"Not a folder: {b.path}")
    started = store.start(p)
    return {"started": started, **store.status()}


@router.get("/status")
def status():
    return {**store.status(), "dupes": dupe_job.status()}


@router.get("/tree")
def tree(path: str, depth: int = 2):
    found = store.find(path)
    if not found:
        raise HTTPException(404, "No scan covers this folder yet")
    res, node = found
    out = node_json(node, max(0, min(depth, 4)))
    from ..compress.jobs import summary

    ghosts = summary()["outputs"]
    if ghosts:
        _mark_ghosts(out, ghosts)
    out["scan_root"] = res.root.path
    out["scanned"] = int(res.finished * 1000)
    return out


def _mark_ghosts(n: dict, ghosts: dict[str, int]) -> int:
    """Set `ghost` (the size after a restore) on archive cells and on the folders above them."""
    extra = 0
    for ch in n.get("children", []):
        if ch["path"] in ghosts:
            ch["ghost"] = ghosts[ch["path"]]
            extra += ch["ghost"] - ch["size"]
        elif ch.get("is_dir"):
            extra += _mark_ghosts(ch, ghosts)
    if extra > 0 and n.get("is_dir"):
        n["ghost"] = n["size"] + extra
    return extra


@router.get("/growth")
def growth(path: str, days: int = 90):
    """Growth line (section 9.6): one size snapshot per day for a scan root, oldest first."""
    from .. import db

    p = os.path.abspath(os.path.expanduser(path))
    since = int(__import__("time").time()) - max(1, min(days, 3650)) * 86400
    rows = db.conn().execute("SELECT ts, size FROM size_snapshots WHERE path=? AND ts >= ? ORDER BY ts", (p, since)).fetchall()
    return {"path": p, "points": [{"ts": r[0] * 1000, "size": r[1]} for r in rows]}


@router.get("/largest")
def largest(path: str, limit: int = 60):
    found = store.find(path)
    if not found:
        raise HTTPException(404, "No scan covers this folder yet")
    res, _ = found
    top = os.path.abspath(path)
    root = os.path.join(top, "")  # with the separator at the end: "/x/" or "C:\\x\\"
    out = []
    for size, p, mtime, atime, cat in res.largest:
        if p.startswith(root) or top == os.path.dirname(top):
            out.append({"path": p, "name": os.path.basename(p), "size": size, "mtime": mtime * 1000, "atime": atime * 1000, "cat": cat})
            if len(out) >= limit:
                break
    return {"items": out}


@router.post("/duplicates")
def start_dupes(b: PathBody):
    p = os.path.abspath(os.path.expanduser(b.path))
    if not os.path.isdir(p):
        raise HTTPException(400, f"Not a folder: {b.path}")
    return {"started": dupe_job.start(p, max(1, b.min_size)), **dupe_job.status()}


@router.get("/duplicates")
def get_dupes(path: str | None = None, limit: int = 300):
    groups = dupe_job.groups
    if path:
        from ..platform import below

        root = os.path.abspath(path)
        groups = [g for g in groups if any(below(f["path"], root) for f in g["files"])]
    return {**dupe_job.status(), "groups": groups[:limit]}


class ForgetBody(BaseModel):
    items: list[tuple[str, int]]


@router.post("/forget")
def forget(b: ForgetBody):
    """The renderer calls this after a cleanup ran, with (path, size) of each removed item."""
    store.forget(b.items)
    dupe_job.forget({p for p, _ in b.items})
    return {"ok": True}


@router.get("/suggestions")
def get_suggestions(path: str):
    return {"rows": suggestions(path)}


def dir_sizes(paths: list[str]) -> dict[str, int]:
    """Folder sizes for the Files list: the live scan first, then the stored sizes."""
    out = scan.dir_sizes(paths)
    for p in paths:
        s = store.dir_size(p)
        if s is not None:
            out[p] = s
    return out


# ---------------------------------------------------------------- reclaim (PLAN2 P2-4)


@router.get("/reclaim")
async def reclaim_scan(path: str | None = None):
    from . import reclaim

    return await run_in_threadpool(reclaim.scan, path)


class ReclaimPlanBody(BaseModel):
    target: int
    id: str | None = None


@router.post("/reclaim/plan")
def reclaim_plan(b: ReclaimPlanBody):
    from . import reclaim

    try:
        return reclaim.plan(b.target, b.id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


class ReclaimRunBody(BaseModel):
    id: str
    rows: dict[str, list[str]]


@router.post("/reclaim/run")
async def reclaim_run(b: ReclaimRunBody):
    from . import reclaim

    try:
        return await run_in_threadpool(reclaim.run, b.id, b.rows)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


class OffloadBody(BaseModel):
    paths: list[str]
    target: str


@router.post("/offload")
async def offload(b: OffloadBody):
    from . import reclaim

    try:
        return await run_in_threadpool(reclaim.offload, b.paths, b.target)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/offloaded")
def offloaded():
    from . import reclaim

    return {"items": reclaim.offloaded()}


@router.get("/forecast")
def forecast(path: str | None = None):
    from . import reclaim

    return reclaim.forecast(path or reclaim.HOME)
