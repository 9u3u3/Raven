"""Router and privacy API: run a task, the ledger, test redaction, sensitive files."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import db
from ..privacy import gate
from ..privacy.model import model as layer2
from . import cloud, local
from . import router as core

router = APIRouter()


class RunBody(BaseModel):
    task: str
    path: str
    think_harder: bool = False
    consent: bool = False


@router.post("/router/run")
async def run(b: RunBody):
    try:
        return await run_in_threadpool(core.run, b.task, b.path, b.think_harder, b.consent)
    except (ValueError, local.LocalError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/privacy/status")
def status():
    return {
        "ai_mode": core.ai_mode(),
        "providers": cloud.configured(),
        "groq_model": (db.get_setting("groq_model") or None),
        "claude_model": db.get_setting("claude_model") or "sonnet",
        "claude_account": db.get_setting("claude_config_dir") or "",
        "anthropic_model": db.get_setting("anthropic_model") or cloud.ANTHROPIC_DEFAULT,
        "cloud_provider": db.get_setting("cloud_provider") or "auto",
        "local_model": local.LLM,
        "local_ready": local.available(),
        "layer2": {"available": layer2.available(), "error": layer2.error, "load_ms": layer2.load_ms},
        "today": gate.ledger_today(),
        # Local only never checks: it makes no outbound connection at all.
        "online": None if core.ai_mode() == "local" else cloud.online(),
    }


@router.get("/privacy/ledger")
def ledger(limit: int = 200):
    return {"rows": gate.ledger_list(limit), "today": gate.ledger_today()}


@router.get("/privacy/ledger/export")
def ledger_export():
    return {"json": gate.ledger_export()}


class TextBody(BaseModel):
    text: str
    layer2: bool = True


@router.post("/privacy/redact")
async def redact(b: TextBody):
    r = await run_in_threadpool(gate.redact_text, b.text, b.layer2)
    return r.public()


@router.get("/privacy/sensitive")
def sensitive(limit: int = 300):
    c = db.conn()
    rows = c.execute(
        "SELECT f.id, f.path, f.name, f.size, f.mtime, f.pii_level FROM files f WHERE f.pii_level > 0 "
        "ORDER BY f.pii_level DESC, f.mtime DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        types = Counter(
            t[0] for t in c.execute(
                "SELECT DISTINCT s.type, s.start FROM pii_spans s JOIN chunks ch ON ch.id = s.chunk_id WHERE ch.file_id=?", (r["id"],)
            )
        )
        out.append({"path": r["path"], "name": r["name"], "size": r["size"], "mtime": (r["mtime"] or 0) * 1000,
                    "level": r["pii_level"], "types": dict(types)})
    return {"files": out}


@router.get("/privacy/groq-models")
def groq_models():
    try:
        return {"models": cloud.groq_models()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Groq: {e}") from e
