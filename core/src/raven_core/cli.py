"""Raven in the terminal: the Ctrl+K search of the app as a TUI. Enter goes to the folder of the result.

A program cannot change the folder of the shell that started it. So `raven search` writes the target folder
to stdout, and a shell function from `raven init <shell>` runs `cd` on it. The TUI draws on /dev/tty, so
`$(raven search)` catches only the folder.

The CLI uses the running sidecar through <data dir>/sidecar.json (as mcp_server.py does). When no sidecar
runs, it starts one with RAVEN_PARENT_PID set to its own pid, so that sidecar exits with the CLI. This module
does not import config.py: config creates folders at import time, and the CLI must start fast.

Shell setup (bash, zsh or fish), in the shell rc file:
  eval "$(<repo>/core/.venv/bin/raven init bash)"
"""

from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
import unicodedata
from urllib.parse import urlencode

DATA_DIR = os.environ.get("RAVEN_DATA_DIR") or ""
if not DATA_DIR:
    from platformdirs import user_data_dir

    DATA_DIR = user_data_dir("raven", appauthor=False)

HOME = os.path.expanduser("~")
# Same rule as PATHLIKE in app/src/renderer/src/lib/search.ts: the query names a place.
PATHLIKE = re.compile(r"^(~|/|\\\\|[a-z]+://|[a-z]:[\\/])", re.I)
FOLDER_WORDS = re.compile(r"\b(folders?|director(y|ies)|dir|projects?)\b", re.I)
HYBRID_DEBOUNCE = 0.15
FILE_LIMIT = 30
FOLDER_LIMIT = 8
START_WAIT = 30.0
# The packaged sidecar (PyInstaller). It runs this CLI as `raven-core cli ...`.
FROZEN = bool(getattr(sys, "frozen", False))
LAUNCHER = os.path.join(HOME, ".local", "bin", "raven")
# The first line after the shebang. Raven replaces or removes only a launcher that has it.
MARK = "# raven: terminal command, written by the Raven app"

# The features of the terminal command. Settings, Integrations lists them (GET /cli).
FEATURES = [
    {"id": "search", "name": "Search", "keys": "Ctrl+K, or rv QUERY",
     "text": "The Ctrl+K search of the app. Enter goes to the folder of the result. Ctrl+O opens the result."},
]


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- sidecar


class Api:
    """HTTP to the sidecar. Each thread keeps its own keep-alive connection."""

    def __init__(self, port: int, token: str):
        self.port = port
        self.token = token
        self._local = threading.local()

    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
            self._local.conn = c
        c.timeout = timeout
        if c.sock is not None:
            c.sock.settimeout(timeout)
        return c

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                timeout: float = 30.0) -> dict:
        url = path + ("?" + urlencode(params) if params else "")
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        for attempt in (0, 1):
            c = self._conn(timeout)
            try:
                c.request(method, url, body=data, headers=headers)
                r = c.getresponse()
                raw = r.read()
                break
            except (OSError, http.client.HTTPException) as e:
                c.close()
                self._local.conn = None
                if attempt:
                    raise ApiError(f"Raven core does not answer ({e.__class__.__name__})") from e
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            out = {}
        if r.status >= 400:
            msg = out.get("error") or out.get("detail") if isinstance(out, dict) else None
            raise ApiError(str(msg or f"Raven core error {r.status}"))
        return out

    def get(self, path: str, timeout: float = 30.0, **params) -> dict:
        return self.request("GET", path, params=params, timeout=timeout)

    def post(self, path: str, body: dict, timeout: float = 5.0) -> dict:
        return self.request("POST", path, body=body, timeout=timeout)


