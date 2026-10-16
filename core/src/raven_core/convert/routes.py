"""Conversion API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..fs import ops
from . import registry
from .registry import ConvertError

router = APIRouter(prefix="/convert")


class PathsBody(BaseModel):
    paths: list[str]


class RunBody(BaseModel):
    paths: list[str]
    target: str
    combine: bool = False


class RecordBody(BaseModel):
    group: str | None = None
    paths: list[str]
    label: str = "Convert to PDF"


@router.post("/targets")
def targets(b: PathsBody):
    return {"targets": registry.targets(b.paths)}


@router.post("/run")
async def run(b: RunBody):
    try:
        res = await run_in_threadpool(registry.convert, b.paths, b.target, b.combine)
    except ConvertError as e:
        raise HTTPException(400, str(e)) from e
    return {**res, "journal": ops.peek()}


@router.post("/record")
def record(b: RecordBody):
    """The renderer printed these PDFs (MD or HTML to PDF). They join the batch's undo group."""
    gid = registry.record_printed(b.group, b.paths, b.label)
    return {"group": gid, "journal": ops.peek()}
