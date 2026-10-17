"""Tier T3: the cloud error type, the online check and `claude -p` (development build only).

The providers themselves live in router/providers (PLAN4 4). This package and router/providers are the only
code in the sidecar that opens outbound connections to an AI service (section 10.6). Every payload that
reaches them went through the redaction gate and the ledger first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

from .. import config, db

class CloudError(Exception):
    pass


def claude_ok() -> bool:
    """`claude -p` runs with the user's claude.ai login. Anthropic does not allow a third-party product to offer
    that login without approval, so a release build never uses it (PLAN4 B1). The API key path stays."""
    return config.DEV and shutil.which("claude") is not None


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
    """The default account and each ~/.claude-* folder that holds a Claude config. None in a release build."""
    if not config.DEV:
        return []
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


def claude_p(system: str, user: str, schema: dict, model: str = "sonnet") -> tuple[dict, str]:
    """`claude -p` with the safe flags from section 10.5: no tools, no MCP, no session,
    no settings, in an empty temporary folder. The prompt comes in on stdin."""
    if not claude_ok():
        raise CloudError("The claude CLI is not installed")
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


def claude_stream(system: str, user: str, max_tokens: int = 900, model: str = "sonnet"):
    """`claude -p` with stream-json output (PLAN3 P1-8). Yields the model name, then each piece of text as it
    comes. The safe flags are the same as in claude_p()."""
    _ = max_tokens
    if not claude_ok():
        raise CloudError("The claude CLI is not installed")
    cmd = [
        "claude", "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
        "--setting-sources", "", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--model", model, "--system-prompt", system,
    ]
    yield f"claude -p · {model}"
    with tempfile.TemporaryDirectory(prefix="raven-claude-") as tmp:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp,
                                env=claude_env())
        timed_out = threading.Event()
        timer = threading.Timer(180, lambda: (timed_out.set(), proc.kill()))
        timer.start()
        streamed, final, error = False, "", None
        try:
            proc.stdin.write(user.encode())
            proc.stdin.close()
            for raw in proc.stdout:
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = e.get("type")
                if kind == "stream_event":
                    ev = e.get("event") or {}
                    delta = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
                        streamed = True
                        yield delta["text"]
                elif kind == "result":
                    final = str(e.get("result") or "")
                    if e.get("is_error"):
                        error = final[:200] or "unknown error"
            proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    if timed_out.is_set():
        raise CloudError("claude -p timed out")
    if error:
        raise CloudError(f"claude -p error: {error}")
    if proc.returncode != 0 and not (streamed or final):
        raise CloudError(f"claude -p failed: {proc.stderr.read().decode(errors='replace')[:200]}")
    if not streamed and final:
        yield final  # an older claude without partial messages: the whole answer at the end


def configured() -> dict[str, bool]:
    from . import providers

    return providers.configured()


_online = {"at": 0.0, "ok": True}


def online() -> bool:
    """Can this computer reach the cloud? A TCP connect to the first provider, no data sent. Cached for 30 s.

    Section 12.7: offline, the mode line says LOCAL ONLY (offline) and heavy tasks run on the local model.
    """
    import socket
    import time as _t

    now = _t.monotonic()
    if now - _online["at"] < 30:
        return _online["ok"]
    try:
        from . import providers

        # The first provider that answers counts: a local server that is off does not make the computer offline.
        ends = [e for e in map(providers.endpoint, providers.order()) if e][:3] or [("api.groq.com", 443)]
        ok = False
        for host, port in ends:
            try:
                socket.create_connection((host, port), timeout=1.5).close()
                ok = True
                break
            except OSError:
                continue
    except OSError:
        ok = False
    _online.update(at=now, ok=ok)
    return ok
