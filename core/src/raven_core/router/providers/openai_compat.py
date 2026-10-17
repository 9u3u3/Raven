"""Any service with the OpenAI chat API (PLAN4 4): OpenAI, Groq, OpenRouter, and a custom server such as LM Studio,
vLLM or Ollama on another computer. Plain HTTP with httpx. Only providers.chat() and providers.stream() call
this module, with a sealed payload."""

from __future__ import annotations

import json
import re

import httpx

from ..cloud import CloudError

CHAT_TOKENS = 4000


def _headers(ctx: dict) -> dict:
    h = dict(ctx["headers"])
    if ctx["key"]:
        h["Authorization"] = f"Bearer {ctx['key']}"
    return h


def _body(ctx: dict, system: str, user: str, max_tokens: int) -> dict:
    body = {"model": ctx["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            ctx["max_tokens_field"]: max_tokens}
    if not ctx["no_temperature"]:
        body["temperature"] = 0.2
    m = ctx["model"] or ""
    # Reasoning models think before they answer. Low effort keeps the answer fast.
    if m.startswith("openai/gpt-oss") or (ctx["preset"] == "openai" and re.match(r"^(gpt-5|o\d)", m)):
        body["reasoning_effort"] = "low"
    return body


def _fail(ctx: dict, r: httpx.Response) -> CloudError:
    if r.status_code in (401, 403):
        return CloudError(f"{ctx['name']}: the key is not valid")
    return CloudError(f"{ctx['name']} error {r.status_code}: {r.text[:200]}")


def _json(text: str) -> dict:
    """The JSON object in an answer. Some models put it in a ``` block or add words around it."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def chat(ctx: dict, system: str, user: str, schema: dict) -> dict:
    sys = system + "\nAnswer with JSON that matches this schema:\n" + json.dumps(schema)
    body = _body(ctx, sys, user, CHAT_TOKENS)
    body["response_format"] = {"type": "json_object"}
    url = f"{ctx['base_url']}/chat/completions"
    try:
        r = httpx.post(url, headers=_headers(ctx), json=body, timeout=90)
        if r.status_code == 400 and "response_format" in r.text:
            body.pop("response_format")  # a server without JSON mode: the prompt still asks for JSON
            r = httpx.post(url, headers=_headers(ctx), json=body, timeout=90)
    except httpx.HTTPError as e:
        raise CloudError(f"{ctx['name']} is not reachable: {e}") from e
    if r.status_code != 200:
        raise _fail(ctx, r)
    try:
        content = r.json()["choices"][0]["message"]["content"] or ""
        return _json(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise CloudError(f"{ctx['name']} did not return JSON") from e


def stream(ctx: dict, system: str, user: str, max_tokens: int):
    body = _body(ctx, system, user, max_tokens)
    body["stream"] = True
    try:
        with httpx.stream("POST", f"{ctx['base_url']}/chat/completions", headers=_headers(ctx), json=body,
                          timeout=httpx.Timeout(90, connect=5)) as r:
            if r.status_code != 200:
                r.read()
                raise _fail(ctx, r)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if ev.get("error"):
                    raise CloudError(f"{ctx['name']} error: {str(ev['error'])[:200]}")
                try:
                    delta = ev["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta
    except httpx.HTTPError as e:
        raise CloudError(f"{ctx['name']} is not reachable: {e}") from e


def _free(m: dict) -> bool:
    if str(m.get("id", "")).endswith(":free"):
        return True
    p = m.get("pricing") or {}
    return str(p.get("prompt")) == "0" and str(p.get("completion")) == "0"


def models(ctx: dict) -> list[dict]:
    try:
        if ctx["preset"] == "openrouter" and ctx["key"]:
            # The model list needs no key on OpenRouter, so check the key on its own.
            k = httpx.get(f"{ctx['base_url']}/key", headers=_headers(ctx), timeout=10)
            if k.status_code != 200:
                raise _fail(ctx, k)
        r = httpx.get(f"{ctx['base_url']}/models", headers=_headers(ctx), timeout=10)
    except httpx.HTTPError as e:
        raise CloudError(f"{ctx['name']} is not reachable: {e}") from e
    if r.status_code != 200:
        raise _fail(ctx, r)
    out = []
    for m in r.json().get("data", []):
        mid = str(m.get("id", ""))
        if not mid or not m.get("active", True) or any(s in mid for s in ctx["skip"]):
            continue
        arch = m.get("architecture") or {}
        if arch.get("output_modalities") and "text" not in arch["output_modalities"]:
            continue
        out.append({"id": mid, "free": _free(m)})
    return sorted(out, key=lambda m: (not m["free"], m["id"])) if ctx["preset"] == "openrouter" else sorted(out, key=lambda m: m["id"])
