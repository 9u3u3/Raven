"""Search by appearance: images, PDF pages and video moments (media/visual.py)."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..config import THUMB_DIR
from ..search.filters import parse
from . import visual

router = APIRouter()


@router.get("/visual/search")
async def visual_search(q: str = Query(..., max_length=500), limit: int = 24):
    f = parse(q)
    if not f.text:
        return {"results": [], "ms": 0}
    return await run_in_threadpool(visual.search, f.text, limit, f)


@router.get("/visual/similar")
async def visual_similar(path: str, limit: int = 24):
    return await run_in_threadpool(visual.similar, path, limit)


@router.get("/visual/moments")
def visual_moments(path: str):
    return {"moments": visual.moments(path)}


@router.get("/visual/status")
def visual_status():
    return visual.job.status()


@router.get("/transcribe/status")
def transcribe_status():
    from . import transcribe

    return transcribe.job.status()


@router.get("/transcribe/text")
def transcribe_text(path: str):
    """The timed transcript of one audio or video file, as Raven indexed it."""
    from .. import db

    rows = db.conn().execute(
        "SELECT c.at, c.text FROM chunks c JOIN files f ON f.id=c.file_id WHERE f.path=? AND c.at IS NOT NULL ORDER BY c.at",
        (path,),
    ).fetchall()
    return {"parts": [{"at": r[0], "text": r[1]} for r in rows]}


@router.get("/visual/thumb")
def visual_thumb(p: str):
    """A stored keyframe or page image. Only files inside the thumbnail folder."""
    real = os.path.realpath(p)
    if not real.startswith(str(THUMB_DIR.resolve()) + os.sep) or not os.path.isfile(real):
        raise HTTPException(404, "No such thumbnail")
    return FileResponse(real, headers={"Cache-Control": "max-age=86400"})
