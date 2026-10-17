"""Router and privacy API: run a task, the ledger, test redaction, sensitive files, cloud providers (PLAN4 4),
custom words (PLAN4 7.4)."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import config, db, lite
from ..privacy import gate
from ..privacy.model import model as layer2
from . import cloud, local
from . import providers as registry
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
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except (ValueError, local.LocalError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/privacy/status")
def status():
    return {
        "ai_mode": core.ai_mode(),
        "providers": cloud.configured(),
        "provider_names": {r["id"]: registry.label(r["id"]) for r in registry.rows()},
        "dev": config.DEV,
        "claude_account": db.get_setting("claude_config_dir") or "",
        "local_model": local.LLM,
        "local_ready": local.available(),
        "layer2": {"available": layer2.available(), "error": layer2.error, "load_ms": layer2.load_ms},
        # Strict mode (PLAN4 7.3): the second model. "on" needs the switch and the model files.
        "strict": {"switch": lite.enabled("pii_strict"), "available": gate.strict.available(), "on": gate.strict_on(),
                   "error": gate.strict.error},
        # Fail closed (PLAN4 7.5): without the Privacy Filter nothing goes to the cloud. None means ready.
        "cloud_blocked": gate.layer2_ready(),
        "words": len(gate.custom_words()),
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


# ---------------------------------------------------------------- custom words (PLAN4 7.4)


class WordsBody(BaseModel):
    words: list[str]


@router.get("/privacy/words")
def words_get():
    return {"words": db.get_setting("privacy_words") or []}


@router.post("/privacy/words")
def words_set(b: WordsBody):
    seen, out = set(), []
    for w in b.words:
        w = " ".join(str(w).split())[:80]
        if len(w) >= 2 and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    if len(out) > 500:
        raise HTTPException(400, "At most 500 words")
    db.set_setting("privacy_words", out)
    gate.forget_words_cache()
    return {"words": out}


# ---------------------------------------------------------------- cloud providers (PLAN4 4)


class ProviderAddBody(BaseModel):
    preset: str
    base_url: str | None = None
    name: str | None = None
    key: str | None = None


class ProviderUpdateBody(BaseModel):
    id: str
    on: bool | None = None
    model: str | None = None
    clear_model: bool = False
    base_url: str | None = None
    name: str | None = None
    key: str | None = None
    clear_key: bool = False


class ProviderIdBody(BaseModel):
    id: str


class ProviderOrderBody(BaseModel):
    ids: list[str]


def _presets() -> list[dict]:
    return [{"id": k, "name": v["name"], "adapter": v["adapter"], "key_hint": v["key_hint"], "key_url": v["key_url"],
             "free": v["free"], "custom": bool(v.get("custom"))} for k, v in registry.presets().items()]


@router.get("/cloud/providers")
def providers_list():
    from .. import keys

    return {"providers": registry.public(), "presets": _presets(), "key_store": keys.store()}


@router.post("/cloud/providers/add")
def providers_add(b: ProviderAddBody):
    try:
        row = registry.add(b.preset, b.base_url, b.name)
        if b.key:
            registry.set_key(row["id"], b.key)
    except registry.ProviderError as e:
        raise HTTPException(400, str(e)) from e
    return providers_list()


@router.post("/cloud/providers/update")
def providers_update(b: ProviderUpdateBody):
    try:
        changes = {"on": b.on, "base_url": b.base_url, "name": b.name}
        if b.model is not None or b.clear_model:
            changes["model"] = None if b.clear_model else b.model
        registry.update(b.id, **changes)
        if b.key or b.clear_key:
            registry.set_key(b.id, None if b.clear_key else b.key)
    except registry.ProviderError as e:
        raise HTTPException(400, str(e)) from e
    return providers_list()


@router.post("/cloud/providers/remove")
def providers_remove(b: ProviderIdBody):
    registry.remove(b.id)
    return providers_list()


@router.post("/cloud/providers/order")
def providers_order(b: ProviderOrderBody):
    try:
        registry.reorder(b.ids)
    except registry.ProviderError as e:
        raise HTTPException(400, str(e)) from e
    return providers_list()


@router.get("/cloud/models")
async def providers_models(id: str):
    try:
        return {"models": await run_in_threadpool(registry.models, id, True)}
    except (registry.ProviderError, cloud.CloudError) as e:
        raise HTTPException(400, str(e)) from e
