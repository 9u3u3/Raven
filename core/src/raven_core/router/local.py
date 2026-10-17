"""Tier T2: the local LLM through Ollama (loopback only)."""

from __future__ import annotations

import json
import os
import re

import httpx

from .. import config

LLM = os.environ.get("RAVEN_LLM", "qwen3:4b")
# How long Ollama keeps the model in memory after a call. A day (the old value) kept 2.5 to 5 GB busy for one question.
KEEP = os.environ.get("RAVEN_LLM_KEEP", "5m")  # after a call from the user: the next question is quick
KEEP_BACKGROUND = "1m"  # after background work: the next batch comes within a second, then the model unloads
_used = False  # Raven called the model in this run, so unload() frees it at exit


def use_model(name: str) -> None:
    """Switch the local model (the size choice in Features and models, PLAN4 section 5.2)."""
    global LLM, _seen_ok
    LLM, _seen_ok = name, 0.0


def load_setting() -> None:
    """The model from the "llm_model" setting. RAVEN_LLM wins, for tests and scripts."""
    from .. import db

    m = db.get_setting("llm_model")
    if m and not os.environ.get("RAVEN_LLM"):
        use_model(str(m))
_client = httpx.Client(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(300, connect=2))


class LocalError(Exception):
    pass


_seen_ok = 0.0
_why = ""


def available() -> bool:
    """True when Ollama has the local model. A busy Ollama (a model loads) can need a few seconds to answer,
    so the check waits up to 5 s and keeps a good answer for 60 s."""
    global _seen_ok, _why
    import time

    from .. import lite

    if not lite.enabled("llm"):
        _why = "The local AI model is off (lite mode). Turn it on in Settings, Performance."
        return False

    if time.time() - _seen_ok < 60:
        return True
    try:
        names = {m["name"] for m in _client.get("/api/tags", timeout=5).json().get("models", [])}
    except Exception:  # noqa: BLE001
        _why = f"Ollama does not answer at {config.OLLAMA_URL}. Start it with: ollama serve"
        return False
    if LLM in names or f"{LLM}:latest" in names:
        _seen_ok = time.time()
        return True
    _why = f"The local model {LLM} is not in Ollama. Run: ollama pull {LLM}"
    return False


def unavailable_reason() -> str:
    """Why available() said no: Ollama does not answer, or the model is missing."""
    return _why or f"The local model {LLM} is not in Ollama. Run: ollama pull {LLM}"


def background_allowed() -> bool:
    """Background LLM work (folder summaries, file categories) runs only on mains power with an idle computer.
    A request from the user always runs."""
    from ..compress.auto import idle, on_ac

    return on_ac() and idle()


def chat(system: str, user: str, schema: dict, max_tokens: int = 450, background: bool = False) -> dict:
    """Short JSON task: schema output, a token limit, no thinking (section 5.2 rule 8). A call from the user
    pauses background indexing. A background call does not, and the model unloads sooner after it."""
    if background:
        return _chat(system, user, schema, max_tokens, KEEP_BACKGROUND)
    from ..index.indexer import indexer

    was_set = indexer.interactive.is_set()
    indexer.interactive.set()
    try:
        return _chat(system, user, schema, max_tokens)
    finally:
        if not was_set:
            indexer.interactive.clear()


