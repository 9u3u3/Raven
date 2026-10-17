"""Cloud AI providers (PLAN4 section 4): one registry, one adapter for each API style.

Adapters:
 1. `anthropic`: the official `anthropic` SDK.
 2. `openai`: any service with the OpenAI chat API (OpenAI, Groq, OpenRouter, a custom server).
 3. `gemini`: the official `google-genai` SDK.
 4. `claude_cli`: `claude -p`, in a development build only (PLAN4 B1).

The user keeps a list of provider rows in their own order (the setting "cloud_providers"). Each row has an id,
a preset, "on" or "off", a model and, for a custom server, a base URL. Its key lives in the keyring as
"<id>_api_key" (keys.py). The first row that works answers.

chat() and stream() are the only way to call an adapter, and both take only a gate.Sealed payload: text that
went through the redaction gate and its final check. A test checks this rule.
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlparse

from ... import config, db
from ...privacy.gate import Sealed
from ..cloud import CloudError

log = logging.getLogger("raven.providers")

# Free tiers (owner request, 2026-10-17). A free tier can keep the prompts or use them for training, so the UI
# says so next to the provider. Raven only ever sends redacted text.
PRESETS: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic", "adapter": "anthropic", "base_url": "https://api.anthropic.com",
        "prefer": ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"], "key_hint": "sk-ant-…",
        "key_url": "https://console.anthropic.com/settings/keys", "free": "",
    },
    "openai": {
        "name": "OpenAI", "adapter": "openai", "base_url": "https://api.openai.com/v1",
        "prefer": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"], "key_hint": "sk-…",
        "key_url": "https://platform.openai.com/api-keys", "free": "",
        "skip": ("embedding", "whisper", "tts", "dall-e", "image", "audio", "realtime", "moderation", "transcribe",
                 "search", "davinci", "babbage", "codex", "computer-use", "sora"),
        "no_temperature": True,
    },
    "groq": {
        "name": "Groq", "adapter": "openai", "base_url": "https://api.groq.com/openai/v1",
        "prefer": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
        "key_hint": "gsk_…", "key_url": "https://console.groq.com/keys",
        "free": "Free plan with rate limits.",
        "skip": ("whisper", "orpheus", "guard", "tts", "playai", "compound"),
    },
    "openrouter": {
        "name": "OpenRouter", "adapter": "openai", "base_url": "https://openrouter.ai/api/v1",
        "prefer": [], "key_hint": "sk-or-…", "key_url": "https://openrouter.ai/keys",
        "free": "Models that end in :free cost nothing. Their hosts can keep the prompts.",
        "headers": {"X-Title": "Raven"},
        "max_tokens_field": "max_tokens",
    },
    "gemini": {
        "name": "Google Gemini", "adapter": "gemini", "base_url": "https://generativelanguage.googleapis.com",
        "prefer": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"], "key_hint": "AIza…",
        "key_url": "https://aistudio.google.com/apikey",
        "free": "Free plan with rate limits. Google can use free-plan prompts to improve its products.",
    },
    "custom": {
        "name": "Custom OpenAI-compatible", "adapter": "openai", "base_url": "", "prefer": [], "key_hint": "optional",
        "key_url": "", "free": "", "custom": True,
    },
    "claude": {
        "name": "claude -p (development build)", "adapter": "claude_cli", "base_url": "", "prefer": ["sonnet", "opus", "haiku"],
        "key_hint": "", "key_url": "", "free": "", "dev": True,
    },
}

_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class ProviderError(ValueError):
    pass


def presets() -> dict[str, dict]:
    return {k: v for k, v in PRESETS.items() if config.DEV or not v.get("dev")}


def key_name(pid: str) -> str:
    return f"{pid}_api_key"


def key(pid: str) -> str | None:
    from ... import keys

    env = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
           "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(pid)
    return (os.environ.get(env) if env else None) or keys.get(key_name(pid))


# ---------------------------------------------------------------- rows


def _legacy_rows() -> list[dict]:
    """The rows for a settings table from before the registry: Groq and Anthropic keys, the chosen provider."""
    have = [p for p in ("groq", "anthropic") if key(p)]
    if config.DEV and db.get_setting("cloud_provider") == "claude":
        have.insert(0, "claude")
    first = db.get_setting("cloud_provider")
    if first in have:
        have.remove(first)
        have.insert(0, first)
    models = {"groq": db.get_setting("groq_model"), "anthropic": db.get_setting("anthropic_model"),
              "claude": db.get_setting("claude_model")}
    return [{"id": p, "preset": p, "on": True, "model": models.get(p) or None} for p in have]


def rows() -> list[dict]:
    """The provider rows in the user's order. A release build leaves out the development rows."""
    raw = db.get_setting("cloud_providers")
    if raw is None:
        raw = _legacy_rows()
        if raw:
            db.set_setting("cloud_providers", raw)
    out = []
    for r in raw if isinstance(raw, list) else []:
        p = PRESETS.get(r.get("preset"))
        if not p or (p.get("dev") and not config.DEV):
            continue
        out.append({"id": r["id"], "preset": r["preset"], "on": bool(r.get("on", True)), "model": r.get("model") or None,
                    "base_url": (r.get("base_url") or None) if p.get("custom") else None, "name": r.get("name") or None})
    return out


