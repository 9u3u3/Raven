"""Agent API (section 9.10): plan a command, then run the plan the user reviewed."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..fs import ops
from ..router import cloud, local
from . import grammar, llm
from . import plan as core

router = APIRouter(prefix="/agent")
RUNS: dict[str, object] = {}  # paused runs that wait for the renderer to print PDFs


class PlanBody(BaseModel):
    command: str = Field(..., min_length=1, max_length=1000)
    cwd: str = ""
    selection: list[str] = []
    think_harder: bool = False


def _plan(b: PlanBody) -> dict:
    """Try the planners in order: the grammar, the local model, the cloud. A plan that fails
    to resolve, or that changes nothing, goes to the next planner."""
    t0 = time.perf_counter()
    cwd = os.path.abspath(b.cwd or str(Path.home()))
    selection = [os.path.abspath(p) for p in b.selection]
    from ..privacy.scope import is_private

    # A private folder (PLAN4 7.6): the agent does not plan in it or for files in it.
    if is_private(cwd) or any(is_private(p) for p in selection):
        raise core.PlanError("This folder is private. Raven's agent does not read or change it.")
    try:
        fast = None if b.think_harder else grammar.parse(b.command)
    except grammar.OrganizeRequest as e:
        raise core.PlanError("Organize plans open in the Organize screen (G then O).") from e

    def attempts():
        if fast is not None:
            yield fast, "fast path", "grammar", 0
            return
        provs = llm.providers()
        if not (b.think_harder or llm.verb_count(b.command) > 3):
            from ..index.indexer import indexer
            indexer.interactive.set()
            try:
                steps, err = llm.plan_local(b.command, cwd, selection)
            except local.LocalError as e:
                steps, err = [], str(e)
            finally:
                indexer.interactive.clear()
            yield steps, "T2", local.LLM, err
        if provs:
            try:
                steps, err, prov, sent = llm.plan_cloud(b.command, cwd, selection, provs)
            except cloud.CloudError as e:
                yield [], "T3", "cloud", f"The cloud planner failed: {e}"
                return
            yield steps, "T3", prov, (sent, err)

    last = "Raven did not understand this command. Try a form such as: convert all DOCX files in Sem5 to PDF"
    for steps, tier, planner, extra in attempts():
        sent = extra[0] if isinstance(extra, tuple) else 0
        err = extra[1] if isinstance(extra, tuple) else (extra if isinstance(extra, str) else "")
        if not steps:
            last = err or last
            continue
        ctx = core.Context(cwd, selection, b.command)
        try:
            p = core.resolve(steps, ctx, tier, planner, round((time.perf_counter() - t0) * 1000), sent)
        except core.PlanError as e:
            last = str(e)
            if tier == "fast path":
                raise
            continue
        out = p.public(str(Path.home()))
        if not out["steps"]:
            last = "The plan finds files but changes nothing"
            core.PLANS.pop(p.id, None)
            continue
        out["abstract"] = steps
        return out
    raise core.PlanError(last)


@router.post("/plan")
async def plan(b: PlanBody):
    try:
        return await run_in_threadpool(_plan, b)
    except core.PlanError as e:
        raise HTTPException(400, str(e)) from e


class RunBody(BaseModel):
    id: str
    keep: dict[int, list[str]] | None = None


def _advance(run_id: str, gen, value=None) -> dict:
    try:
        req = gen.send(value)
    except StopIteration as stop:
        RUNS.pop(run_id, None)
        return {"status": "done", **stop.value, "journal": ops.peek()}
    RUNS[run_id] = gen
    return {"status": "print", "run_id": run_id, "jobs": req["print"]}


@router.post("/run")
async def run(b: RunBody):
    gen = core.run(b.id, b.keep)
    try:
        return await run_in_threadpool(_advance, uuid.uuid4().hex[:10], gen)
    except (core.PlanError, ops.OpError) as e:
        raise HTTPException(400, str(e)) from e


class ResumeBody(BaseModel):
    run_id: str
    printed: list[str] = []


@router.post("/resume")
async def resume(b: ResumeBody):
    gen = RUNS.pop(b.run_id, None)
    if gen is None:
        raise HTTPException(404, "This run is gone")
    try:
        return await run_in_threadpool(_advance, b.run_id, gen, b.printed)
    except (core.PlanError, ops.OpError) as e:
        raise HTTPException(400, str(e)) from e
