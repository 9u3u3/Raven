"""Raven as an MCP server (section 13.12 item 1): the stdio adapter that an MCP client starts.

Claude Desktop, Cursor or Zed starts this process. It finds the running Raven sidecar through
<data dir>/sidecar.json (port and token, mode 0600) and forwards each tool call to the sidecar's /mcp
routes. The sidecar redacts each answer and writes it to the privacy ledger (mcp_gate.py).

Client config (Claude Desktop, claude_desktop_config.json):
  {"mcpServers": {"raven": {"command": "<repo>/core/.venv/bin/python", "args": ["-m", "raven_core.mcp_server"]}}}
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from platformdirs import user_data_dir

DATA_DIR = os.environ.get("RAVEN_DATA_DIR") or user_data_dir("raven", appauthor=False)

server = MCPServer(
    name="raven",
    title="Raven",
    instructions=(
        "Raven is the user's file manager. It searches, reads and answers questions from the files in the folders "
        "that the user indexed. Results are redacted: placeholders such as [PERSON_1] replace private values. "
        "Paths start with ~ (the home folder). Raven tools only read; to change files, tell the user what to do in Raven."
    ),
    version="0.3.0",
)


class RavenDown(ToolError):
    pass


def _call(method: str, path: str, **kw) -> dict:
    try:
        with open(os.path.join(DATA_DIR, "sidecar.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise RavenDown("Raven is not running. Ask the user to start Raven.") from e
    try:
        r = httpx.request(method, f"http://127.0.0.1:{cfg['port']}{path}", headers={"Authorization": f"Bearer {cfg['token']}"},
                          timeout=180, **kw)
    except httpx.HTTPError as e:
        raise RavenDown("Raven is not running. Ask the user to start Raven.") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail") or r.json().get("error")
        except ValueError:
            msg = r.text[:300]
        # ToolError: the client sees the reason (outside the indexed folders, MCP off, blocked by the gate).
        raise ToolError(msg or f"Raven error {r.status_code}")
    return r.json()


@server.tool(description="Search the user's files by name, words or meaning. Returns paths, the reason for each match and a redacted snippet.")
def search_files(query: str, limit: int = 10) -> dict:
    return _call("GET", "/mcp/search", params={"q": query, "limit": limit})


@server.tool(description="Answer a question from the user's files with the local model. Returns the answer with numbered citations and their paths.")
def ask_files(question: str) -> dict:
    return _call("POST", "/mcp/ask", json={"question": question})


@server.tool(description="Read the text of one file (PDF, Word, Markdown, text and more). Private values are redacted. Path from search_files, such as ~/Documents/x.pdf.")
def read_file(path: str, max_chars: int = 6000) -> dict:
    return _call("POST", "/mcp/read", json={"path": path, "max_chars": max_chars})


@server.tool(description="List the files and folders in one indexed folder, such as ~/Downloads.")
def list_folder(path: str) -> dict:
    return _call("POST", "/mcp/list", json={"path": path})


@server.tool(description="Propose how to organize a folder into subfolders by category. A plan only: the user applies it in Raven.")
def plan_organize(path: str) -> dict:
    return _call("POST", "/mcp/organize", json={"path": path})


@server.tool(description="Raven's index state: how many files it knows and which folders it indexes.")
def raven_status() -> dict:
    return _call("GET", "/mcp/status")


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
