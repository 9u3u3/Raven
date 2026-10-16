"""HTTP and WebSocket API of the sidecar. It binds to 127.0.0.1 and needs the token on every call."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from watchfiles import Change, watch

from . import config, db, events
from .config import IMAGE_EXTS, THUMB_DIR, VERSION
from .fs import listing, ops
from .fs.kinds import ext_of
from .index.indexer import indexer
from .search import hybrid
from .compress import routes as compress_routes
from .convert import routes as convert_routes
from .agent import routes as agent_routes
from .rag import routes as rag_routes
from .router import routes as router_routes
from .storage import routes as space
from .media import routes as media_routes

log = logging.getLogger("raven")
# Pop, not get: the tools that the sidecar starts (claude, 7z, soffice) do not inherit the token (PLAN3 P0-1).
TOKEN = os.environ.pop("RAVEN_TOKEN", "")
# The media token opens only GET on MEDIA_ROUTES: the URLs in <img>, <video> and the PDF frame carry it, not
# the main token (PLAN3 P0-4).
MEDIA_TOKEN = os.environ.pop("RAVEN_MEDIA_TOKEN", "")
MEDIA_ROUTES = ("/fs/raw", "/fs/thumb", "/visual/thumb")
# A file from /fs/raw with one of these types would open as a page on the sidecar origin, and a script in it
# could call the API. Raven sends these files as plain text.
ACTIVE_TYPES = {"html", "htm", "xhtml", "shtml", "xml", "xsl", "xslt", "js", "mjs", "cjs", "svgz", "hta", "mht", "mhtml"}


_started = {"done": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import secure

    events.bind_loop(asyncio.get_running_loop())
    state = secure.startup()
    if state != "locked":
        _start_services(background=False)
        # The port opens when this function yields. The background threads start after that, so they do not
        # slow the start-up (a large index has many threads with work at once).
        asyncio.get_running_loop().call_later(0.3, _start_background)
    else:
        log.info("the index is encrypted and locked: waiting for the passphrase")
    _write_discovery()
    yield
    _remove_discovery()
    if _started["done"]:
        indexer.shutdown()
        dir_watch.stop()
        try:
            db.conn().execute("PRAGMA optimize")  # PLAN3 P1-7: fresh query statistics for the next start
        except Exception:  # noqa: BLE001
            pass


def _start_services(background: bool = True) -> None:
    """Everything that reads the database. It runs at start, or after an unlock."""
    if _started["done"]:
        return
    _started["done"] = True
    db.init()
    from . import habits, lite

    lite.apply_priority()
    habits.init()
    habits.prune()
    from .search import folders

    folders.init()
    from .media import visual

    visual.init()
    from . import learn, rules

    learn.init()
    rules.init()
    from . import keys

    keys.migrate()
    from .compress import auto  # noqa: F401  (import now, not in a busy process later)

    if background:
        _start_background()


def _start_background() -> None:
    if _started.get("background"):
        return
    _started["background"] = True
    from . import rules
    from .compress import auto

    indexer.start()
    threading.Thread(target=auto.loop, args=(indexer.stop,), name="auto-compress", daemon=True).start()
    threading.Thread(target=rules.loop, args=(indexer.stop,), name="watch-rules", daemon=True).start()
    from .storage import reclaim

    threading.Thread(target=reclaim.disk_loop, args=(indexer.stop,), name="disk-forecast", daemon=True).start()
    from . import maintenance

    threading.Thread(target=maintenance.loop, args=(indexer.stop,), name="index-maintenance", daemon=True).start()


DISCOVERY = config.DATA_DIR / "sidecar.json"


def _write_discovery() -> None:
    """Port and token for local tools of this user (the MCP adapter). Mode 0600: other users cannot read it."""
    import json

    port = os.environ.get("RAVEN_PORT")
    if not port or not TOKEN:
        return
    fd = os.open(DISCOVERY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"port": int(port), "token": TOKEN, "pid": os.getpid()}, f)


def _remove_discovery() -> None:
    import json

    try:
        if json.loads(DISCOVERY.read_text()).get("pid") == os.getpid():
            DISCOVERY.unlink()
    except (OSError, ValueError):
        pass


class ClientJSON(JSONResponse):
    """JSON with Windows paths written with / (platform.to_client). Other systems: unchanged."""

    def render(self, content) -> bytes:
        from .platform import to_client

        return super().render(to_client(content))


app = FastAPI(title="raven-core", version=VERSION, lifespan=lifespan, default_response_class=ClientJSON)
app.include_router(space.router)
app.include_router(compress_routes.router)
app.include_router(convert_routes.router)
app.include_router(router_routes.router)
app.include_router(rag_routes.router)
app.include_router(agent_routes.router)
app.include_router(media_routes.router)
from . import mcp_gate  # noqa: E402

app.include_router(mcp_gate.router)
# The renderer runs from file:// or the Vite dev server. The token is the guard, not the origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _authorized(request_token: str | None) -> bool:
    return bool(TOKEN) and request_token is not None and hmac.compare_digest(request_token, TOKEN)


def _media_ok(request: Request, request_token: str | None) -> bool:
    return (bool(MEDIA_TOKEN) and request_token is not None and request.method == "GET"
            and request.url.path in MEDIA_ROUTES and hmac.compare_digest(request_token, MEDIA_TOKEN))


def _media_headers(response) -> None:
    """No type guessing, and no scripts if a file opens as a page. The PDF viewer does not run in a sandboxed
    document, so a PDF gets nosniff only."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not response.headers.get("content-type", "").startswith("application/pdf"):
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    tok = request.query_params.get("token")
    h = request.headers.get("authorization", "")
    if h.startswith("Bearer "):
        tok = h[7:]
    if not _authorized(tok) and not _media_ok(request, tok):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import secure

    if secure.locked() and not request.url.path.startswith(("/health", "/security")):
        return JSONResponse({"error": "The index is locked. Type the passphrase to open it.", "locked": True}, status_code=423)
    response = await call_next(request)
    if request.url.path in MEDIA_ROUTES:
        _media_headers(response)
    return response


