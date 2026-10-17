"""Add Raven to an MCP client with one button (PLAN4 4, R4 integrations).

Each client keeps its MCP servers in a JSON file. add() puts the "raven" entry from /mcp/config into that file
and keeps every other entry. Before the first change, Raven writes a copy of the file next to it
(<name>.raven-backup). A file that is not valid JSON stays as it is, and add() says so. Claude Code has a
command for this (`claude mcp add-json`), so Raven runs the command and does not touch its file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _app_dir() -> Path:
    """The folder where desktop apps keep their settings on this system."""
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")


def clients() -> list[dict]:
    """id, name, the config file, the key that holds the servers, and the entry shape."""
    return [
        {"id": "claude_desktop", "name": "Claude Desktop", "file": _app_dir() / "Claude" / "claude_desktop_config.json",
         "key": "mcpServers", "vscode": False},
        {"id": "cursor", "name": "Cursor", "file": _home() / ".cursor" / "mcp.json", "key": "mcpServers", "vscode": False},
        {"id": "windsurf", "name": "Windsurf", "file": _home() / ".codeium" / "windsurf" / "mcp_config.json",
         "key": "mcpServers", "vscode": False},
        {"id": "vscode", "name": "VS Code", "file": _app_dir() / "Code" / "User" / "mcp.json", "key": "servers", "vscode": True},
        {"id": "claude_code", "name": "Claude Code", "file": None, "key": None, "vscode": False},
    ]


class ClientError(ValueError):
    pass


def _entry(base: dict, vscode: bool) -> dict:
    e = dict(base)
    if vscode:
        e = {"type": "stdio", **e}
    return e


def _read(f: Path) -> dict:
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as e:
        raise ClientError(f"{f.name} is not valid JSON, so Raven does not change it. Fix the file or add Raven by hand.") from e
    if not isinstance(data, dict):
        raise ClientError(f"{f.name} does not hold a JSON object, so Raven does not change it.")
    return data


def _write(f: Path, data: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    backup = f.with_name(f.name + ".raven-backup")
    if f.exists() and not backup.exists():
        shutil.copy2(f, backup)
    tmp = f.with_name(f.name + ".raven-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if f.exists():
        shutil.copymode(f, tmp)
    os.replace(tmp, f)


def _claude_cli() -> str | None:
    return shutil.which("claude")


def status(entry: dict) -> list[dict]:
    out = []
    for c in clients():
        if c["id"] == "claude_code":
            found = bool(_claude_cli())
            added = False
            cfg = _home() / ".claude.json"
            if found and cfg.exists():
                try:
                    added = "raven" in (json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers") or {})
                except (OSError, ValueError):
                    pass
            out.append({"id": c["id"], "name": c["name"], "found": found, "added": added, "current": added, "path": "claude mcp"})
            continue
        f: Path = c["file"]
        found = f.exists() or f.parent.exists()
        have = None
        try:
            have = (_read(f).get(c["key"]) or {}).get("raven")
        except ClientError:
            pass
        out.append({"id": c["id"], "name": c["name"], "found": found, "added": have is not None,
                    "current": have == _entry(entry, c["vscode"]), "path": str(f)})
    return out


def _client(cid: str) -> dict:
    for c in clients():
        if c["id"] == cid:
            return c
    raise ClientError(f"Unknown MCP client: {cid}")


def add(cid: str, entry: dict) -> dict:
    c = _client(cid)
    if cid == "claude_code":
        cli = _claude_cli()
        if not cli:
            raise ClientError("The claude command is not installed")
        subprocess.run([cli, "mcp", "remove", "raven", "--scope", "user"], capture_output=True, timeout=30)
        r = subprocess.run([cli, "mcp", "add-json", "raven", json.dumps(entry), "--scope", "user"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ClientError(f"claude mcp add-json failed: {(r.stderr or r.stdout)[:200]}")
        return {"ok": True, "path": "claude mcp (user scope)"}
    f: Path = c["file"]
    data = _read(f)
    servers = data.get(c["key"])
    if servers is not None and not isinstance(servers, dict):
        raise ClientError(f"{c['key']} in {f.name} is not a JSON object, so Raven does not change it.")
    data[c["key"]] = {**(servers or {}), "raven": _entry(entry, c["vscode"])}
    _write(f, data)
    return {"ok": True, "path": str(f)}


def remove(cid: str) -> dict:
    c = _client(cid)
    if cid == "claude_code":
        cli = _claude_cli()
        if not cli:
            raise ClientError("The claude command is not installed")
        subprocess.run([cli, "mcp", "remove", "raven", "--scope", "user"], capture_output=True, timeout=30)
        return {"ok": True}
    f: Path = c["file"]
    data = _read(f)
    servers = data.get(c["key"]) or {}
    if isinstance(servers, dict) and "raven" in servers:
        servers.pop("raven")
        data[c["key"]] = servers
        _write(f, data)
    return {"ok": True}
