"""Google Gemini through the official `google-genai` SDK (PLAN4 4). Only providers.chat() and
providers.stream() call this module, with a sealed payload."""

from __future__ import annotations

import json

from ..cloud import CloudError

CHAT_TOKENS = 8000


def _client(ctx: dict):
    from google import genai
    from google.genai import types

    if not ctx["key"]:
        raise CloudError("No Gemini API key")
    return genai.Client(api_key=ctx["key"], http_options=types.HttpOptions(timeout=120_000))


def _error(e: Exception) -> CloudError:
    from google.genai import errors

    if isinstance(e, errors.APIError):
        if e.code in (400, 401, 403) and "key" in str(e.message or "").lower():
            return CloudError("Gemini: the key is not valid")
        return CloudError(f"Gemini error {e.code}: {str(e.message)[:200]}")
    return CloudError(f"Gemini is not reachable: {e}"[:200])


def _config(system: str, max_tokens: int, schema: dict | None = None):
    from google.genai import types

    extra = {"response_mime_type": "application/json", "response_json_schema": schema} if schema else {}
    return types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens, temperature=0.2, **extra)


def chat(ctx: dict, system: str, user: str, schema: dict) -> dict:
    import httpx
    from google.genai import errors

    try:
        r = _client(ctx).models.generate_content(model=ctx["model"], contents=user, config=_config(system, CHAT_TOKENS, schema))
    except (errors.APIError, httpx.HTTPError) as e:
        raise _error(e) from e
    try:
        return json.loads(r.text or "")
    except json.JSONDecodeError as e:
        raise CloudError("Gemini did not return JSON") from e


def stream(ctx: dict, system: str, user: str, max_tokens: int):
    import httpx
    from google.genai import errors

    try:
        for chunk in _client(ctx).models.generate_content_stream(model=ctx["model"], contents=user,
                                                                  config=_config(system, max(max_tokens, 2000))):
            if chunk.text:
                yield chunk.text
    except (errors.APIError, httpx.HTTPError) as e:
        raise _error(e) from e


def models(ctx: dict) -> list[dict]:
    import httpx
    from google.genai import errors

    try:
        out = []
        for m in _client(ctx).models.list():
            name = (m.name or "").removeprefix("models/")
            if "generateContent" in (m.supported_actions or []) and name.startswith("gemini") and "embedding" not in name \
                    and "image" not in name and "tts" not in name:
                out.append({"id": name, "free": False})
        return out
    except (errors.APIError, httpx.HTTPError) as e:
        raise _error(e) from e
