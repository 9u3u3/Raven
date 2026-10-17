"""Text embeddings (tier T1): through Ollama, or a static model on the CPU in lite mode (PLAN2 P2-6).

A static model (model2vec "potion") is a table of token vectors: no GPU, no Ollama, about 30 to 130 MB,
and thousands of chunks per second on one CPU core. Its name in the embed_model setting starts with "potion".
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

import httpx

from .. import config, db

_client = httpx.Client(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(120, connect=2))
_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_cache_lock = threading.Lock()
CACHE_SIZE = 512


def model() -> str:
    return db.get_setting("embed_model") or config.EMBED_MODEL


def _doc_text(m: str, title: str, text: str) -> str:
    if m.startswith("embeddinggemma"):
        return f"title: {title or 'none'} | text: {text}"
    return text


def _query_text(m: str, q: str) -> str:
    if m.startswith("embeddinggemma"):
        return f"task: search result | query: {q}"
    if m.startswith("qwen3-embedding"):
        return f"Instruct: Given a search query, retrieve passages from the user's files that match it\nQuery: {q}"
    return q


_static: dict = {}
_static_lock = threading.Lock()


def is_static(m: str | None = None) -> bool:
    return (m or model()).startswith("potion")


# How long Ollama keeps the embedding model after a call. Each search needs it, so it stays longer than the LLM.
KEEP = os.environ.get("RAVEN_EMBED_KEEP", "10m")
_used_ollama = False  # Raven called the Ollama model in this run, so unload() frees it at exit
STATIC_IDLE = float(os.environ.get("RAVEN_STATIC_IDLE", "300"))  # seconds: an unused static model leaves memory (it loads again in about 0.3 s)


def _static_model(m: str):
    import time

    with _static_lock:
        if _static.get("name") != m or _static.get("model") is None:
            from model2vec import StaticModel

            # float16: half the memory, the same ranking for search.
            _static.update(name=m, model=StaticModel.from_pretrained(str(config.MODELS_DIR / m), quantize_to="float16"))
            if not _static.get("reaper"):
                _static["reaper"] = threading.Thread(target=_reap, name="static-reaper", daemon=True)
                _static["reaper"].start()
        _static["last"] = time.monotonic()
        return _static["model"]


def _reap() -> None:
    import time

    from ..memory import trim

    while True:
        time.sleep(30)
        with _static_lock:
            if _static.get("model") is not None and time.monotonic() - _static.get("last", 0) > STATIC_IDLE:
                _static["model"] = None
                unloaded = True
            else:
                unloaded = False
        if unloaded:
            trim(min_gap=0)


def static_available(m: str = "potion-retrieval-32m") -> bool:
    return (config.MODELS_DIR / m / "model.safetensors").exists()


def _embed(inputs: list[str]) -> list[list[float]]:
    m = model()
    if is_static(m):
        vecs = _static_model(m).encode(inputs, normalize=True)
        return [v.tolist() for v in vecs]
    global _used_ollama
    _used_ollama = True
    r = _client.post("/api/embed", json={"model": m, "input": inputs, "keep_alive": KEEP, "truncate": True})
    r.raise_for_status()
    return r.json()["embeddings"]


def embed_docs(items: list[tuple[str, str]]) -> list[list[float]]:
    """items: (title, text with header). Return one vector per item."""
    m = model()
    return _embed([_doc_text(m, t, x) for t, x in items])


def embed_query(q: str) -> list[float]:
    m = model()
    key = (m, q)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    vec = _embed([_query_text(m, q)])[0]
    with _cache_lock:
        _cache[key] = vec
        if len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return vec


def available() -> bool:
    if is_static():
        return static_available(model())
    try:
        r = _client.get("/api/tags", timeout=1.5)
        names = {x["name"].split(":")[0] for x in r.json().get("models", [])}
        return model().split(":")[0] in names
    except Exception:
        return False


def unload() -> None:
    """At exit: Ollama frees the embedding model now. An Ollama that ran before Raven keeps running."""
    if not _used_ollama:
        return
    try:
        _client.post("/api/embed", json={"model": model(), "input": [], "keep_alive": 0}, timeout=2)
    except Exception:  # noqa: BLE001
        pass


def warm() -> int | None:
    """Load the model into memory and return the vector size."""
    try:
        return len(_embed(["warm up"])[0])
    except Exception:
        return None