def _discover() -> Api | None:
    try:
        with open(os.path.join(DATA_DIR, "sidecar.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        return Api(int(cfg["port"]), str(cfg["token"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _health(api: Api, timeout: float = 1.0) -> dict | None:
    try:
        return api.get("/health", timeout=timeout)
    except ApiError:
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_sidecar(status) -> Api:
    """Start a sidecar for this CLI. It exits about 2 s after the CLI (RAVEN_PARENT_PID, __main__._watch_parent)."""
    port, token = _free_port(), secrets.token_hex(16)
    env = dict(os.environ, RAVEN_TOKEN=token, RAVEN_PORT=str(port), RAVEN_PARENT_PID=str(os.getpid()))
    core = [sys.executable] if FROZEN else [sys.executable, "-m", "raven_core"]
    proc = subprocess.Popen([*core, "--port", str(port)], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    api = Api(port, token)
    status("starting Raven core")
    end = time.monotonic() + START_WAIT
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise ApiError(f"Raven core did not start. Read the log in {os.path.join(DATA_DIR, 'logs')}.")
        if _health(api, 0.5) is not None:
            return api
        time.sleep(0.1)
    proc.kill()
    raise ApiError("Raven core did not start in 30 s.")


def connect(status=lambda s: None, start: bool = True) -> Api:
    api = _discover()
    h = _health(api) if api else None
    if h is None:
        if not start:
            raise ApiError("Raven is not running. Start Raven, or run without --no-start.")
        api = _start_sidecar(status)
        h = _health(api) or {}
    if h.get("locked"):
        raise ApiError("The index is locked. Open Raven and type the passphrase.")
    return api


# ---------------------------------------------------------------- search


def merge(q: str, folders: list[dict], files: list[dict]) -> list[dict]:
    """One list for the TUI: folders and files, without the same path twice.
    A path, a folder name that ranks first or a folder word puts the folders first (as in CommandBar.tsx)."""
    top = folders[0].get("ranks", {}) if folders else {}
    first = bool(PATHLIKE.match(q)) or 1 in (top.get("name"), top.get("disk"), top.get("project")) \
        or bool(FOLDER_WORDS.search(q))
    fold = [dict(f, is_dir=True, _folder=True) for f in folders[: FOLDER_LIMIT if first or not files else 4]]
    seen = {f["path"] for f in fold}
    rest = []
    for h in files:
        if h["path"] in seen:
            continue
        seen.add(h["path"])
        rest.append(h)
    return fold + rest if first else rest + fold


def target_dir(item: dict) -> str:
    """The folder to go to: the folder itself, or the folder that holds the file."""
    return item["path"] if item.get("is_dir") else (item.get("parent") or os.path.dirname(item["path"]))


class Search:
    """Two workers. The fast one runs names, BM25 and folders on each key. The slow one runs the hybrid
    search (vectors and reranker) after a pause in typing. A result for an old query is dropped."""

    def __init__(self, changed):
        self.changed = changed
        self.api: Api | None = None
        self.error: str | None = None
        self.note: str | None = None
        self.cv = threading.Condition()
        self.gen = 0
        self.q = ""
        self.res = self._empty("")
        for fn in (self._fast, self._slow):
            threading.Thread(target=fn, daemon=True).start()

    @staticmethod
    def _empty(q: str) -> dict:
        return {"q": q, "folders": [], "keyword": None, "hybrid": None, "frecent": [], "pending": True, "ms": None}

    def ready(self, api: Api) -> None:
        with self.cv:
            self.api = api
            self.note = None
            self.gen += 1
            self.cv.notify_all()
        self.changed()

    def fail(self, msg: str) -> None:
        self.error = msg
        self.changed()

    def status(self, msg: str) -> None:
        self.note = msg
        self.changed()

    def set(self, q: str) -> None:
        q = q.strip()
        with self.cv:
            if q == self.q:
                return
            self.q = q
            self.res = self._empty(q)
            self.gen += 1
            self.cv.notify_all()

    def _wait(self, seen: int) -> tuple[int, str, Api]:
        with self.cv:
            while self.gen == seen or self.api is None:
                self.cv.wait()
            return self.gen, self.q, self.api

    def _put(self, q: str, **kw) -> None:
        with self.cv:
            if self.res["q"] != q:
                return
            self.res.update(kw)
        self.changed()

    def _fast(self) -> None:
        seen = 0
        while True:
            seen, q, api = self._wait(seen)
            try:
                if not q:
                    r = api.get("/ui/frecent", limit=12)
                    self._put(q, frecent=r.get("folders", []), pending=False)
                    continue
                t0 = time.perf_counter()
                fold = api.get("/search/folders", q=q, limit=12).get("results", [])
                self._put(q, folders=fold)
                if PATHLIKE.match(q):
                    self._put(q, keyword={"results": []}, pending=False, ms=(time.perf_counter() - t0) * 1000)
                    continue
                kw = api.get("/search", q=q, mode="keyword", limit=FILE_LIMIT)
                self._put(q, keyword=kw, ms=(time.perf_counter() - t0) * 1000)
            except (ApiError, KeyError, TypeError, ValueError) as e:
                self._put(q, pending=False)
                self.note = str(e)
                self.changed()

    def _slow(self) -> None:
        seen = 0
        while True:
            seen, q, api = self._wait(seen)
            if not q or PATHLIKE.match(q):
                continue
            with self.cv:
                self.cv.wait(HYBRID_DEBOUNCE)
                if self.gen != seen:
                    continue
            try:
                t0 = time.perf_counter()
                hy = api.get("/search", q=q, mode="hybrid", limit=FILE_LIMIT, timeout=60)
                self._put(q, hybrid=hy, pending=False, ms=(time.perf_counter() - t0) * 1000)
            except ApiError:
                # No Ollama or no vectors: the keyword list stays.
                self._put(q, pending=False)

    def items(self) -> tuple[str, list[dict], dict]:
        with self.cv:
            r = dict(self.res)
        q = r["q"]
        if not q:
            rows = [{"path": f["path"], "name": f["name"], "parent": os.path.dirname(f["path"]), "is_dir": True,
                     "_folder": True, "why": "frequent"} for f in r["frecent"]]
            return "Frequent folders", rows, r
        files = (r["hybrid"] or r["keyword"] or {}).get("results", [])
        return "", merge(q, r["folders"], files), r


# ---------------------------------------------------------------- text helpers


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in s)


def fit(s: str, w: int, left: bool = False) -> str:
    """Cut s to w columns. left=True keeps the end (for paths) and marks the cut with an ellipsis."""
    if w <= 0:
        return ""
    if width(s) <= w:
        return s
    chars = list(reversed(s)) if left else list(s)
    out, n = [], 0
    for c in chars:
        cw = width(c)
        if n + cw > w - 1:
            break
        out.append(c)
        n += cw
    return "…" + "".join(reversed(out)) if left else "".join(out) + "…"


def tilde(p: str) -> str:
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + os.sep) else p


def human(n: int | None) -> str:
    if n is None:
        return ""
    f = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or u == "TB":
            return f"{f:.0f} {u}" if u == "B" or f >= 10 else f"{f:.1f} {u}"
        f /= 1024
    return ""


def why_label(h: dict) -> str:
    if h.get("_folder"):
        w = h.get("why") or "folder"
        return "folder" if w in ("name", "path") else f"folder · {w}"
    w = h.get("why") or ""
    return f"{w} · p.{h['page']}" if h.get("page") else w


def marks(text: str, q: str) -> list[bool]:
    """The characters of text that a query word matches (case does not matter)."""
    on = [False] * len(text)
    low = text.lower()
    for word in {w for w in re.split(r"\s+", q.lower()) if len(w) > 1 or len(q.strip()) == 1}:
        i = low.find(word)
        while i >= 0:
            for j in range(i, i + len(word)):
                on[j] = True
            i = low.find(word, i + len(word))
    return on


# ---------------------------------------------------------------- TUI


HELP = "enter go · ctrl+o open · ↑↓ ctrl+n/p select · ctrl+u clear · esc quit"


class Tui:
    def __init__(self, scr, query: str, start: bool):
        import curses

        self.c = curses
        self.scr = scr
        self.query = query
        self.sel = 0
        self.top = 0
        self.dirty = threading.Event()
        self.search = Search(self.dirty.set)
        self.search.set(query)
        threading.Thread(target=self._connect, args=(start,), daemon=True).start()
        curses.curs_set(1)
        curses.raw()
        scr.keypad(True)
        scr.timeout(40)
        self._colors()

    def _connect(self, start: bool) -> None:
        try:
            self.search.ready(connect(self.search.status, start))
        except ApiError as e:
            self.search.fail(str(e))

    def _colors(self) -> None:
        c = self.c
        self.A = {"accent": c.A_BOLD, "dim": c.A_DIM, "match": c.A_BOLD | c.A_UNDERLINE, "dir": c.A_BOLD,
                  "sel": c.A_REVERSE, "err": c.A_BOLD}
        if not c.has_colors():
            return
        c.start_color()
        try:
            c.use_default_colors()
            bg = -1
        except c.error:
            bg = c.COLOR_BLACK
        sel_bg = 236 if c.COLORS >= 256 else bg
        c.init_pair(1, c.COLOR_CYAN, bg)
        c.init_pair(2, c.COLOR_YELLOW, bg)
        c.init_pair(3, c.COLOR_BLUE, bg)
        c.init_pair(4, c.COLOR_RED, bg)
        c.init_pair(5, -1 if bg == -1 else c.COLOR_WHITE, sel_bg)
        c.init_pair(6, c.COLOR_YELLOW, sel_bg)
        c.init_pair(7, c.COLOR_BLUE, sel_bg)
        c.init_pair(8, 244 if c.COLORS >= 256 else c.COLOR_WHITE, sel_bg)
        c.init_pair(9, 244 if c.COLORS >= 256 else c.COLOR_WHITE, bg)
        self.A = {"accent": c.color_pair(1) | c.A_BOLD, "dim": c.color_pair(9), "match": c.color_pair(2) | c.A_BOLD,
                  "dir": c.color_pair(3) | c.A_BOLD, "err": c.color_pair(4) | c.A_BOLD,
                  "sel": c.color_pair(5), "sel_match": c.color_pair(6) | c.A_BOLD, "sel_dir": c.color_pair(7) | c.A_BOLD,
                  "sel_dim": c.color_pair(8)}
        if sel_bg == bg:
            self.A.update(sel=c.A_REVERSE, sel_match=c.A_REVERSE | c.A_BOLD, sel_dir=c.A_REVERSE | c.A_BOLD,
                          sel_dim=c.A_REVERSE)

    def put(self, y: int, x: int, s: str, attr: int = 0) -> int:
        """Write s at (y, x) and return the next column. The last screen cell raises in curses: ignore it."""
        h, w = self.scr.getmaxyx()
        if y >= h or x >= w:
            return x
        s = fit(s, w - x)
        try:
            self.scr.addstr(y, x, s, attr)
        except self.c.error:
            pass
        return x + width(s)

    # ------------------------------------------------ draw

    def draw(self) -> list[dict]:
        scr, A = self.scr, self.A
        scr.erase()
        h, w = scr.getmaxyx()
        title, items, res = self.search.items()
        self.sel = max(0, min(self.sel, len(items) - 1))

        # Line 0: the prompt and the state on the right.
        s = self.search
        if s.error:
            state, sattr = s.error, A["err"]
        elif s.api is None or s.note:
            state, sattr = (s.note or "connecting"), A["dim"]
        elif res["pending"] and res["q"]:
            state, sattr = "searching", A["accent"]
        else:
            # Hybrid without vectors (no Ollama, no embeddings yet) is a word search.
            tm = (res["hybrid"] or {}).get("timings", {})
            mode = "meaning" if "dense_ms" in tm or "embed_ms" in tm else "words"
            ms = f" · {res['ms']:.0f} ms" if res["ms"] is not None and res["q"] else ""
            state = f"{len(items)} found · {mode}{ms}" if res["q"] else ""
            sattr = A["dim"]
        state = fit(state, max(0, w // 2))
        x = self.put(0, 1, "raven ", A["accent"])
        x = self.put(0, x, "❯ ", A["accent"])
        qx = x
        qw = max(1, w - x - width(state) - 3)
        shown = fit(self.query, qw, left=True)
        self.put(0, x, shown)
        self.put(0, w - width(state) - 1, state, sattr)
        self.put(1, 0, "─" * w, A["dim"])

        # The list. Folders and files carry a section line where the kind changes.
        list_top, list_h = 2, max(1, h - 6)
        rows: list[tuple[str, int]] = []
        if title:
            rows.append((title, -1))
        prev = None
        for i, it in enumerate(items):
            kind = "Folders" if it.get("_folder") else "Files"
            if not title and kind != prev:
                rows.append((kind, -1))
                prev = kind
            rows.append(("", i))
        sel_row = next((r for r, (_, i) in enumerate(rows) if i == self.sel), 0)
        if sel_row < self.top + 1:
            self.top = max(0, sel_row - 1)
        elif sel_row >= self.top + list_h:
            self.top = sel_row - list_h + 1
        self.top = max(0, min(self.top, max(0, len(rows) - list_h)))
        for y, (head, i) in enumerate(rows[self.top: self.top + list_h], start=list_top):
            if i < 0:
                self.put(y, 1, head.upper(), A["dim"] | self.c.A_BOLD)
                continue
            self._row(y, w, items[i], i == self.sel, res["q"])
        if not items and not res["pending"] and s.api and not s.error:
            msg = "Nothing found. Raven searches only the folders that it indexed." if res["q"] else \
                "Type to search files and folders by name, words or meaning."
            self.put(list_top + 1, 2, msg, A["dim"])

        # The detail of the selected item: its full path and a snippet or a summary.
        self.put(h - 4, 0, "─" * w, A["dim"])
        if items:
            it = items[self.sel]
            self.put(h - 3, 1, fit(tilde(it["path"]), w - 2, left=True), A["dir"] if it.get("is_dir") else 0)
            self._detail(h - 2, w, it, res["q"])
        self.put(h - 1, 1, fit(HELP, w - 2), A["dim"])
        cx = qx + width(shown)
        try:
            scr.move(0, min(cx, w - 1))
        except self.c.error:
            pass
        scr.refresh()
        return items

    def _row(self, y: int, w: int, it: dict, sel: bool, q: str) -> None:
        A = self.A
        base = A["sel"] if sel else 0
        if sel:
            self.put(y, 0, " " * w, base)
            self.put(y, 0, "▌", A["accent"])
        is_dir = it.get("is_dir")
        name = it.get("name") or os.path.basename(it["path"]) or it["path"]
        name += "/" if is_dir else ""
        why = why_label(it)
        right = why
        parent = tilde(it.get("parent") or os.path.dirname(it["path"]))
        name_w = min(width(name), max(10, w // 2))
        name = fit(name, name_w)
        x = 2
        nattr = (A["sel_dir"] if sel else A["dir"]) if is_dir else base
        mattr = A["sel_match"] if sel else A["match"]
        for ch, on in zip(name, marks(name, q) + [False] * len(name)):
            x = self.put(y, x, ch, mattr if on else nattr)
        x += 2
        rw = width(right) + 2
        pw = w - x - rw - 1
        dattr = A["sel_dim"] if sel else A["dim"]
        if pw > 4:
            self.put(y, x, fit(parent, pw, left=True), dattr)
        if w - rw > x:
            self.put(y, w - rw, right, dattr)

    def _detail(self, y: int, w: int, it: dict, q: str) -> None:
        A = self.A
        if it.get("_folder"):
            parts = [p for p in (f"{it['files']} files" if it.get("files") is not None else "", human(it.get("size")))
                     if p]
            text = " · ".join(parts)
            if it.get("summary"):
                text = (text + " · " if text else "") + it["summary"]
            if it.get("top"):
                text = (text + " · " if text else "") + ", ".join(t["name"] for t in it["top"])
            self.put(y, 1, fit(text, w - 2), A["dim"])
            return
        x = 1
        if it.get("title"):
            x = self.put(y, x, fit(it["title"], w // 3) + "  ", self.c.A_BOLD)
        for seg in it.get("snippet") or []:
            t = " ".join(str(seg.get("t", "")).split())
            if not t or x >= w - 1:
                continue
            x = self.put(y, x, t + " ", A["match"] if seg.get("hit") else A["dim"])
        if x == 1:
            meta = " · ".join(p for p in (it.get("kind"), human(it.get("size"))) if p)
            self.put(y, 1, meta, A["dim"])

    # ------------------------------------------------ input

    def run(self) -> tuple[str, dict | None]:
        """Returns ("go" | "open" | "quit", the selected item)."""
        c = self.c
        items: list[dict] = []
        self.dirty.set()
        while True:
            if self.dirty.is_set():
                self.dirty.clear()
                items = self.draw()
            try:
                k = self.scr.get_wch()
            except c.error:
                continue
            self.dirty.set()
            if k in ("\n", "\r", c.KEY_ENTER):
                if items:
                    return "go", items[self.sel]
            elif k in ("\x1b", "\x03", "\x07", "\x04"):
                return "quit", None
            elif k == "\x0f":
                if items:
                    return "open", items[self.sel]
            elif k in (c.KEY_UP, "\x10", "\x0b"):
                self.sel = max(0, self.sel - 1)
            elif k in (c.KEY_DOWN, "\x0e", "\t"):
                self.sel = min(max(0, len(items) - 1), self.sel + 1)
            elif k == c.KEY_PPAGE:
                self.sel = max(0, self.sel - max(1, self.scr.getmaxyx()[0] - 8))
            elif k == c.KEY_NPAGE:
                self.sel = min(max(0, len(items) - 1), self.sel + max(1, self.scr.getmaxyx()[0] - 8))
            elif k in (c.KEY_BACKSPACE, "\x7f", "\x08"):
                self._edit(self.query[:-1])
            elif k == "\x15":
                self._edit("")
            elif k == "\x17":
                self._edit(re.sub(r"\S*\s*$", "", self.query.rstrip()) if self.query.strip() else "")
            elif k == c.KEY_RESIZE:
                pass
            elif isinstance(k, str) and k.isprintable():
                self._edit(self.query + k)

    def _edit(self, q: str) -> None:
        self.query = q
        self.sel = 0
        self.top = 0
        self.search.set(q)


def _tui(query: str, start: bool) -> tuple[str, dict | None, Api | None, str]:
    """Run the TUI on /dev/tty. stdout stays free for the result."""
    import curses

    os.environ.setdefault("ESCDELAY", "25")
    import locale

    locale.setlocale(locale.LC_ALL, "")
    out_fd, in_fd = os.dup(1), os.dup(0)
    tty = os.open("/dev/tty", os.O_RDWR)
    os.dup2(tty, 1)
    os.dup2(tty, 0)
    holder: dict = {}
    try:
        def body(scr):
            t = Tui(scr, query, start)
            holder["t"] = t
            return t.run()

        action, item = curses.wrapper(body)
    finally:
        os.dup2(out_fd, 1)
        os.dup2(in_fd, 0)
        os.close(tty)
        os.close(out_fd)
        os.close(in_fd)
    t = holder.get("t")
    return action, item, (t.search.api if t else None), (t.query if t else query)


def _open(path: str) -> None:
    cmd = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def cmd_search(query: str, start: bool) -> int:
    if os.name == "nt":
        print("raven: the terminal search runs on Linux and macOS only", file=sys.stderr)
        return 2
    if not os.path.exists("/dev/tty"):
        print("raven: search needs a terminal", file=sys.stderr)
        return 2
    action, item, api, q = _tui(query, start)
    if action == "quit" or item is None:
        return 1
    if api is not None:
        # Habits and frecency, as the app records them (store.ts: /ui/visit, activity('result_click')).
        try:
            if q.strip():
                api.post("/ui/activity", {"kind": "result_click", "path": item["path"], "query": q.strip()}, timeout=1)
            api.post("/ui/visit", {"path": target_dir(item)}, timeout=1)
        except ApiError:
            pass
    if action == "open":
        _open(item["path"])
        return 1
    print(target_dir(item))
    return 0


# ---------------------------------------------------------------- shell setup


def _self_cmd() -> str:
    """The command that starts this CLI again, with absolute paths. The shell rc does not need PATH.
    The launcher sets RAVEN_CLI_CMD, because the AppImage path inside the mount changes at each start."""
    if os.environ.get("RAVEN_CLI_CMD"):
        return shlex.quote(os.environ["RAVEN_CLI_CMD"])
    if FROZEN:
        return f"{shlex.quote(sys.executable)} cli"
    argv0 = os.path.abspath(sys.argv[0])
    if os.path.basename(argv0) in ("raven", "raven.exe") and os.access(argv0, os.X_OK):
        return shlex.quote(argv0)
    return f"{shlex.quote(sys.executable)} -m raven_core.cli"


def init_script(shell: str, key: bool = True) -> str:
    raven = _self_cmd()
    if shell == "bash":
        s = f"""rv() {{
  local d
  d="$({raven} search "$*")" || return
  [ -n "$d" ] && builtin cd -- "$d"
}}
"""
        # A macro, not bind -x: the shell runs rv as a command, so the prompt shows the new folder.
        # Ctrl+U keeps the typed line in the kill ring. Ctrl+Y brings it back.
        if key:
            s += 'bind \'"\\C-k": "\\C-e\\C-urv\\C-m"\'\n'
        return s
    if shell == "zsh":
        s = f"""rv() {{
  local d
  d="$({raven} search "$*")" || return
  [[ -n $d ]] && builtin cd -- "$d"
}}
"""
        if key:
            s += f"""_raven_widget() {{
  local d
  d="$({raven} search </dev/tty)"
  if [[ -n $d ]]; then
    builtin cd -- "$d"
    local f
    for f in $precmd_functions; do $f; done
  fi
  zle reset-prompt
}}
zle -N _raven_widget
bindkey '^K' _raven_widget
"""
        return s
    if shell == "fish":
        s = f"""function rv
    set -l d ({raven} search "$argv")
    or return
    test -n "$d"; and builtin cd -- $d
end
"""
        if key:
            s += "bind \\ck 'rv; commandline -f repaint'\n"
        return s
    raise SystemExit(f"raven: no setup for the shell {shell!r}. Use bash, zsh or fish.")


# ---------------------------------------------------------------- the raven command (Settings, Integrations)


def _launcher_script() -> str | None:
    """A small script for ~/.local/bin/raven, or None when this build cannot have one.

    An AppImage mounts itself in a new temporary folder at each start, so the script cannot name the sidecar
    binary. It mounts the AppImage with --appimage-mount (the AppImage runtime prints the folder), runs the
    binary inside, and ends the mount when no process runs from it."""
    me = shlex.quote(LAUNCHER)
    head = f"#!/usr/bin/env bash\n{MARK}. Settings, Integrations, Terminal can remove it.\nexport RAVEN_CLI_CMD={me}\n"
    if os.environ.get("RAVEN_DATA_DIR"):
        # The app uses another data folder (as in the MCP config): the command uses the same one.
        head += f"export RAVEN_DATA_DIR={shlex.quote(os.environ['RAVEN_DATA_DIR'])}\n"
    image = os.environ.get("APPIMAGE")
    if FROZEN and image:
        app = shlex.quote(image)
        return head + f"""app={app}
if [ ! -x "$app" ]; then
  echo "raven: $app is gone. Open Raven, Settings, Integrations, and install the command again." >&2
  exit 2
fi
coproc mount {{ exec "$app" --appimage-mount; }}
IFS= read -r dir <&"${{mount[0]}}"
"$dir/resources/raven-core/raven-core" cli "$@"
rc=$?
# A sidecar that the CLI started runs from the mount for about 2 s more. End the mount after it, in the
# background: the shell must not wait for it.
busy() {{
  local p
  for p in /proc/[0-9]*/exe; do
    [[ $(readlink "$p" 2>/dev/null) == "$dir"/* ]] && return 0
  done
  return 1
}}
(for _ in $(seq 1 120); do busy || break; sleep 0.5; done; kill "$mount_PID") </dev/null >/dev/null 2>&1 &
exit $rc
"""
    if FROZEN:
        return head + f'exec {shlex.quote(sys.executable)} cli "$@"\n'
    return head + f'exec {shlex.quote(sys.executable)} -m raven_core.cli "$@"\n'


def _ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MARK in f.read(400)
    except OSError:
        return False


def status() -> dict:
    """The state of the raven command for Settings, Integrations."""
    supported = os.name != "nt"
    script = _launcher_script() if supported else None
    exists = os.path.lexists(LAUNCHER)
    on_path = os.path.dirname(LAUNCHER) in os.environ.get("PATH", "").split(os.pathsep)
    # The full path: some systems add ~/.local/bin to PATH after the shell reads its rc file.
    cmd = shlex.quote(LAUNCHER)
    current = False
    if exists and script:
        try:
            with open(LAUNCHER, encoding="utf-8") as f:
                current = f.read() == script
        except OSError:
            pass
    return {
        "supported": supported,
        "reason": None if supported else "The terminal search runs on Linux and macOS. Windows comes later.",
        "path": LAUNCHER,
        "installed": exists and _ours(LAUNCHER),
        "current": current,
        "other": exists and not _ours(LAUNCHER),
        "on_path": on_path,
        "features": FEATURES,
        "setup": {
            "bash": f'eval "$({cmd} init bash)"',
            "zsh": f'eval "$({cmd} init zsh)"',
            "fish": f"{cmd} init fish | source",
        },
    }


def install() -> dict:
    script = _launcher_script() if os.name != "nt" else None
    if script is None:
        raise ApiError("The terminal search runs on Linux and macOS only.")
    if os.path.lexists(LAUNCHER) and not _ours(LAUNCHER):
        raise ApiError(f"{LAUNCHER} exists and Raven did not write it. Remove or rename it first.")
    os.makedirs(os.path.dirname(LAUNCHER), exist_ok=True)
    tmp = LAUNCHER + ".raven-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(tmp, 0o755)
    os.replace(tmp, LAUNCHER)
    return status()


def remove() -> dict:
    if os.path.lexists(LAUNCHER):
        if not _ours(LAUNCHER):
            raise ApiError(f"Raven did not write {LAUNCHER}, so it does not remove it.")
        os.remove(LAUNCHER)
    return status()


USAGE = """usage: raven [search] [QUERY...]     search, then print the folder to go to
       raven init bash|zsh|fish [--no-key]   print the shell setup (rv and Ctrl+K)

search keys: enter goes to the folder (a file: the folder that holds it), ctrl+o opens the item,
             up/down or ctrl+p/ctrl+n select, ctrl+u clears, ctrl+w deletes a word, esc quits.
options:     --no-start  do not start Raven core when Raven is not running

Shell setup (add one line to your rc file):
  bash:  eval "$({cmd} init bash)"
  zsh:   eval "$({cmd} init zsh)"
  fish:  {cmd} init fish | source
Then press Ctrl+K, or type rv QUERY."""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE.format(cmd=_self_cmd()))
        return 0
    if args and args[0] == "init":
        if len(args) < 2:
            print(USAGE.format(cmd=_self_cmd()), file=sys.stderr)
            return 2
        sys.stdout.write(init_script(args[1], key="--no-key" not in args))
        return 0
    if args and args[0] == "search":
        args = args[1:]
    start = "--no-start" not in args
    args = [a for a in args if a != "--no-start"]
    try:
        return cmd_search(" ".join(args), start)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
