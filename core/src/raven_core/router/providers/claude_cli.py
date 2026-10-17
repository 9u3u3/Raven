"""`claude -p` (development build only, PLAN4 B1). Only providers.chat() and providers.stream() call this module,
with a sealed payload."""

from __future__ import annotations

from .. import cloud


def chat(ctx: dict, system: str, user: str, schema: dict) -> dict:
    data, _ = cloud.claude_p(system, user, schema, ctx["model"])
    return data


def stream(ctx: dict, system: str, user: str, max_tokens: int):
    gen = cloud.claude_stream(system, user, max_tokens, ctx["model"])
    next(gen)  # the model name: providers.stream() gives its own
    yield from gen


def models(ctx: dict) -> list[dict]:
    return [{"id": m, "free": False} for m in ("sonnet", "opus", "haiku")]
