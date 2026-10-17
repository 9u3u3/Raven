"""The Anthropic API through the official `anthropic` SDK (PLAN4 4).

JSON answers use structured outputs (`output_config.format`). The current models refuse a forced tool call and
the sampling settings (temperature), so neither is sent. Only providers.chat() and providers.stream() call
this module, with a sealed payload.
"""

from __future__ import annotations

import json

from ..cloud import CloudError

# Thinking can use part of the token budget, so the budget leaves room for it.
CHAT_TOKENS = 8000
STREAM_TOKENS = 4000


def _client(ctx: dict):
    import anthropic

    if not ctx["key"]:
        raise CloudError("No Anthropic API key")
    return anthropic.Anthropic(api_key=ctx["key"], max_retries=1, timeout=120)


def _error(e: Exception) -> CloudError:
    import anthropic

    if isinstance(e, anthropic.AuthenticationError):
        return CloudError("Anthropic: the key is not valid")
    if isinstance(e, anthropic.APIStatusError):
        return CloudError(f"Anthropic error {e.status_code}: {str(e.message)[:200]}")
    if isinstance(e, anthropic.APIConnectionError):
        return CloudError(f"Anthropic is not reachable: {e}")
    return CloudError(f"Anthropic: {e}"[:200])


def strict_schema(schema: dict) -> dict:
    """The schema in the form that structured outputs take: every object closed, no length or number limits."""
    drop = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
            "minItems", "maxItems", "pattern"}
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) for k, v in schema.items() if k not in drop}
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out.setdefault("properties", {})
    return out


def chat(ctx: dict, system: str, user: str, schema: dict) -> dict:
    import anthropic

    try:
        r = _client(ctx).messages.create(
            model=ctx["model"], max_tokens=CHAT_TOKENS, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": strict_schema(schema)}},
        )
    except anthropic.AnthropicError as e:
        raise _error(e) from e
    if r.stop_reason == "refusal":
        raise CloudError("Anthropic refused the request")
    if r.stop_reason == "max_tokens":
        raise CloudError("Anthropic stopped before the answer was complete")
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise CloudError("Anthropic did not return JSON") from e


def stream(ctx: dict, system: str, user: str, max_tokens: int):
    import anthropic

    try:
        with _client(ctx).messages.stream(
            model=ctx["model"], max_tokens=max(max_tokens, STREAM_TOKENS), system=system,
            messages=[{"role": "user", "content": user}],
        ) as s:
            yield from s.text_stream
    except anthropic.AnthropicError as e:
        raise _error(e) from e


def models(ctx: dict) -> list[dict]:
    import anthropic

    try:
        return [{"id": m.id, "free": False} for m in _client(ctx).models.list(limit=100)]
    except anthropic.AnthropicError as e:
        raise _error(e) from e