def _save(rs: list[dict]) -> None:
    db.set_setting("cloud_providers", rs)
    _models_cache.clear()


def _row(pid: str) -> dict:
    for r in rows():
        if r["id"] == pid:
            return r
    raise ProviderError(f"No provider {pid}")


def _check_url(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ProviderError("The base URL must start with https:// (or http:// for a server on this network)")
    return url.strip().rstrip("/")


def add(preset: str, base_url: str | None = None, name: str | None = None) -> dict:
    p = presets().get(preset)
    if not p:
        raise ProviderError(f"Unknown provider type: {preset}")
    rs = rows()
    if p.get("custom"):
        n = 1
        while f"custom{n}" in {r["id"] for r in rs}:
            n += 1
        row = {"id": f"custom{n}", "preset": preset, "on": True, "model": None, "base_url": _check_url(base_url or ""),
               "name": (name or "").strip()[:40] or None}
    else:
        if preset in {r["id"] for r in rs}:
            raise ProviderError(f"{p['name']} is in the list already")
        row = {"id": preset, "preset": preset, "on": True, "model": None, "base_url": None, "name": None}
    rs.append(row)
    _save(rs)
    return row


def update(pid: str, **changes) -> dict:
    rs = rows()
    for r in rs:
        if r["id"] != pid:
            continue
        if "on" in changes and changes["on"] is not None:
            r["on"] = bool(changes["on"])
        if "model" in changes:
            m = changes["model"]
            r["model"] = (m.strip()[:120] or None) if isinstance(m, str) else None
        if changes.get("base_url") is not None and PRESETS[r["preset"]].get("custom"):
            r["base_url"] = _check_url(changes["base_url"])
        if changes.get("name") is not None and PRESETS[r["preset"]].get("custom"):
            r["name"] = changes["name"].strip()[:40] or None
        _save(rs)
        return r
    raise ProviderError(f"No provider {pid}")


def remove(pid: str) -> None:
    from ... import keys

    rs = [r for r in rows() if r["id"] != pid]
    _save(rs)
    keys.put(key_name(pid), None)


def reorder(ids: list[str]) -> list[dict]:
    rs = rows()
    by = {r["id"]: r for r in rs}
    if sorted(ids) != sorted(by):
        raise ProviderError("The order must list each provider once")
    _save([by[i] for i in ids])
    return rows()


def set_key(pid: str, value: str | None) -> str:
    from ... import keys

    _row(pid)
    return keys.put(key_name(pid), (value or "").strip() or None)


def ready(row: dict) -> bool:
    """On, with what it needs to run: a key, or a base URL for a custom server, or the claude CLI."""
    if not row["on"]:
        return False
    p = PRESETS[row["preset"]]
    if p["adapter"] == "claude_cli":
        from .. import cloud

        return cloud.claude_ok()
    if p.get("custom"):
        return bool(row["base_url"])
    return bool(key(row["id"]))


def order() -> list[str]:
    """The ids of the providers that can run, in the user's order."""
    return [r["id"] for r in rows() if ready(r)]


def configured() -> dict[str, bool]:
    return {r["id"]: ready(r) for r in rows()}


def label(pid: str) -> str:
    try:
        r = _row(pid)
    except ProviderError:
        return pid
    return r["name"] or PRESETS[r["preset"]]["name"]


def endpoint(pid: str) -> tuple[str, int] | None:
    """The host and port of a provider, for the online check. None for the claude CLI."""
    r = _row(pid)
    u = urlparse(r["base_url"] or PRESETS[r["preset"]]["base_url"])
    if not u.hostname:
        return None
    return u.hostname, u.port or (80 if u.scheme == "http" else 443)


# ---------------------------------------------------------------- models

_models_cache: dict[str, tuple[float, list[dict]]] = {}


def models(pid: str, fresh: bool = False) -> list[dict]:
    """The chat models of a provider: [{id, free}]. Cached for 10 minutes. It sends no file data."""
    hit = _models_cache.get(pid)
    if hit and not fresh and time.monotonic() - hit[0] < 600:
        return hit[1]
    r = _row(pid)
    out = _adapter(r).models(_ctx(r))
    _models_cache[pid] = (time.monotonic(), out)
    return out


def model_for(row: dict) -> str:
    """The row's model, or the first preferred model the provider has, or its first model."""
    if row["model"]:
        return row["model"]
    p = PRESETS[row["preset"]]
    try:
        have = [m["id"] for m in models(row["id"])]
    except Exception:  # noqa: BLE001  (no list: use the first preferred name)
        have = []
    for m in p["prefer"]:
        if m in have:
            return m
    if row["preset"] == "openrouter":
        free = [m for m in have if m.endswith(":free")]
        if free:
            return free[0]
    if have:
        return have[0]
    if p["prefer"]:
        return p["prefer"][0]
    raise CloudError(f"{label(row['id'])}: pick a model in Settings, AI providers")


# ---------------------------------------------------------------- calls


def _ctx(row: dict, model: str | None = None) -> dict:
    p = PRESETS[row["preset"]]
    return {"id": row["id"], "preset": row["preset"], "name": row["name"] or p["name"], "key": key(row["id"]),
            "base_url": row["base_url"] or p["base_url"], "model": model, "headers": p.get("headers") or {},
            "skip": p.get("skip") or (), "no_temperature": p.get("no_temperature", False),
            "max_tokens_field": p.get("max_tokens_field", "max_completion_tokens")}


def _adapter(row: dict):
    kind = PRESETS[row["preset"]]["adapter"]
    if kind == "anthropic":
        from . import anthropic_adapter as a
    elif kind == "openai":
        from . import openai_compat as a
    elif kind == "gemini":
        from . import gemini as a
    else:
        from . import claude_cli as a
    return a


def _require_sealed(user) -> None:
    if not isinstance(user, Sealed):
        raise CloudError("refused: the payload did not pass the redaction gate")


def chat(pid: str, system: str, user: Sealed, schema: dict) -> tuple[dict, str]:
    """One JSON answer for the schema. Returns (data, "Provider · model")."""
    _require_sealed(user)
    row = _row(pid)
    if not ready(row):
        raise CloudError(f"{label(pid)} is off or has no key")
    model = model_for(row)
    data = _adapter(row).chat(_ctx(row, model), system, str(user), schema)
    return data, f"{label(pid)} · {model}"


def stream(pid: str, system: str, user: Sealed, max_tokens: int = 900):
    """Plain text pieces. The first value is "Provider · model"."""
    _require_sealed(user)
    row = _row(pid)
    if not ready(row):
        raise CloudError(f"{label(pid)} is off or has no key")
    model = model_for(row)
    gen = _adapter(row).stream(_ctx(row, model), system, str(user), max_tokens)
    yield f"{label(pid)} · {model}"
    yield from gen


def test(pid: str) -> dict:
    """Check a provider with its model list (no file data, no model call). Returns {ok, detail, models}."""
    try:
        row = _row(pid)
    except ProviderError as e:
        return {"ok": False, "detail": str(e)}
    p = PRESETS[row["preset"]]
    if p["adapter"] == "claude_cli":
        from .. import cloud

        st = cloud.claude_status()
        return {"ok": st["logged_in"], "detail": f"signed in as {st['email']}" if st.get("email") else st["detail"]}
    if not p.get("custom") and not key(pid):
        return {"ok": False, "detail": "No key"}
    try:
        ms = models(pid, fresh=True)
    except CloudError as e:
        return {"ok": False, "detail": str(e)[:200]}
    free = sum(1 for m in ms if m.get("free"))
    detail = f"{len(ms)} models" + (f", {free} free" if free else "")
    return {"ok": bool(ms), "detail": detail if ms else "The provider lists no chat models", "models": len(ms)}


def public() -> list[dict]:
    """The rows for Settings: no key, only its last 4 characters and where it is stored."""
    from ... import keys

    out = []
    for r in rows():
        p = PRESETS[r["preset"]]
        k = key(r["id"])
        out.append({**r, "label": r["name"] or p["name"], "adapter": p["adapter"], "ready": ready(r),
                    "key": ("…" + k[-4:]) if k else "", "key_hint": p["key_hint"], "key_url": p["key_url"],
                    "free": p["free"], "base_url": r["base_url"] or p["base_url"],
                    "needs_key": not p.get("custom") and p["adapter"] != "claude_cli", "store": keys.store()})
    return out
