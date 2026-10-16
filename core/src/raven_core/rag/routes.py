"""Ask API: POST /ask streams NDJSON (see rag/ask.py)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import ask as core
from .retrieve import retrieve

router = APIRouter()


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    think_harder: bool = False
    consent: bool = False
    history: list[dict] = []


@router.post("/ask")
def ask(b: AskBody):
    return StreamingResponse(core.ask(b.question, b.think_harder, b.consent, b.history), media_type="application/x-ndjson")


@router.get("/ask/retrieve")
def ask_retrieve(q: str, k: int = 6):
    """Retrieval alone, for tests and calibration."""
    sources, info = retrieve(q, k=k)
    return {"sources": [s.public() | {"sim": s.sim, "chunks": s.chunk_ids} for s in sources], "info": info}
