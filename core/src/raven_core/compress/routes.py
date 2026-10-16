"""Compression API: candidates, run, records, restore, ghost space."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import db
from ..fs import ops
from ..storage.scan import store
from . import candidates as cand
from . import jobs
from .methods import CompressError

router = APIRouter(prefix="/compress")


class RunBody(BaseModel):
    root: str
    paths: list[str]
    method: str
    label: str | None = None
    accept_loss: bool = False


def _space_after_change(paths: list[str]) -> None:
    store.refresh_paths(paths)
    cand.forget_cache()


@router.post("/run")
async def run(b: RunBody):
    try:
        job = await run_in_threadpool(jobs.compress, b.root, b.paths, b.method, b.label, "suggest", None, b.accept_loss)
    except CompressError as e:
        raise HTTPException(400, str(e)) from e
    _space_after_change(b.paths + job["outputs"])
    return {"job": job, "journal": ops.peek()}


@router.post("/restore/{job_id}")
async def restore(job_id: int):
    try:
        res = await run_in_threadpool(jobs.restore, job_id)
    except CompressError as e:
        raise HTTPException(400, str(e)) from e
    _space_after_change(res["restored_paths"] + res["outputs"])
    return {"job": res, "journal": ops.peek()}


@router.get("/jobs")
def list_jobs():
    return {"jobs": jobs.list_jobs()}


@router.get("/jobs/{job_id}")
def job(job_id: int):
    try:
        return {"job": jobs.get_job(job_id), "items": jobs.item_rows(job_id)}
    except CompressError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/summary")
def summary():
    return jobs.summary()


@router.get("/candidates")
def get_candidates(path: str, cold_days: int = 90):
    return {"rows": cand.candidates(path, cold_days)}


# ---------------------------------------------------------------- Auto mode (section 9.8 step 3)


@router.get("/auto")
def auto_state():
    from . import auto

    return {"rules": auto.rules(), "log": db.get_setting("auto_log", []) or [], "ac": auto.on_ac(), "idle": auto.idle(),
            "buffer_days": auto.BUFFER_DAYS, "methods": list(auto.METHODS)}


class AutoRulesBody(BaseModel):
    rules: list[dict]


@router.post("/auto/rules")
def auto_rules(b: AutoRulesBody):
    from . import auto

    try:
        return {"rules": auto.set_rules(b.rules)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class AutoRunBody(BaseModel):
    id: str
    skip_wait: bool = False


@router.post("/auto/run")
async def auto_run(b: AutoRunBody):
    from . import auto

    rule = next((r for r in auto.rules() if r["id"] == b.id), None)
    if rule is None:
        raise HTTPException(404, "No such rule")
    entry = await run_in_threadpool(auto.run_rule, rule, b.skip_wait)
    return {"entry": entry, "journal": ops.peek()}