def _chat(system: str, user: str, schema: dict, max_tokens: int, keep: str = KEEP) -> dict:
    global _used
    _used = True
    body = {
        "model": LLM,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": keep,
        "options": {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": 8192},
    }
    try:
        r = _client.post("/api/chat", json=body)
    except httpx.HTTPError as e:
        raise LocalError(f"Ollama is not reachable: {e}") from e
    if r.status_code != 200:
        raise LocalError(f"Ollama error {r.status_code}: {r.text[:200]}")
    j = r.json()
    content = j["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # The token limit cut the answer: keep the complete items of a list.
        if j.get("done_reason") == "length":
            fixed = salvage(content)
            if fixed is not None:
                return fixed
        raise LocalError("The local model did not return JSON") from e


def salvage(content: str) -> dict | None:
    """A JSON answer that ends in the middle of a list item: drop that item and close the brackets."""
    end = len(content)
    for _ in range(50):
        end = content.rfind("}", 0, end)
        if end < 0:
            return None
        head = content[: end + 1]
        opens = [ch for ch in head if ch in "[{"]
        closes = [ch for ch in head if ch in "]}"]
        if len(opens) <= len(closes):
            continue
        stack = []
        for ch in head:
            if ch in "[{":
                stack.append(ch)
            elif ch in "]}" and stack:
                stack.pop()
        tail = "".join("]" if ch == "[" else "}" for ch in reversed(stack))
        try:
            out = json.loads(head + tail)
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def unload() -> None:
    """At exit: Ollama frees the model now, not after KEEP. An Ollama that ran before Raven keeps running."""
    if not _used:
        return
    try:
        _client.post("/api/generate", json={"model": LLM, "prompt": "", "keep_alive": 0}, timeout=2)
    except Exception:  # noqa: BLE001
        pass


def warm() -> None:
    global _used
    _used = True
    try:
        _client.post("/api/generate", json={"model": LLM, "prompt": "", "keep_alive": KEEP}, timeout=60)
    except Exception:  # noqa: BLE001
        pass


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}},
    "required": ["answer", "sources"],
}


class _JsonString:
    """Decode the "answer" string of a streamed JSON object, piece by piece.

    qwen3:4b is a thinking model and ignores think=false for free text. A JSON schema
    output leaves no room for the thinking, so the answer comes as {"answer": "..."}.
    """

    def __init__(self) -> None:
        self.raw = ""
        self.pos = -1  # the index after the opening quote of the value
        self.done = False

    def feed(self, piece: str) -> str:
        self.raw += piece
        if self.done:
            return ""
        if self.pos < 0:
            m = re.search(r'"answer"\s*:\s*"', self.raw)
            if not m:
                return ""
            self.pos = m.end()
        out = []
        i = self.pos
        while i < len(self.raw):
            ch = self.raw[i]
            if ch == '"':
                self.done = True
                i += 1
                break
            if ch == "\\":
                if i + 1 >= len(self.raw):
                    break
                nxt = self.raw[i + 1]
                if nxt == "u":
                    if i + 6 > len(self.raw):
                        break
                    try:
                        out.append(chr(int(self.raw[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append({"n": "\n", "t": "\t", "r": "", "b": "", "f": ""}.get(nxt, nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        self.pos = i
        return "".join(out)


def stream(system: str, messages: list[dict], max_tokens: int = 400):
    """Stream the answer text from the local model. Yields text pieces, then a dict with Ollama's
    timings and the "sources" list of the JSON object."""
    body = {
        "model": LLM,
        "messages": [{"role": "system", "content": system}, *messages],
        "format": ANSWER_SCHEMA,
        "stream": True,
        "think": False,
        "keep_alive": KEEP,
        "options": {"temperature": 0.1, "num_predict": max_tokens, "num_ctx": 8192},
    }
    global _used
    _used = True
    dec = _JsonString()
    try:
        with _client.stream("POST", "/api/chat", json=body) as r:
            if r.status_code != 200:
                r.read()
                raise LocalError(f"Ollama error {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                piece = msg.get("message", {}).get("content")
                if piece:
                    text = dec.feed(piece)
                    if text:
                        yield text
                if msg.get("done"):
                    stats = {k: msg.get(k) for k in ("prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "load_duration")}
                    try:
                        stats["sources"] = [int(n) for n in json.loads(dec.raw).get("sources", [])]
                    except (json.JSONDecodeError, TypeError, ValueError):
                        stats["sources"] = []
                    yield stats
                    return
    except httpx.HTTPError as e:
        raise LocalError(f"Ollama is not reachable: {e}") from e
