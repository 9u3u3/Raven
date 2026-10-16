"""Push events to the renderer over WebSocket. Worker threads may call publish()."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


async def _send_all(text: str) -> None:
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def publish(kind: str, **data: Any) -> None:
    """Send one event to every client. Safe from any thread."""
    if _loop is None or not _clients:
        return
    text = json.dumps({"type": kind, **data})
    _loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_send_all(text)))


async def serve(ws: WebSocket, on_message=None) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if on_message:
                try:
                    await on_message(json.loads(msg))
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        _clients.discard(ws)
