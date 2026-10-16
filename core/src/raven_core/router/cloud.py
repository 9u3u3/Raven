"""Tier T3: cloud providers. This is the only module in the sidecar that opens
outbound connections (section 10.6). Every payload that reaches it went through the
redaction gate and the ledger first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

import httpx

from .. import db

GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]


class CloudError(Exception):
    pass


def groq_key() -> str | None:
    from .. import keys

    return os.environ.get("GROQ_API_KEY") or keys.get("groq_api_key")


def claude_ok() -> bool:
    return shutil.which("claude") is not None


def claude_env(config_dir: str | None = None) -> dict:
    """The environment for the claude CLI. `claude_config_dir` picks the account: empty means the default
    account (no CLAUDE_CONFIG_DIR, config in ~/.claude.json), a path means that folder (CLAUDE_CONFIG_DIR).
    The variables of a running Claude Code session are removed, so a Raven started from one still works."""
    env = {k: v for k, v in os.environ.items() if not (k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_"))}
    d = (config_dir if config_dir is not None else db.get_setting("claude_config_dir")) or ""
    env.pop("CLAUDE_CONFIG_DIR", None)
    if d:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(d)
    else:
        fallback = _owner_claude_dir()
        if fallback:
            env["CLAUDE_CONFIG_DIR"] = fallback
    return env


def _owner_claude_dir() -> str | None:
    """Raven can run with HOME set to another folder (a demo home). The claude CLI then finds no sign-in there.
    Use the sign-in in the real home folder of the user (from the password database) when HOME has none."""
    if os.name != "posix":
        return None
    home = os.path.expanduser("~")
    if os.path.exists(os.path.join(home, ".claude", ".credentials.json")):
        return None
    try:
        import pwd

        real = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):
        return None
    cand = os.path.join(real, ".claude")
    if os.path.realpath(real) != os.path.realpath(home) and os.path.exists(os.path.join(cand, ".credentials.json")):
        return cand
    return None


def claude_status(config_dir: str | None = None) -> dict:
    """`claude auth status` for one account: signed in or not, the email. It makes no model call."""
    if not claude_ok():
        return {"logged_in": False, "detail": "The claude CLI is not installed"}
    try:
        r = subprocess.run(["claude", "auth", "status"], env=claude_env(config_dir), capture_output=True, timeout=20)
        out = json.loads(r.stdout.decode(errors="replace") or "{}")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {"logged_in": False, "detail": "claude auth status failed"}
    return {"logged_in": bool(out.get("loggedIn")), "email": out.get("email") or "", "method": out.get("authMethod") or "",
            "detail": "signed in" if out.get("loggedIn") else "not signed in: run claude auth login"}


def claude_accounts() -> list[dict]:
    """The default account and each ~/.claude-* folder that holds a Claude config."""
    home = os.path.expanduser("~")
    dirs = [""]
    homes = [(home, "~/")]
    try:
        import pwd

        real = pwd.getpwuid(os.getuid()).pw_dir
        if os.path.realpath(real) != os.path.realpath(home):  # HOME is a demo folder: list the real accounts too
            homes.append((real, real.rstrip("/") + "/"))
    except (ImportError, KeyError):
        pass
    for base, prefix in homes:
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if name.startswith(".claude-") and os.path.isdir(p) and any(
                    os.path.exists(os.path.join(p, f)) for f in (".claude.json", ".credentials.json")):
                dirs.append(prefix + name)
    cur = db.get_setting("claude_config_dir") or ""
    if cur and cur not in dirs:
        dirs.append(cur)
    return [{"dir": d, "label": d or "Default account (~/.claude.json)", "selected": d == cur, **claude_status(d)} for d in dirs]


def groq_models() -> list[str]:
    key = groq_key()
    if not key:
        return []
    r = httpx.get(f"{GROQ_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=8)
    r.raise_for_status()
    # Chat models only: no speech, voice or prompt-guard models.
    skip = ("whisper", "orpheus", "guard", "tts", "playai")
    return sorted(m["id"] for m in r.json().get("data", []) if m.get("active", True) and not any(s in m["id"] for s in skip))


def groq_model() -> str:
    m = db.get_setting("groq_model")
    if m:
        return m
    try:
        have = set(groq_models())
        for p in GROQ_PREFERRED:
            if p in have:
                return p
        if have:
            return sorted(have)[0]
    except Exception:  # noqa: BLE001
        pass
    return GROQ_PREFERRED[0]


def groq_chat(system: str, user: str, schema: dict) -> tuple[dict, str]:
    """Return (parsed JSON, model). Groq: interactive T3 tasks where speed matters (section 4.3)."""
    key = groq_key()
    if not key:
        raise CloudError("No Groq API key")
    model = groq_model()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system + "\nAnswer with JSON that matches this schema:\n" + json.dumps(schema)},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_completion_tokens": 1500,
    }
    try:
        r = httpx.post(f"{GROQ_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
    except httpx.HTTPError as e:
        raise CloudError(f"Groq is not reachable: {e}") from e
    if r.status_code != 200:
        raise CloudError(f"Groq error {r.status_code}: {r.text[:200]}")
    content = r.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content), model
    except json.JSONDecodeError as e:
        raise CloudError("Groq did not return JSON") from e


def claude_p(system: str, user: str, schema: dict) -> tuple[dict, str]:
    """`claude -p` with the safe flags from section 10.5: no tools, no MCP, no session,
    no settings, in an empty temporary folder. The prompt comes in on stdin."""
    if not claude_ok():
        raise CloudError("The claude CLI is not installed")
    model = db.get_setting("claude_model") or "sonnet"
    cmd = [
        "claude", "-p",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--setting-sources", "",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", model,
        "--system-prompt", system,
    ]
    with tempfile.TemporaryDirectory(prefix="raven-claude-") as tmp:
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=user.encode(), cwd=tmp, capture_output=True, timeout=180, env=claude_env())
        except subprocess.TimeoutExpired as e:
            raise CloudError("claude -p timed out") from e
    if r.returncode != 0:
        raise CloudError(f"claude -p failed: {(r.stderr or r.stdout).decode(errors='replace')[:200]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise CloudError("claude -p did not return JSON") from e
    if out.get("is_error"):
        raise CloudError(f"claude -p error: {str(out.get('result'))[:200]}")
    data = out.get("structured_output")
    if data is None:
        try:
            data = json.loads(out.get("result") or "")
        except json.JSONDecodeError as e:
            raise CloudError("claude -p answer is not JSON") from e
    _ = time.time() - t0
    return data, f"claude -p · {model}"


def groq_stream(system: str, user: str, max_tokens: int = 900):
    """Stream plain text from Groq. Yields text pieces. The first value is the model name."""
    key = groq_key()
    if not key:
        raise CloudError("No Groq API key")
    model = groq_model()
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
        "stream": True,
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"
    try:
        with httpx.stream("POST", f"{GROQ_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=httpx.Timeout(60, connect=5)) as r:
            if r.status_code != 200:
                r.read()
                raise CloudError(f"Groq error {r.status_code}: {r.text[:200]}")
            yield model
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta
    except httpx.HTTPError as e:
        raise CloudError(f"Groq is not reachable: {e}") from e


def claude_stream(system: str, user: str, max_tokens: int = 900):
    """`claude -p` gives the whole answer at the end. Yields the model name, then the text."""
    _ = max_tokens
    if not claude_ok():
        raise CloudError("The claude CLI is not installed")
    model = db.get_setting("claude_model") or "sonnet"
    cmd = [
        "claude", "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
        "--setting-sources", "", "--output-format", "json", "--model", model, "--system-prompt", system,
    ]
    yield f"claude -p · {model}"
    with tempfile.TemporaryDirectory(prefix="raven-claude-") as tmp:
        try:
            r = subprocess.run(cmd, input=user.encode(), cwd=tmp, capture_output=True, timeout=180, env=claude_env())
        except subprocess.TimeoutExpired as e:
            raise CloudError("claude -p timed out") from e
    if r.returncode != 0:
        raise CloudError(f"claude -p failed: {(r.stderr or r.stdout).decode(errors='replace')[:200]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise CloudError("claude -p did not return JSON") from e
    if out.get("is_error"):
        raise CloudError(f"claude -p error: {str(out.get('result'))[:200]}")
    yield str(out.get("result") or "")


ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_DEFAULT = "claude-sonnet-5-5"


def anthropic_key() -> str | None:
    from .. import keys

    return os.environ.get("ANTHROPIC_API_KEY") or keys.get("anthropic_api_key")


def _anthropic_headers(key: str) -> dict:
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}


def anthropic_chat(system: str, user: str, schema: dict) -> tuple[dict, str]:
    """The Anthropic API with an API key (no claude CLI needed). A forced tool call gives JSON for the schema."""
    key = anthropic_key()
    if not key:
        raise CloudError("No Anthropic API key")
    model = db.get_setting("anthropic_model") or ANTHROPIC_DEFAULT
    body = {
        "model": model, "max_tokens": 2000, "temperature": 0.2, "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"name": "answer", "description": "The answer in the required shape", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "answer"},
    }
    try:
        r = httpx.post(f"{ANTHROPIC_URL}/messages", headers=_anthropic_headers(key), json=body, timeout=90)
    except httpx.HTTPError as e:
        raise CloudError(f"Anthropic is not reachable: {e}") from e
    if r.status_code != 200:
        raise CloudError(f"Anthropic error {r.status_code}: {r.text[:200]}")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input") or {}, f"Anthropic · {model}"
    raise CloudError("Anthropic did not return the JSON answer")


def anthropic_stream(system: str, user: str, max_tokens: int = 900):
    """Stream plain text from the Anthropic API. The first value is the model name."""
    key = anthropic_key()
    if not key:
        raise CloudError("No Anthropic API key")
    model = db.get_setting("anthropic_model") or ANTHROPIC_DEFAULT
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2, "system": system,
            "messages": [{"role": "user", "content": user}], "stream": True}
    try:
        with httpx.stream("POST", f"{ANTHROPIC_URL}/messages", headers=_anthropic_headers(key), json=body,
                          timeout=httpx.Timeout(90, connect=5)) as r:
            if r.status_code != 200:
                r.read()
                raise CloudError(f"Anthropic error {r.status_code}: {r.text[:200]}")
            yield f"Anthropic · {model}"
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    yield ev["delta"].get("text", "")
                elif ev.get("type") == "error":
                    raise CloudError(f"Anthropic error: {str(ev.get('error'))[:200]}")
    except httpx.HTTPError as e:
        raise CloudError(f"Anthropic is not reachable: {e}") from e


def test_key(provider: str) -> dict:
    """Check a stored key with one small request that sends no file data. Returns {ok, detail}."""
    try:
        if provider == "groq":
            if not groq_key():
                return {"ok": False, "detail": "No key"}
            n = len(groq_models())
            return {"ok": n > 0, "detail": f"{n} models"}
        if provider == "anthropic":
            key = anthropic_key()
            if not key:
                return {"ok": False, "detail": "No key"}
            r = httpx.get(f"{ANTHROPIC_URL}/models", headers=_anthropic_headers(key), timeout=10)
            if r.status_code == 200:
                return {"ok": True, "detail": f"{len(r.json().get('data', []))} models"}
            return {"ok": False, "detail": "The key is not valid" if r.status_code in (401, 403) else f"Error {r.status_code}"}
        if provider == "claude":
            st = claude_status()
            return {"ok": st["logged_in"], "detail": (f"signed in as {st['email']}" if st.get("email") else st["detail"]) if st["logged_in"] else st["detail"]}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "detail": "The key is not valid" if e.response.status_code in (401, 403) else f"Error {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"Not reachable: {e}"[:160]}
    return {"ok": False, "detail": f"Unknown provider {provider}"}


PROVIDERS = {"groq": groq_chat, "claude": claude_p, "anthropic": anthropic_chat}
STREAMS = {"groq": groq_stream, "claude": claude_stream, "anthropic": anthropic_stream}


def configured() -> dict[str, bool]:
    return {"groq": bool(groq_key()), "claude": claude_ok(), "anthropic": bool(anthropic_key())}


_online = {"at": 0.0, "ok": True}


def online() -> bool:
    """Can this computer reach the cloud? A TCP connect to the Groq API, no data sent. Cached for 30 s.

    Section 12.7: offline, the mode line says LOCAL ONLY (offline) and heavy tasks run on the local model.
    """
    import socket
    import time as _t

    now = _t.monotonic()
    if now - _online["at"] < 30:
        return _online["ok"]
    try:
        socket.create_connection(("api.groq.com", 443), timeout=1.5).close()
        ok = True
    except OSError:
        ok = False
    _online.update(at=now, ok=ok)
    return ok
