"""Raven as an MCP server (section 13.12 item 1): the sidecar side.

An MCP client (Claude Desktop, Cursor, Zed) is an AI outside Raven, so Raven treats every answer to it as
outbound data (section 10):
- the user turns MCP on in Settings first (off by default);
- paths go out relative to the home folder (`~/...`), never with the user name;
- names, snippets and texts pass the redaction gate, and a result that still holds a high-risk item is
  blocked (10.3 step 6);
- every answer is written to the privacy ledger with its exact payload (10.4);
- the tools only read. No tool moves, renames or deletes a file. Organize returns a plan to show.
- Ask uses the local model only: the MCP client is the cloud model in this flow.

mcp_server.py is the stdio adapter that the client starts. It calls these routes on the running sidecar.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import db
from .privacy import gate

router = APIRouter(prefix="/mcp")
HOME = str(Path.home())
MAX_READ = 12_000


def enabled() -> bool:
    return bool(db.get_setting("mcp_enabled", False))


def _need() -> None:
    if not enabled():
        raise HTTPException(403, "Raven's MCP access is off. Turn it on in Raven: Settings, MCP clients.")


def _out(path: str) -> str:
    return "~" + path[len(HOME):] if path == HOME or path.startswith(HOME + "/") else path


def _in(path: str) -> str:
    p = os.path.abspath(os.path.expanduser(path))
    roots = [os.path.abspath(r) for r in db.get_setting("index_roots", [])]
    if not any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots):
        raise HTTPException(403, "Raven shares only files inside its indexed folders.")
    from .fs.props import policy_for

    if policy_for(p, "no_cloud"):
        raise HTTPException(403, "A folder rule keeps this folder away from AI outside Raven.")
    return p


def _send(tool: str, payload: dict, red: gate.Redactor) -> dict:
    """The last step for every tool: the final check, then the ledger, then the answer."""
    text = json.dumps(payload, ensure_ascii=False)
    blocked, reason = gate.final_check(text)
    if blocked:
        gate.ledger_write(f"mcp {tool}", "T3", "mcp", f"BLOCKED: {reason}", len(red.mapping))
        raise HTTPException(451, f"Raven blocked this answer: {reason}.")
    gate.ledger_write(f"mcp {tool}", "T3", "mcp", text, len(red.mapping))
    return payload


def _no_cloud(path: str) -> bool:
    from .fs.props import policy_for

    return policy_for(path, "no_cloud")


def _path_out(path: str, red: gate.Redactor) -> str:
    """~/rel/path with personal names in folder and file names replaced."""
    parts = _out(path).split("/")
    return "/".join(red.redact(p, name=True) if i else p for i, p in enumerate(parts))


# ---------------------------------------------------------------- tools


def search(q: str, limit: int) -> dict:
    from .search import hybrid

    res = hybrid.search(q, "hybrid", max(1, min(limit, 25)))
    red = gate.Redactor()
    rows = []
    from .fs.props import policy_for

    for r in res["results"]:
        if policy_for(r["path"], "no_cloud"):
            continue  # a folder rule: an MCP client is an AI outside Raven
        snippet = "".join(s["t"] for s in r.get("snippet") or [])
        rows.append({
            "path": _path_out(r["path"], red), "kind": r["kind"], "why": r["why"],
            "page": r.get("page"), "size": r.get("size"), "snippet": red.redact(snippet) if snippet else "",
        })
    return _send("search", {"query": q, "results": rows}, red)


def ask(question: str) -> dict:
    from .rag import ask as rag

    answer, sources, tier = "", [], None
    for line in rag.ask(question, False, False, [], local_only=True):
        e = json.loads(line)
        if e["type"] == "token":
            answer += e["t"]
        elif e["type"] == "sources":
            sources = e["sources"]
        elif e["type"] == "error":
            raise HTTPException(502, e.get("message") or "Ask failed")
    if any(_no_cloud(s["path"]) for s in sources):
        raise HTTPException(403, "The answer uses files in a folder that a folder rule keeps away from AI outside Raven.")
    red = gate.Redactor()
    return _send("ask", {
        "question": question,
        "answer": red.redact(answer),
        "sources": [{"n": s["n"], "path": _path_out(s["path"], red), "page": s.get("page")} for s in sources],
        "model": "local",
    }, red)


def read(path: str, max_chars: int) -> dict:
    from .index.extract import extract
    from .fs.kinds import ext_of

    p = _in(path)
    if not os.path.isfile(p):
        raise HTTPException(404, "No such file")
    row = db.conn().execute("SELECT category FROM files WHERE path=?", (p,)).fetchone()
    if row and row[0] == "Secret":
        raise HTTPException(403, "This file holds passwords or keys. Raven does not share it.")
    ex = extract(p, ext_of(p))
    text = "\n\n".join(s.text for s in ex.sections)[: max(500, min(max_chars, MAX_READ))]
    red = gate.Redactor()
    return _send("read", {"path": _path_out(p, red), "title": red.redact(ex.title or "") if ex.title else None,
                          "text": red.redact(text), "truncated": len(text) >= max_chars}, red)


def list_folder(path: str) -> dict:
    from .fs import listing

    p = _in(path)
    res = listing.list_dir(p, hidden=False)
    red = gate.Redactor()
    items = [{"name": red.redact(e["name"], name=True), "folder": e["is_dir"], "size": e["size"], "kind": e["kind"]}
             for e in sorted(res["entries"], key=lambda e: (not e["is_dir"], e["name"].lower()))[:300]]
    return _send("list", {"path": _path_out(p, red), "items": items}, red)


def organize(path: str) -> dict:
    from . import organize as org

    p = _in(path)
    try:
        plan = org.suggest(p)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    red = gate.Redactor()
    moves = [{"from": _path_out(m.src, red), "to": _path_out(m.dst, red), "why": m.reason} for m in plan.moves]
    return _send("organize", {"folder": _path_out(p, red), "moves": moves,
                              "note": "A plan only. The user applies it in Raven: Organize, Apply."}, red)


def status() -> dict:
    from .index.indexer import indexer

    p = indexer.progress()
    return {"files": p["files"], "chunks": p["chunks"], "roots": [_out(r) for r in p["roots"]], "mcp": enabled()}


# ---------------------------------------------------------------- routes


class AskBody(BaseModel):
    question: str


class PathBody(BaseModel):
    path: str
    max_chars: int = 6000


@router.get("/status")
def r_status():
    return status()


@router.get("/config")
def r_config():
    """The block to paste into an MCP client's config. A packaged Raven starts itself with --mcp."""
    import sys

    from .config import DATA_DIR
    from platformdirs import user_data_dir

    frozen = getattr(sys, "frozen", False)
    cmd = sys.executable
    args = ["--mcp"] if frozen else ["-m", "raven_core", "--mcp"]
    entry: dict = {"command": cmd, "args": args}
    if str(DATA_DIR) != user_data_dir("raven", appauthor=False):
        entry["env"] = {"RAVEN_DATA_DIR": str(DATA_DIR)}
    return {"enabled": enabled(), "config": {"mcpServers": {"raven": entry}}}


@router.get("/search")
async def r_search(q: str, limit: int = 10):
    _need()
    return await run_in_threadpool(search, q, limit)


@router.post("/ask")
async def r_ask(b: AskBody):
    _need()
    return await run_in_threadpool(ask, b.question)


@router.post("/read")
async def r_read(b: PathBody):
    _need()
    return await run_in_threadpool(read, b.path, b.max_chars)


@router.post("/list")
async def r_list(b: PathBody):
    _need()
    return await run_in_threadpool(list_folder, b.path)


@router.post("/organize")
async def r_organize(b: PathBody):
    _need()
    return await run_in_threadpool(organize, b.path)