@app.exception_handler(ops.OpError)
async def op_error(_: Request, e: ops.OpError):
    return JSONResponse({"error": str(e), **({"code": e.code} if e.code else {})}, status_code=e.status)


@app.exception_handler(FileNotFoundError)
async def not_found(_: Request, e: FileNotFoundError):
    return JSONResponse({"error": f"Not found: {e.filename or e}"}, status_code=404)


@app.exception_handler(PermissionError)
async def no_permission(_: Request, e: PermissionError):
    return JSONResponse({"error": f"Permission denied: {e.filename or e}"}, status_code=403)


_OS_ERRORS = {  # errno name -> (status, message). PLAN2 P2-9: shares refuse things that a local disk allows.
    "EINVAL": (400, "This drive or share does not allow that name. Avoid < > : \" \\ | ? * and names that end with a dot."),
    "EILSEQ": (400, "This drive or share does not allow a character in that name."),
    "ENAMETOOLONG": (400, "The name or the path is too long for this drive or share."),
    "EROFS": (403, "This drive or share is read-only."),
    "ENOSPC": (507, "The drive or share is full."),
    "EDQUOT": (507, "Your quota on this drive or share is full."),
    "ENOTSUP": (501, "This drive or share does not support that operation."),
    "EOPNOTSUPP": (501, "This drive or share does not support that operation."),
    "ETIMEDOUT": (504, "The share does not answer. Check the connection."),
    "EHOSTDOWN": (504, "The server is down."),
    "ENOTCONN": (504, "The share is not connected any more. Connect it again."),
}


@app.exception_handler(OSError)
async def os_error(_: Request, e: OSError):
    import errno as errno_

    name = errno_.errorcode.get(e.errno or 0, "")
    if name == "EIO" and e.filename:
        from .fs.network import is_network

        if is_network(str(e.filename)):
            return JSONResponse({"error": "The share does not answer. Check the connection, or disconnect it.", "errno": name},
                                status_code=504)
    if name in _OS_ERRORS:
        status, msg = _OS_ERRORS[name]
        return JSONResponse({"error": msg, "errno": name}, status_code=status)
    return JSONResponse({"error": e.strerror or str(e)}, status_code=500)


# ---------------------------------------------------------------- basics


@app.get("/health")
def health():
    from . import secure

    return {"ok": True, "version": VERSION, "pid": os.getpid(), "data_dir": str(config.DATA_DIR), "locked": secure.locked()}


# ---------------------------------------------------------------- models (first run)


@app.get("/models")
def models_status():
    from . import models

    return models.status()


class ModelsBody(BaseModel):
    names: list[str]


@app.post("/models/download")
def models_download(b: ModelsBody):
    from . import models

    return models.download(b.names)


# ---------------------------------------------------------------- encrypted index (secure.py)


@app.get("/security")
def security_status():
    from . import secure

    return secure.status()


class PassBody(BaseModel):
    passphrase: str
    remember: bool = False


@app.post("/security/unlock")
async def security_unlock(b: PassBody):
    from . import secure

    if not await run_in_threadpool(secure.unlock, b.passphrase):
        raise HTTPException(403, "Wrong passphrase")
    await run_in_threadpool(_start_services)
    if b.remember:
        secure._remember(b.passphrase)
    return {"ok": True}


@app.post("/security/encrypt")
async def security_encrypt(b: PassBody):
    """Copy the index into an encrypted file. The app then restarts the sidecar to swap the files."""
    from . import secure

    try:
        indexer.shutdown()
        return await run_in_threadpool(secure.encrypt, b.passphrase, b.remember)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/security/decrypt")
async def security_decrypt(b: PassBody):
    from . import secure

    try:
        indexer.shutdown()
        return await run_in_threadpool(secure.decrypt, b.passphrase)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not _authorized(websocket.query_params.get("token")):
        await websocket.close(code=4401)
        return

    async def on_message(msg: dict):
        if msg.get("type") == "watch_dir":
            dir_watch.set(msg.get("path"))

    await events.serve(websocket, on_message)


# ---------------------------------------------------------------- read-only file system


@app.get("/places")
def places():
    return listing.places()


# PLAN2 P2-9.4: a call on a share runs in its own small pool with a time limit. A share that does not answer
# then costs one of these threads, not a thread that local work needs, and the user gets an error in seconds.
_NET_POOL = None
NET_TIMEOUT = float(os.environ.get("RAVEN_NET_TIMEOUT", "5"))


async def _io(path: str, fn, *args, timeout: float | None = None):
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial

    from .fs.network import is_network

    global _NET_POOL
    if not is_network(os.path.expanduser(path)):
        return await run_in_threadpool(fn, *args)
    if _NET_POOL is None:
        _NET_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="net-io")
    fut = asyncio.get_running_loop().run_in_executor(_NET_POOL, partial(fn, *args))
    try:
        return await asyncio.wait_for(fut, timeout or NET_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(504, "The share does not answer. Check the connection, or disconnect it.") from None


@app.get("/fs/list")
async def fs_list(path: str, hidden: bool = True):
    return await _io(path, _fs_list, path, hidden)


def _fs_list(path: str, hidden: bool = True):
    p = os.path.expanduser(path)
    from .fs import archive

    if not os.path.isdir(p) and archive.split(p):
        # A folder inside an archive (section 13.12): members, no extraction.
        return archive.list_dir(p)
    res = listing.list_dir(p, hidden=hidden)
    indexer.prioritize(res["path"])
    sizes = space.dir_sizes([e["path"] for e in res["entries"] if e["is_dir"]])
    for e in res["entries"]:
        if e["path"] in sizes:
            e["dir_size"] = sizes[e["path"]]
    return res


def _real(path: str) -> str:
    """A member of an archive is served from its extracted copy in the cache."""
    if os.path.lexists(path):
        return path
    from .fs import archive

    return archive.member_file(path) if archive.split(path) else path


@app.get("/fs/info")
async def fs_info(path: str):
    return await _io(path, _fs_info, path)


def _fs_info(path: str):
    if not os.path.lexists(path):
        from .fs import archive

        if archive.split(path):
            return archive.info(path)
    return listing.info(path)


def _hashes(path: str) -> dict:
    import blake3

    from .fs.atime import keep_atime

    sha, b3 = hashlib.sha256(), blake3.blake3()
    with keep_atime(path), open(path, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
            b3.update(chunk)
    return {"sha256": sha.hexdigest(), "blake3": b3.hexdigest()}


@app.get("/fs/hash")
async def fs_hash(path: str):
    """Checksums for Properties (section 6.1), on request only: a large file takes time."""
    path = await run_in_threadpool(_real, path)
    if not os.path.isfile(path):
        raise HTTPException(400, "Checksums are for files only")
    return await run_in_threadpool(_hashes, path)


class PathsBody(BaseModel):
    paths: list[str]
    dst: str | None = None
    on_conflict: str = "keep_both"
    label: str | None = None
    source: str = "manual"


@app.post("/fs/relative")
def fs_relative(b: PathsBody):
    """Copy as relative path (section 6.1): from the nearest project root (.git, package.json and others),
    else from the home folder as ~/..."""
    from .storage import protect

    home = os.path.expanduser("~")
    out = []
    for p in b.paths:
        cur = os.path.dirname(os.path.abspath(p))
        base = None
        while cur and cur != os.path.dirname(cur):
            if protect.is_project_root(cur):
                base = cur
                break
            cur = os.path.dirname(cur)
        if base:
            out.append(os.path.relpath(p, base))
        elif p == home or p.startswith(home + os.sep):
            out.append("~" + p[len(home):])
        else:
            out.append(p)
    return {"paths": out}


@app.get("/fs/free")
async def fs_free(path: str):
    return await _io(path, listing.disk_free, path)


@app.get("/fs/text")
async def fs_text(path: str, limit: int = 256 * 1024):
    return await _io(path, _fs_text, path, limit, timeout=max(NET_TIMEOUT, 15))


def _fs_text(path: str, limit: int):
    with open(_real(path), "rb") as f:
        raw = f.read(limit + 1)
    if b"\0" in raw[:8192]:
        raise HTTPException(415, "Binary file")
    return {"text": raw[:limit].decode("utf-8", errors="replace"), "truncated": len(raw) > limit}


@app.get("/fs/raw")
def fs_raw(path: str):
    path = _real(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if ext_of(path) in ACTIVE_TYPES:
        return FileResponse(path, media_type="text/plain; charset=utf-8")
    return FileResponse(path)


def _thumb(path: str, px: int) -> Path | None:
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{px}".encode()).hexdigest()
    out = THUMB_DIR / f"{key}.webp"
    if out.exists():
        return out
    ext = ext_of(path)
    from PIL import Image

    if ext == "pdf":
        import pymupdf

        from .pdflock import LOCK

        with LOCK, pymupdf.open(path) as doc:
            if doc.page_count == 0:
                return None
            page = doc[0]
            zoom = px / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
    elif ext in IMAGE_EXTS and ext not in ("svg", "heic", "jxl"):
        img = Image.open(path)
        img.draft("RGB", (px, px))
    else:
        return None
    img.thumbnail((px, px))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img.save(out, "WEBP", quality=78)
    return out


def _thumb_keep_atime(path: str, px: int) -> Path | None:
    from .fs.atime import keep_atime

    with keep_atime(path):
        return _thumb(path, px)


@app.get("/fs/thumb")
async def fs_thumb(path: str, size: int = 256):
    size = max(32, min(size, 1024))
    try:
        path = await _io(path, _real, path)
        out = await _io(path, _thumb_keep_atime, path, size, timeout=max(NET_TIMEOUT, 20))
    except Exception:
        out = None
    if out is None:
        return Response(status_code=204)
    return FileResponse(out, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------- changes (journal)



class RenameBody(BaseModel):
    path: str
    name: str


class CreateBody(BaseModel):
    parent: str
    name: str | None = None


@app.post("/fs/copy")
async def fs_copy(b: PathsBody):
    out = await run_in_threadpool(ops.copy, b.paths, b.dst, b.on_conflict)
    return {"paths": out, "journal": ops.peek()}


@app.post("/fs/move")
async def fs_move(b: PathsBody):
    out = await run_in_threadpool(ops.move, b.paths, b.dst, b.on_conflict)
    return {"paths": out, "journal": ops.peek()}


@app.post("/fs/rename")
def fs_rename(b: RenameBody):
    return {"path": ops.rename(b.path, b.name), "journal": ops.peek()}


@app.post("/fs/mkdir")
def fs_mkdir(b: CreateBody):
    return {"path": ops.mkdir(b.parent, b.name or "New folder"), "journal": ops.peek()}


@app.post("/fs/newfile")
def fs_newfile(b: CreateBody):
    return {"path": ops.newfile(b.parent, b.name or "New file.txt"), "journal": ops.peek()}


@app.post("/fs/trash")
async def fs_trash(b: PathsBody):
    n = await run_in_threadpool(ops.trash, b.paths, b.source, b.label)
    return {"count": n, "journal": ops.peek()}


class RenameManyBody(BaseModel):
    items: list[dict]


@app.post("/fs/rename_many")
async def fs_rename_many(b: RenameManyBody):
    out = await run_in_threadpool(ops.rename_many, b.items)
    return {"paths": out, "journal": ops.peek()}


class ArchiveBody(BaseModel):
    paths: list[str]
    format: str = "zip"
    name: str | None = None


@app.post("/fs/archive")
async def fs_archive(b: ArchiveBody):
    from .fs import archive

    out = await run_in_threadpool(archive.create, b.paths, b.format, b.name)
    return {"path": out, "journal": ops.peek()}


class ExtractBody(BaseModel):
    path: str
    dst: str | None = None


@app.post("/fs/extract")
async def fs_extract(b: ExtractBody):
    from .fs import archive

    out = await run_in_threadpool(archive.extract, b.path, b.dst)
    return {"path": out, "journal": ops.peek()}


@app.get("/fs/open_path")
async def fs_open_path(path: str):
    """The real path to open with another app: a member of an archive is extracted to the cache first."""
    from . import habits

    await run_in_threadpool(habits.record, "open", path)
    return {"path": await run_in_threadpool(_real, path)}


class ElevatedBody(BaseModel):
    op: str
    src: str | None = None
    dst: str | None = None


@app.post("/fs/elevated")
async def fs_elevated(b: ElevatedBody):
    """Retry one operation as administrator (Power user mode only)."""
    from .fs import elevate

    if not db.get_setting("power_user", False):
        raise HTTPException(403, "Turn on Power user mode in Settings to act as administrator.")
    return await run_in_threadpool(elevate.do, b.op, b.src, b.dst)


@app.post("/fs/restore")
async def fs_restore(b: PathsBody):
    out = await run_in_threadpool(ops.restore, b.paths)
    return {"paths": out, "journal": ops.peek()}


class JobBody(BaseModel):
    kind: str
    paths: list[str]
    dst: str
    choices: dict[str, str] = {}
    default: str = "keep_both"


@app.post("/fs/conflicts")
def fs_conflicts(b: PathsBody):
    from .fs import jobs

    return {"conflicts": jobs.conflicts(b.paths, b.dst or "")}


@app.post("/fs/jobs")
def fs_job_add(b: JobBody):
    from .fs import jobs

    if b.default not in ("keep_both", "replace", "skip") or any(v not in ("keep_both", "replace", "skip") for v in b.choices.values()):
        raise HTTPException(400, "A choice must be keep_both, replace or skip")
    return jobs.queue.add(b.kind, b.paths, b.dst, b.choices, b.default).public()


@app.get("/fs/jobs")
def fs_jobs():
    from .fs import jobs

    return {"jobs": jobs.queue.list()}


@app.post("/fs/jobs/{job_id}/{action}")
def fs_job_control(job_id: str, action: str):
    from .fs import jobs

    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(400, "action must be pause, resume or cancel")
    return jobs.queue.control(job_id, action).public()


@app.post("/fs/delete")
async def fs_delete(b: PathsBody):
    n = await run_in_threadpool(ops.delete, b.paths)
    return {"count": n, "journal": ops.peek()}


@app.post("/fs/undo")
async def fs_undo():
    res = await run_in_threadpool(ops.undo)
    return {**res, "journal": ops.peek()}


@app.post("/fs/redo")
async def fs_redo():
    res = await run_in_threadpool(ops.redo)
    return {**res, "journal": ops.peek()}


@app.get("/fs/journal")
def fs_journal():
    return ops.peek()


# ---------------------------------------------------------------- index and search


class RootsBody(BaseModel):
    roots: list[str]


@app.get("/index/status")
def index_status():
    return indexer.progress()


class ConnectBody(BaseModel):
    url: str
    user: str = ""
    password: str = ""
    domain: str = ""
    trust: bool = False


def _net_error(e) -> JSONResponse:
    d = e.as_dict()
    return JSONResponse({"error": d.pop("message"), **d}, status_code=400)


@app.post("/net/connect")
async def net_connect(b: ConnectBody):
    from .fs import network

    try:
        path = await run_in_threadpool(network.connect, b.url, b.user, b.password, b.domain, b.trust)
    except network.NetError as e:
        return _net_error(e)
    return {"path": path, "places": listing.places()}


@app.post("/net/shares")
async def net_shares(b: ConnectBody):
    from .fs import network

    try:
        url = network.normalize(b.url)
        p = network.parts(url)
        shares = await run_in_threadpool(network.list_shares, p["host"], b.user or p["user"], b.password, b.domain)
    except network.NetError as e:
        return _net_error(e)
    return {"host": p["host"], "shares": shares}


@app.get("/net/info")
def net_info():
    from .fs import network

    return {**network.backends(), "recent": network.recent()}


class NetPathBody(BaseModel):
    path: str


@app.post("/net/forget")
def net_forget(b: NetPathBody):
    from .fs import network

    network.forget(b.path)
    return {"recent": network.recent()}


def _rc(fn, *a):
    from .fs import rclone

    try:
        return fn(*a)
    except rclone.RcloneError as e:
        raise HTTPException(e.status, str(e)) from e


@app.get("/rclone")
async def rclone_status():
    from .fs import rclone

    return await run_in_threadpool(_rc, rclone.status)


class RemoteBody(BaseModel):
    name: str
    type: str
    params: dict[str, str] = {}


@app.post("/rclone/remotes")
async def rclone_create(b: RemoteBody):
    from .fs import rclone

    return await run_in_threadpool(_rc, rclone.create, b.name, b.type, b.params)


class RemoteName(BaseModel):
    name: str


@app.post("/rclone/mount")
async def rclone_mount(b: RemoteName):
    from .fs import rclone

    return await run_in_threadpool(_rc, rclone.mount, b.name)


@app.post("/rclone/unmount")
async def rclone_unmount(b: RemoteName):
    from .fs import rclone

    await run_in_threadpool(_rc, rclone.unmount, b.name)
    return {"ok": True}


@app.delete("/rclone/remotes/{name}")
async def rclone_delete(name: str):
    from .fs import rclone

    await run_in_threadpool(_rc, rclone.delete, name)
    return {"ok": True}


@app.post("/net/disconnect")
async def net_disconnect(b: NetPathBody):
    from .fs import network

    try:
        await run_in_threadpool(network.disconnect, b.path)
    except network.NetError as e:
        return _net_error(e)
    return {"places": listing.places()}


class OrganizeBody(BaseModel):
    path: str
    mode: str = "both"
    think_harder: bool = False


@app.post("/organize/suggest")
async def organize_suggest(b: OrganizeBody):
    from . import organize
    from .router import cloud, local

    try:
        plan = await run_in_threadpool(organize.suggest, b.path, b.mode, b.think_harder)
    except (ValueError, local.LocalError, cloud.CloudError) as e:
        raise HTTPException(400, str(e)) from e
    return organize.public(plan)


class OrganizeApplyBody(BaseModel):
    id: str
    items: list[dict]


@app.post("/organize/apply")
async def organize_apply(b: OrganizeApplyBody):
    from . import organize

    try:
        return await run_in_threadpool(organize.apply, b.id, b.items)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class NameBody(BaseModel):
    path: str | None = None  # a folder, one file, or None for the whole index
    use_llm: bool = True


@app.post("/name/suggest")
async def name_suggest(b: NameBody):
    """PLAN2 P2-3.2: new names for files with generic names. Nothing changes before /name/apply."""
    from . import naming

    return await run_in_threadpool(naming.scan, b.path, b.use_llm)


class NameApplyBody(BaseModel):
    id: str
    items: list[dict]


@app.post("/name/apply")
async def name_apply(b: NameApplyBody):
    from . import naming

    try:
        return await run_in_threadpool(naming.apply, b.id, b.items)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- granular control (PLAN2 P2-5)


class ModeBody(BaseModel):
    paths: list[str]
    mode: str
    recursive: bool = False
    files_mode: str | None = None
    elevated: bool = False


@app.post("/fs/chmod")
async def fs_chmod(b: ModeBody):
    from .fs import props

    return await run_in_threadpool(props.set_mode, b.paths, b.mode, b.recursive, b.files_mode, b.elevated)


class OwnerBody(BaseModel):
    paths: list[str]
    owner: str = ""
    group: str = ""
    recursive: bool = False


@app.post("/fs/chown")
async def fs_chown(b: OwnerBody):
    from .fs import elevate, props

    try:
        return await run_in_threadpool(props.set_owner, b.paths, b.owner, b.group, b.recursive)
    except KeyError as e:
        raise HTTPException(400, f"No such user or group: {e}") from e
    except elevate.ElevateError as e:
        raise HTTPException(e.status, str(e)) from e


class TouchBody(BaseModel):
    paths: list[str]
    mtime: float
    recursive: bool = False


@app.post("/fs/touch")
async def fs_touch(b: TouchBody):
    from .fs import props

    return await run_in_threadpool(props.set_times, b.paths, b.mtime, b.recursive)


class FlagBody(BaseModel):
    path: str
    flag: str
    on: bool


@app.post("/fs/flag")
async def fs_flag(b: FlagBody):
    from .fs import elevate, props

    try:
        return await run_in_threadpool(props.set_flag, b.path, b.flag, b.on)
    except elevate.ElevateError as e:
        raise HTTPException(e.status, str(e)) from e


class TagsBody(BaseModel):
    paths: list[str]
    add: list[str] = []
    remove: list[str] = []


@app.post("/fs/tags")
def fs_tags(b: TagsBody):
    from .fs import props

    return props.set_tags(b.paths, b.add, b.remove)


@app.get("/fs/tags/all")
def fs_tags_all():
    from .fs import props

    return {"tags": props.all_tags()}


class LinkBody(BaseModel):
    target: str
    link: str | None = None
    kind: str = "symbolic"


@app.post("/fs/link")
def fs_link(b: LinkBody):
    from .fs import props

    return props.make_link(b.target, b.link, b.kind)


@app.get("/fs/broken")
async def fs_broken(path: str, recursive: bool = True):
    from .fs import props

    return {"links": await run_in_threadpool(props.broken_links, path, recursive)}


@app.get("/fs/uses")
async def fs_uses(path: str):
    from .fs import props

    return {"processes": await run_in_threadpool(props.who_uses, path)}


@app.get("/fs/explain")
def fs_explain(path: str):
    from .fs import props

    return {"explain": props.explain(path)}


@app.get("/fs/policies")
def fs_policies():
    from .fs import props

    return {"policies": props.policies()}


class PolicyBody(BaseModel):
    path: str
    no_index: bool | None = None
    no_cloud: bool | None = None
    protect: bool | None = None
    no_compress: bool | None = None
    full_index: bool | None = None


@app.post("/fs/policies")
def fs_policy(b: PolicyBody):
    from .fs import props

    flags = {k: v for k, v in b.model_dump().items() if k != "path" and v is not None}
    out = props.set_policy(b.path, **flags)
    if "no_index" in flags or "full_index" in flags:
        indexer.roots_changed()  # a rescan drops (or adds) the folder's files
    return out


@app.get("/drives")
def drives_list():
    from .fs import props

    return {"drives": props.drives()}


class DriveBody(BaseModel):
    device: str


@app.post("/drives/{action}")
async def drives_action(action: str, b: DriveBody):
    from .fs import props

    return await run_in_threadpool(props.drive_action, b.device, action)


@app.post("/fs/shred")
async def fs_shred(b: PathsBody):
    from .fs import props

    return await run_in_threadpool(props.secure_delete, b.paths)


# ---------------------------------------------------------------- habits (PLAN2 P2-8)


class ActivityBody(BaseModel):
    kind: str
    path: str | None = None
    query: str | None = None
    extra: dict | None = None


@app.post("/ui/activity")
def ui_activity(b: ActivityBody):
    from . import habits

    if b.kind in ("suggestion_accepted", "suggestion_rejected"):
        habits.suggestion_feedback(b.query or "", b.kind == "suggestion_accepted")
    else:
        habits.record(b.kind, b.path, b.query, b.extra)
    return {"ok": True}


@app.get("/habits/continue")
def habits_continue(limit: int = 6):
    from . import habits

    return {"files": habits.continue_files(limit)}


@app.get("/habits/predict")
def habits_predict(path: str):
    from . import habits

    return {"predict": habits.predict_folder(path)}


@app.get("/habits/summary")
def habits_summary():
    from . import habits

    return habits.summary()


@app.delete("/habits")
def habits_clear():
    from . import habits

    habits.clear()
    return habits.summary()


# ---------------------------------------------------------------- file graph (PLAN2 P2-7)


@app.get("/graph")
async def graph_get(root: str | None = None, types: str = "", limit: int = 3000, orphans: bool = False):
    from . import graph

    return await run_in_threadpool(graph.graph, root, [t for t in types.split(",") if t] or None, limit, orphans)


@app.get("/describe")
async def describe_get(path: str):
    """A short description of a file or a folder for the Inspector (describe.py). No model call."""
    from .describe import describe

    return await run_in_threadpool(describe, path)


@app.get("/graph/path")
async def graph_path(path: str, types: str = ""):
    """The graph of one folder that the user names (only that folder is read)."""
    from . import graph

    try:
        return await run_in_threadpool(graph.for_path, path, [t for t in types.split(",") if t] or None)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/graph/local")
async def graph_local(path: str, depth: int = 1, types: str = ""):
    from . import graph

    return await run_in_threadpool(graph.local, path, depth, [t for t in types.split(",") if t] or None)


@app.post("/graph/build")
def graph_build():
    from . import graph

    return graph.build_async()


# ---------------------------------------------------------------- lite mode (PLAN2 P2-6)


@app.get("/lite")
def lite_status():
    from . import lite

    return lite.status()


class LiteModeBody(BaseModel):
    mode: str


@app.post("/lite/mode")
def lite_mode(b: LiteModeBody):
    from . import lite

    try:
        out = lite.set_mode(b.mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if out["reembed"]:
        indexer.state["embed_ready"] = False
        indexer.wake_c.set()
    return {**out, **lite.status()}


class FeatureBody(BaseModel):
    feature: str
    on: bool | None = None


@app.post("/lite/feature")
def lite_feature(b: FeatureBody):
    from . import lite

    try:
        lite.set_feature(b.feature, b.on)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return lite.status()


class CategoryBody(BaseModel):
    path: str
    category: str


@app.post("/classify/set")
def classify_set(b: CategoryBody):
    """The user corrects a category. Raven keeps it as an example (learn.py)."""
    from . import learn

    try:
        return learn.set_category(b.path, b.category)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/classify/categories")
def classify_categories():
    from . import classify

    return {"categories": [*classify.CATEGORIES, *classify.RULE_ONLY, "Other"]}


@app.get("/learn/examples")
def learn_examples():
    from . import learn

    return {"examples": learn.examples(), "folders": learn.folder_prefs()}


@app.delete("/learn/examples/{example_id}")
def learn_forget(example_id: int):
    from . import learn

    learn.forget(example_id)
    return {"ok": True}


class RuleBody(BaseModel):
    folder: str
    match: str
    value: str
    action: str
    target: str


@app.get("/rules")
def rules_list():
    from . import learn, rules

    return {"rules": rules.list_rules(), "suggestions": learn.habit_suggestions()}


@app.post("/rules")
def rules_add(b: RuleBody):
    from . import rules

    try:
        return rules.add_rule(b.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class RuleState(BaseModel):
    enabled: bool


@app.post("/rules/{rule_id}")
def rules_set(rule_id: int, b: RuleState):
    from . import rules

    rules.update_rule(rule_id, b.enabled)
    return {"ok": True}


@app.delete("/rules/{rule_id}")
def rules_delete(rule_id: int):
    from . import rules

    rules.delete_rule(rule_id)
    return {"ok": True}


@app.post("/rules/{rule_id}/run")
async def rules_run(rule_id: int):
    from . import rules

    r = next((x for x in rules.list_rules() if x["id"] == rule_id), None)
    if r is None:
        raise HTTPException(404, "No such rule")
    return await run_in_threadpool(rules.run_rule, r, True)


@app.get("/galaxy")
def galaxy_get():
    from . import galaxy

    return galaxy.get()


@app.post("/galaxy/build")
def galaxy_build():
    from . import galaxy

    try:
        return galaxy.build_async()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


class ClusterBody(BaseModel):
    cluster: int
    parent: str | None = None
    name: str | None = None


@app.post("/galaxy/plan")
def galaxy_plan(b: ClusterBody):
    from . import galaxy

    try:
        return galaxy.cluster_plan(b.cluster, b.parent, b.name)
    except (ValueError, ops.OpError) as e:
        raise HTTPException(400, str(e)) from e


class ClusterApply(BaseModel):
    folder: str
    moves: list[dict]


@app.post("/galaxy/apply")
async def galaxy_apply(b: ClusterApply):
    from . import galaxy

    return await run_in_threadpool(galaxy.cluster_apply, b.folder, b.moves)


@app.get("/classify/stats")
def classify_stats():
    from . import classify

    return classify.stats()


@app.get("/index/names")
def index_names():
    return hybrid.name_items()


@app.get("/index/size")
def index_size():
    return db.size_info()


@app.post("/index/compact")
async def index_compact():
    """Settings, "Make the index smaller" (PLAN3 P0-8)."""
    from . import maintenance

    info = db.size_info()
    if not maintenance.disk_room_for_compact(info):
        raise HTTPException(507, "Not enough free disk space. Compacting needs about the size of the index free.")
    return await run_in_threadpool(maintenance.run_once, True)


@app.post("/index/roots")
def index_roots(b: RootsBody):
    roots = []
    for r in b.roots:
        p = str(Path(os.path.expanduser(r)).resolve())
        if not os.path.isdir(p):
            raise HTTPException(400, f"Not a folder: {r}")
        if p not in roots:
            roots.append(p)
    db.set_setting("index_roots", roots)
    indexer.roots_changed()
    return {"roots": roots}


@app.get("/search")
async def search(q: str = Query(..., max_length=500), mode: str = "hybrid", limit: int = 50):
    # Habits: the renderer reports a finished search (/ui/activity), not every keystroke of the command bar.
    return await run_in_threadpool(hybrid.search, q, mode, limit)


@app.get("/search/parse")
async def search_parse(q: str = Query(..., max_length=500)):
    """Natural-language filters with the local LLM (section 9.3). Returns the query with filter tokens."""
    from .search import nl

    return await run_in_threadpool(nl.llm_parse, q)


@app.get("/search/folders")
async def search_folders(q: str = Query(..., max_length=500), limit: int = 12):
    from .search import folders

    return await run_in_threadpool(folders.search, q, limit)


@app.get("/search/vectors")
def search_vectors():
    from .search import vectors

    return vectors.status()


@app.get("/folders/status")
def folders_status():
    from .search import folders

    from .search.disk_dirs import disk_dirs

    return {**folders.status(), "disk": disk_dirs.status()}


@app.get("/setup/check")
async def setup_check():
    """The system check for the setup screen (setup.py)."""
    from . import setup

    return await run_in_threadpool(setup.check)


class ProviderBody(BaseModel):
    provider: str


@app.post("/cloud/test")
async def cloud_test(b: ProviderBody):
    """Check a stored cloud key with one small request that sends no file data."""
    from .router import cloud

    return await run_in_threadpool(cloud.test_key, b.provider)


@app.get("/cloud/claude-accounts")
async def cloud_claude_accounts():
    """The Claude accounts on this computer for claude -p, each with its sign-in state."""
    from .router import cloud

    return {"accounts": await run_in_threadpool(cloud.claude_accounts)}


@app.get("/settings")
def get_settings():
    rows = db.conn().execute("SELECT key, value FROM settings").fetchall()
    import json

    from . import keys

    out = {r[0]: json.loads(r[1]) for r in rows if r[0] not in ("ui_tabs", "galaxy")}
    # The renderer never gets a stored API key back, only a hint that one is set. The keys can live in the
    # system keyring, not in this table (keys.py).
    for k in keys.NAMES:
        v = keys.get(k)
        if v or k in out:
            out[k] = ("…" + v[-4:]) if v else ""
    out["key_store"] = keys.store()
    return out


class SettingBody(BaseModel):
    key: str
    value: Any


class VisitBody(BaseModel):
    path: str


@app.post("/ui/visit")
def ui_visit(b: VisitBody):
    if os.path.isdir(b.path):
        db.visit(b.path)
    return {"ok": True}


@app.get("/ui/frecent")
def ui_frecent(limit: int = 8):
    rows = [(p, s) for p, s in db.frecent(200).items() if os.path.isdir(p)][:limit]
    return {"folders": [{"path": p, "name": os.path.basename(p) or p, "score": round(s, 3)} for p, s in rows]}


# The open tabs (renderer state). SQLite writes at once, so a crash or a kill does not lose them.
@app.get("/ui/tabs")
def ui_tabs():
    return {"tabs": db.get_setting("ui_tabs")}


class TabsBody(BaseModel):
    tabs: Any


@app.post("/ui/tabs")
def ui_tabs_save(b: TabsBody):
    import json

    if len(json.dumps(b.tabs)) > 2_000_000:
        raise HTTPException(413, "Too much tab state")
    db.set_setting("ui_tabs", b.tabs)
    return {"ok": True}


@app.post("/settings")
def set_setting(b: SettingBody):
    allowed = {"embed_model", "index_hidden", "ai_mode", "theme", "favorites", "show_hidden", "view", "mcp_enabled", "binary_vectors",
               "power_user", "first_run_done",
               "groq_api_key", "groq_model", "claude_model", "cloud_provider", "anthropic_api_key", "anthropic_model",
               "claude_config_dir", "index_code", "habits"}
    if b.key == "cloud_provider" and b.value not in ("auto", "groq", "claude", "anthropic"):
        raise HTTPException(400, "cloud_provider must be auto, groq, claude or anthropic")
    if b.key.endswith("_api_key") and isinstance(b.value, str):
        b.value = b.value.strip() or None
    if b.key == "ai_mode" and b.value not in ("local", "hybrid", "ask"):
        raise HTTPException(400, "ai_mode must be local, hybrid or ask")
    if b.key == "binary_vectors" and b.value not in ("auto", "on", "off"):
        raise HTTPException(400, "binary_vectors must be auto, on or off")
    if b.key not in allowed:
        raise HTTPException(400, f"Unknown setting: {b.key}")
    if b.key.endswith("_api_key"):
        from . import keys

        return {"ok": True, "store": keys.put(b.key, b.value)}
    db.set_setting(b.key, b.value)
    if b.key == "binary_vectors":
        from .search import vectors

        vectors.forget_cache()
        threading.Thread(target=vectors.fill, name="binary-fill", daemon=True).start() if b.value == "on" else None
    if b.key == "embed_model":
        # A new model means new vectors. The embed loop drops the old table and starts again.
        indexer.state["embed_ready"] = False
        indexer.wake_c.set()
    elif b.key == "index_hidden":
        from .index import indexer as indexer_mod

        indexer_mod.db_cache.index_hidden = bool(b.value)
        indexer.roots_changed()
    elif b.key == "index_code":
        from .index import indexer as indexer_mod

        indexer_mod.db_cache.index_code = bool(b.value)
        indexer.roots_changed()
    return {"ok": True}


# ---------------------------------------------------------------- current-folder watcher


DIR_MODIFIED_EVERY = 5.0
_TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".swp", ".swx", "~", ".lock")


def _dir_watch_filter(_change, path: str) -> bool:
    name = os.path.basename(path)
    if name.endswith(_TEMP_SUFFIXES) or name.startswith((".#", "~$")):
        return False
    return not name.startswith(".") or bool(db.get_setting("show_hidden", False))


class DirWatch:
    """Watch the folder that the renderer shows, so the list updates when other apps change it."""

    def __init__(self) -> None:
        self.path: str | None = None
        self._stop = threading.Event()

    def set(self, path: str | None) -> None:
        if path == self.path:
            return
        self.stop()
        self.path = path
        if not path or not os.path.isdir(path):
            return
        stop = self._stop = threading.Event()

        def run():
            last = 0.0
            try:
                # PLAN3 P0-11: a busy folder (home, Downloads during a download) changes many times each second.
                # Hidden and temporary files do not reach the list, a batch waits 1 s, and a batch with only
                # changed files (no name added or removed) goes out at most every 5 s.
                for changes in watch(path, stop_event=stop, recursive=False, debounce=1000, watch_filter=_dir_watch_filter):
                    names_changed = any(c != Change.modified for c, _ in changes)
                    now = time.monotonic()
                    if names_changed or now - last >= DIR_MODIFIED_EVERY:
                        last = now
                        events.publish("dir_changed", path=path)
            except Exception:
                pass

        threading.Thread(target=run, name="dir-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


dir_watch = DirWatch()
