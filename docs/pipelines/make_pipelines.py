"""Draw one diagram per Raven pipeline as SVG, and render PNGs with rsvg-convert.

Usage: python3 docs/pipelines/make_pipelines.py
The facts in each box come from the code and technical_details.md. Edit the DIAGRAMS below, then run again.
"""

from __future__ import annotations

import shutil
import subprocess
from html import escape
from pathlib import Path

HERE = Path(__file__).parent
W, H = 1600, 900

COL = {  # box kinds: process, data, model, output, guard, user
    "proc": "#7fa8d6", "data": "#d9b26a", "model": "#b48ead", "out": "#8dbf93", "guard": "#e5736a", "user": "#c9ccd1",
}
LEGEND = [("proc", "step"), ("model", "AI model"), ("data", "stored data"), ("guard", "safety check"), ("out", "result")]


class D:
    def __init__(self, no: str, title: str, sub: str) -> None:
        self.no, self.title, self.sub = no, title, sub
        self.boxes: dict[str, tuple[float, float, float, float]] = {}
        self.parts: list[str] = []
        self.under: list[str] = []

    def box(self, id: str, x: float, y: float, w: float, h: float, title: str, lines: list[str] = (), kind: str = "proc") -> "D":
        c = COL[kind]
        self.boxes[id] = (x, y, w, h)
        p = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#1d2024" stroke="{c}" stroke-width="2"/>',
             f'<rect x="{x}" y="{y}" width="5" height="{h}" fill="{c}"/>',
             f'<text x="{x + 18}" y="{y + 28}" font-size="18" font-weight="bold" fill="#e3e5e8">{escape(title)}</text>']
        for i, ln in enumerate(lines):
            p.append(f'<text x="{x + 18}" y="{y + 52 + i * 21}" font-size="14.5" fill="#a9aeb6" font-family="DejaVu Sans Mono, monospace">{escape(ln)}</text>')
        self.parts += p
        return self

    def _pt(self, id: str, side: str) -> tuple[float, float]:
        x, y, w, h = self.boxes[id]
        return {"r": (x + w, y + h / 2), "l": (x, y + h / 2), "t": (x + w / 2, y), "b": (x + w / 2, y + h)}[side]

    def edge(self, a: str, b: str, route: str = "h", label: str = "", dashed: bool = False, color: str = "#7fa8d6",
             ofs: float = 0) -> "D":
        """route h: right to left side, with an elbow. v: bottom to top. hv: out the right side, then down or up into b."""
        if route == "h":
            (x1, y1), (x2, y2) = self._pt(a, "r"), self._pt(b, "l")
            mx = (x1 + x2) / 2 + ofs
            pts = [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
        elif route == "v":
            (x1, y1), (x2, y2) = self._pt(a, "b"), self._pt(b, "t")
            my = (y1 + y2) / 2 + ofs
            pts = [(x1, y1), (x1, my), (x2, my), (x2, y2)]
        elif route == "hv":
            (x1, y1) = self._pt(a, "r")
            bx, by, bw, bh = self.boxes[b]
            x2 = bx + bw / 2 + ofs
            y2 = by if by > y1 else by + bh
            pts = [(x1, y1), (x2, y1), (x2, y2)]
        elif route == "u":
            (x1, y1), (x2, y2) = self._pt(a, "t"), self._pt(b, "b")
            my = (y1 + y2) / 2 + ofs
            pts = [(x1, y1), (x1, my), (x2, my), (x2, y2)]
        else:  # "vh": out the bottom or top, then sideways into b
            ax, ay, aw, ah = self.boxes[a]
            bx, by, bw, bh = self.boxes[b]
            x1 = ax + aw / 2 + ofs
            y2 = by + bh / 2
            y1 = ay + ah if y2 > ay + ah else ay
            x2 = bx if bx > x1 else bx + bw
            pts = [(x1, y1), (x1, y2), (x2, y2)]
        d = "M" + " L".join(f"{x:.0f} {y:.0f}" for x, y in pts)
        dash = ' stroke-dasharray="7 6"' if dashed else ""
        self.under.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.2"{dash} marker-end="url(#arr)"/>')
        if label:
            (ax_, ay_), (bx_, by_) = pts[len(pts) // 2 - 1], pts[len(pts) // 2]
            lx, ly = (ax_ + bx_) / 2, (ay_ + by_) / 2
            tw = len(label) * 7.6 + 14
            self.parts.append(f'<rect x="{lx - tw / 2:.0f}" y="{ly - 13:.0f}" width="{tw:.0f}" height="22" fill="#17191c"/>'
                              f'<text x="{lx:.0f}" y="{ly + 3:.0f}" text-anchor="middle" font-size="13.5" fill="{color}">{escape(label)}</text>')
        return self

    def note(self, x: float, y: float, text: str, color: str = "#8f949c", size: float = 15, anchor: str = "start") -> "D":
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{escape(text)}</text>')
        return self

    def svg(self) -> str:
        legend = []
        lx = W - 64
        for kind, word in reversed(LEGEND):
            tw = len(word) * 8 + 34
            lx -= tw
            legend.append(f'<rect x="{lx}" y="58" width="14" height="14" fill="{COL[kind]}"/>'
                          f'<text x="{lx + 20}" y="70" font-size="13.5" fill="#8f949c">{word}</text>')
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Noto Sans, sans-serif">
<defs>
  <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.1" fill="#2a2e34"/></pattern>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/></marker>
</defs>
<rect width="{W}" height="{H}" fill="#17191c"/><rect width="{W}" height="{H}" fill="url(#dots)"/>
<rect x="24" y="24" width="{W - 48}" height="{H - 48}" fill="none" stroke="#2c3036" stroke-width="2"/>
<path d="M24 64 V24 H64" fill="none" stroke="#7fa8d6" stroke-width="4"/>
<path d="M{W - 64} {H - 24} H{W - 24} V{H - 64}" fill="none" stroke="#7fa8d6" stroke-width="4"/>
<text x="64" y="84" font-size="16" fill="#7fa8d6" font-family="DejaVu Sans Mono, monospace">{self.no}</text>
<text x="104" y="86" font-size="34" font-weight="bold" fill="#e3e5e8">{escape(self.title)}</text>
<text x="64" y="120" font-size="17" fill="#8f949c">{escape(self.sub)}</text>
<text x="{W - 64}" y="{H - 44}" font-size="14" fill="#5d6168" text-anchor="end">RAVEN · pipelines</text>
{''.join(legend)}
{''.join(self.under)}
{''.join(self.parts)}
</svg>
"""


def architecture() -> D:
    d = D("01", "Architecture", "Two processes on the laptop. The sidecar holds all file, index, AI and privacy logic.")
    d.box("ui", 64, 170, 330, 150, "Renderer (React)", ["Files · Space · Organize", "Privacy · Settings", "Command bar: Find, Ask, Act"], "user")
    d.box("main", 64, 380, 330, 150, "Electron main process", ["starts the sidecar + Ollama", "egress guard: loopback only", "OS: open with, print to PDF"], "guard")
    d.box("side", 500, 170, 600, 480, "Python sidecar  raven-core", [
        "FastAPI on 127.0.0.1 + token, one WebSocket",
        "",
        "fs       operations · undo journal · copy queue",
        "         trash · archives · network · rclone",
        "index    walk · extract · OCR · chunk · embed",
        "media    SigLIP 2 (look) · Whisper (speech)",
        "search   BM25 · vectors · RRF · reranker",
        "rag      Ask with citations",
        "agent    grammar · planner · review · run",
        "storage  scan · Reclaim · duplicates · compress",
        "organize classify · plans · naming · rules",
        "privacy  PII layers · gate · ledger · router",
        "mcp      read-only tools through the gate",
    ])
    d.box("db", 1200, 170, 340, 130, "SQLite or SQLCipher", ["files · chunks · FTS5", "sqlite-vec · journal · ledger"], "data")
    d.box("oll", 1200, 340, 340, 110, "Ollama (GPU)", ["embeddinggemma · qwen3:4b"], "model")
    d.box("onnx", 1200, 490, 340, 110, "ONNX / CTranslate2 (CPU)", ["SigLIP 2 · MiniLM · Privacy Filter", "Whisper base"], "model")
    d.box("cloud", 1200, 680, 340, 120, "Cloud (optional)", ["Groq · Anthropic · claude -p", "only after redaction"], "out")
    d.box("mcp", 500, 700, 330, 100, "MCP clients", ["Claude Desktop · Cursor · Zed"], "user")
    d.edge("ui", "side", "h", "HTTP + WebSocket")
    d.edge("main", "side", "h", "spawn, watch")
    d.edge("main", "ui", "v", "", dashed=True, color="#e5736a")
    d.edge("side", "db", "h")
    d.edge("side", "oll", "h", ofs=-20)
    d.edge("side", "onnx", "h", ofs=-20)
    d.edge("side", "cloud", "h", "redaction gate", color="#e5736a", ofs=-20)
    d.edge("mcp", "side", "hv", "stdio adapter", ofs=60)
    d.note(64, 600, "The renderer never reaches the network:", "#e5736a")
    d.note(64, 622, "every non-loopback request is cancelled.", "#e5736a")
    return d


def index() -> D:
    d = D("02", "Index pipeline", "Staged: search works after the names stage. Each later stage adds more. Changed files only.")
    d.box("walk", 64, 170, 300, 160, "Walk the roots", ["skip: .ssh, caches, SDKs,", "node_modules, datasets", "code projects: names only", "UPSERT only when changed"])
    d.box("names", 64, 400, 300, 110, "A · Names", ["files table: size, times,", "kind (magic bytes)"], "data")
    d.box("watch", 64, 590, 300, 120, "Watcher", ["inotify (local)", "poll every 30 s (shares)"])
    d.box("ext", 440, 170, 330, 160, "B · Extract text", ["PDF (PyMuPDF, one lock)", "OCR scans (tesseract)", "DOCX/PPTX/XLSX, ODF, EPUB", "camera photos: no OCR"])
    d.box("chunk", 440, 400, 330, 140, "Chunk", ["~350 tokens, 50 overlap", "header: File | Folder |", "Title | Section"])
    d.box("pii", 440, 610, 330, 110, "PII layer 1", ["spans per chunk", "file PII level 0 / 1 / 2"], "guard")
    d.box("emb", 850, 170, 330, 140, "C · Meaning", ["embeddinggemma (Ollama)", "batches of 48 · 768-d", "lite: potion on the CPU"], "model")
    d.box("vec", 850, 380, 330, 120, "Stores", ["FTS5 (BM25) · sqlite-vec", "binary copies ≥ 50k chunks"], "data")
    d.box("media", 1260, 170, 280, 150, "D · Images", ["SigLIP 2: photos,", "PDF pages, video", "keyframes + time"], "model")
    d.box("speech", 1260, 360, 280, 130, "E · Speech", ["Whisper base int8", "timed 90-word chunks"], "model")
    d.box("cls", 1260, 530, 280, 130, "F · Categories", ["rules → examples →", "prototypes → local LLM"], "model")
    d.box("fold", 850, 570, 330, 140, "Folders", ["profile + 2-sentence", "summary (local LLM)", "+ mean vector"], "model")
    d.edge("walk", "names", "v")
    d.edge("watch", "names", "u", "", dashed=True)
    d.edge("names", "ext", "h")
    d.edge("ext", "chunk", "v")
    d.edge("chunk", "pii", "v")
    d.edge("chunk", "emb", "h")
    d.edge("emb", "vec", "v")
    d.edge("emb", "media", "h", ofs=-10)
    d.edge("media", "speech", "v")
    d.edge("speech", "cls", "v")
    d.edge("vec", "fold", "v")
    d.note(64, 790, "Interactive requests come first: an Ask or a search pauses the background batch at once.", "#7fa8d6", 16)
    d.note(64, 815, "Real home folder: text work cut from 581,790 files to about 5,500 by the skip rules. Rescan of an unchanged disk: 36 s.", "#8f949c", 15)
    return d


def find() -> D:
    d = D("03", "Find pipeline (hybrid search)", "Ctrl+K. Four ways to match, fused by rank, then a cross-encoder fixes the order. 106 ms.")
    d.box("q", 64, 330, 230, 120, "Query", ['"invoices older', 'than 6 months"'], "user")
    d.box("rules", 340, 200, 290, 130, "Rule filters", ["type: > in: after:", '"last semester"', "perm: is: tag:"])
    d.box("llmf", 340, 420, 290, 130, "LLM filters", ["only for words left over", "JSON → the same chips"], "model")
    d.box("name", 690, 150, 300, 90, "Fuzzy names", ["rapidfuzz"])
    d.box("bm25", 690, 265, 300, 90, "Keywords", ["FTS5 BM25 + coverage"], "data")
    d.box("dense", 690, 380, 300, 105, "Meaning", ["sqlite-vec cosine ≥ 0.30", "binary + rescore ≥ 50k"], "data")
    d.box("look", 690, 510, 300, 105, "Looks like", ["SigLIP 2 text → image,", "page, video moment"], "model")
    d.box("rrf", 1060, 230, 230, 120, "RRF", ["Σ 1 / (60 + rank)", "top 100"])
    d.box("hab", 1060, 400, 230, 105, "Habits", ["files you open", "rank a bit higher"])
    d.box("rr", 1340, 230, 210, 140, "Rerank", ["MiniLM cross-", "encoder, top 20,", "RRF again"], "model")
    d.box("out", 1340, 440, 210, 175, "Results", ["name · keyword ·", "meaning: the why", "video: at 4:57", "speech: said at 0:02"], "out")
    d.edge("q", "rules", "h")
    d.edge("rules", "llmf", "v", "words left?", dashed=True)
    for b in ("name", "bm25", "dense"):
        d.edge("rules", b, "h", ofs=-8)
    d.edge("llmf", "dense", "h", ofs=8)
    d.edge("llmf", "look", "h", ofs=8)
    for b in ("name", "bm25", "dense"):
        d.edge(b, "rrf", "h", ofs=-10)
    d.edge("rrf", "hab", "v")
    d.edge("hab", "rr", "h")
    d.edge("rr", "out", "v")
    d.edge("look", "out", "h", ofs=150)
    d.note(64, 760, "Name and keyword search: 6 ms · full hybrid search: 106 ms · by appearance: 25 ms · folders: 65 ms", "#7fa8d6", 16)
    d.note(64, 786, "15 test queries: 14 at rank 1, all 15 in the top 3 (MRR 0.967).", "#8f949c")
    return d


def media() -> D:
    d = D("04", "Media pipeline: look and speech", "Photos, PDF pages and video frames in one SigLIP 2 space. Audio and video speech through Whisper.")
    d.note(64, 175, "BY LOOK", "#b48ead", 16)
    d.box("src", 64, 190, 300, 150, "Sources", ["photos, screenshots", "PDF pages (max 40)", "video keyframes ≥ 2 s apart", "(max 300, same frames dropped)"], "user")
    d.box("vis", 430, 190, 300, 150, "SigLIP 2 vision", ["224 × 224, no crop", "fp32 ONNX on the CPU", "unloads after 90 s"], "model")
    d.box("vv", 800, 190, 260, 150, "visual_vec", ["768-d per image,", "page or frame", "+ the second"], "data")
    d.box("tq", 800, 380, 260, 110, "Text query", ['"a big rabbit', 'in a forest"'], "user")
    d.box("txt", 1120, 380, 220, 110, "SigLIP 2 text", ["int8, 64 tokens"], "model")
    d.box("hit", 1120, 190, 420, 150, "A moment", ["big_buck_bunny.mp4 at 4:57", "opens at that second, with", "a strip of 5 keyframes"], "out")
    d.edge("src", "vis", "h")
    d.edge("vis", "vv", "h")
    d.edge("tq", "txt", "h")
    d.edge("txt", "hit", "v", "cosine ≥ 0.075")
    d.edge("vv", "hit", "h")
    d.note(64, 545, "BY SPEECH", "#b48ead", 16)
    d.box("av", 64, 560, 300, 130, "Audio and video", ["MP3 M4A WAV OGG Opus", "MP4 MKV WEBM MOV, ≤ 3 h"], "user")
    d.box("pcm", 430, 560, 260, 130, "ffmpeg", ["mono 16 kHz PCM", "no PyAV"])
    d.box("wh", 750, 560, 280, 130, "Whisper base", ["faster-whisper int8", "timed segments"], "model")
    d.box("ch", 1090, 560, 220, 130, "Chunks", ["~90 words", "+ start time"], "data")
    d.box("use", 1360, 560, 180, 130, "Find + Ask", ["said at 0:02", "cite the moment"], "out")
    d.edge("av", "pcm", "h")
    d.edge("pcm", "wh", "h")
    d.edge("wh", "ch", "h")
    d.edge("ch", "use", "h")
    d.note(64, 760, "Speech chunks go through BM25, embeddings and PII detection like any document. Music with no speech is marked, so it is not read again.", "#8f949c")
    d.note(64, 786, "Find similar: the mean vector of a file (a video: its keyframes) is the query, similarity ≥ 0.62.", "#8f949c")
    return d


def ask() -> D:
    d = D("05", "Ask pipeline (RAG with citations)", "Answers only from your files. Every fact cites its source. No close source: it says so.")
    d.box("q", 64, 200, 260, 110, "Question", ['"What is the late fee', 'in my rent agreement?"'], "user")
    d.box("ret", 380, 200, 300, 130, "Retrieve chunks", ["BM25 + dense per chunk", "RRF · top 60", "≤ 2 chunks per file"], "data")
    d.box("rr", 740, 200, 260, 130, "Rerank", ["MiniLM top 20", "RRF with fused order"], "model")
    d.box("found", 1060, 200, 240, 130, "Close enough?", ["sim ≥ 0.38 or", "words ≥ 50%"], "guard")
    d.box("nf", 1360, 200, 180, 130, "Not found", ['"I did not find', 'this in your', 'files."'], "out")
    d.box("ctx", 1060, 400, 240, 130, "Parent context", ["neighbor chunks of", "the same section"])
    d.box("t2", 700, 400, 300, 130, "T2 local", ["qwen3:4b · 4 sources", "JSON {answer, sources}", "streamed"], "model")
    d.box("t3", 700, 590, 300, 130, "T3 cloud (Think harder)", ["10 sources, 6000 tokens", "after the redaction gate"], "guard")
    d.box("rest", 380, 480, 260, 130, "Restore", ["placeholders back", "to real values,", "on the laptop"])
    d.box("ans", 64, 480, 260, 150, "Answer", ['"Rs 500 for each day', 'of delay [1]"', "click [1]: the PDF", "opens at the page"], "out")
    d.edge("q", "ret", "h")
    d.edge("ret", "rr", "h")
    d.edge("rr", "found", "h")
    d.edge("found", "nf", "h", "no", color="#e5736a")
    d.edge("found", "ctx", "v", "yes", color="#8dbf93")
    d.edge("ctx", "t2", "h")
    d.edge("ctx", "t3", "vh", ofs=0)
    d.edge("t2", "rest", "h")
    d.edge("t3", "rest", "h")
    d.edge("rest", "ans", "h")
    d.note(64, 780, "A short follow-up (\"and the deposit?\") borrows the words of the earlier question. Lite mode: the best sentence of each source, with [n].", "#8f949c")
    d.note(64, 806, "12 test questions: the right source in all 12, \"not found\" for the 2 that have no answer.", "#7fa8d6")
    return d


def act() -> D:
    d = D("06", "Act pipeline (the agent)", "Plain words become steps. You review every plan first. One Ctrl+Z reverts the whole command.")
    d.box("cmd", 64, 210, 280, 120, "Command", ['"please, in Downloads', 'move all pdfs to Papers"'], "user")
    d.box("norm", 400, 210, 280, 130, "Normalize", ["drop polite words", "a place said first", "goes to its place"])
    d.box("gram", 740, 210, 260, 130, "Grammar", ["no model, fast", "find · convert · move", "copy · rename · mkdir …"])
    d.box("llm", 740, 400, 260, 120, "Local LLM", ["qwen3:4b, JSON", "schema of 10 ops"], "model")
    d.box("cloud", 740, 580, 260, 130, "Cloud (Think harder)", ["redacted context only:", "folder names, types"], "guard")
    d.box("res", 1060, 300, 240, 160, "Resolve + limits", ["paths resolved locally", "≤ 20 steps, ≤ 2000 files", "no system folders", "no shell tool"], "guard")
    d.box("rev", 1360, 210, 180, 140, "Review", ["every step", "shown first;", "trash: 2nd OK"], "user")
    d.box("run", 1360, 420, 180, 130, "Run", ["one journal", "group"], "out")
    d.box("undo", 1360, 610, 180, 110, "Ctrl+Z", ["reverts all", "steps"], "out")
    d.edge("cmd", "norm", "h")
    d.edge("norm", "gram", "h")
    d.edge("gram", "llm", "v", "no match", dashed=True)
    d.edge("llm", "cloud", "v", "Think harder", dashed=True)
    d.edge("gram", "res", "h", ofs=-10)
    d.edge("llm", "res", "h", ofs=-10)
    d.edge("cloud", "res", "h", ofs=-10)
    d.edge("res", "rev", "h")
    d.edge("rev", "run", "v")
    d.edge("run", "undo", "v")
    d.note(64, 790, "The command bar picks Act by itself: \"in desktop make a folder called notes\" → mkdir notes in Desktop. \"copy of trip photo\" stays a search.", "#8f949c")
    return d


def privacy() -> D:
    d = D("07", "Privacy: the router and the redaction gate", "The cloud only for hard tasks, only in Hybrid, only after on-device redaction. Every byte is in the ledger.")
    d.box("task", 64, 200, 240, 110, "A task", ["Ask · summary ·", "organize · plan"], "user")
    d.box("router", 360, 180, 300, 160, "Router: 4 conditions", ["1 heavy, or Think harder", "2 mode allows the cloud", "3 a provider is set", "4 the gate passes"], "guard")
    d.box("t2", 360, 420, 300, 110, "T2 local", ["qwen3:4b · nothing leaves"], "model")
    d.box("min", 720, 180, 260, 110, "Minimum payload", ["passages, not files"])
    d.box("l1", 720, 330, 260, 130, "Layer 1", ["patterns + checksums", "PAN · Aadhaar (Verhoeff)", "cards (Luhn) · IBAN"], "guard")
    d.box("l2", 720, 500, 260, 120, "Layer 2", ["OpenAI Privacy Filter", "q4 ONNX, 8 types"], "model")
    d.box("ph", 1040, 180, 240, 110, "Placeholders", ["[PERSON_1]", "[ACCOUNT_1]"])
    d.box("chk", 1040, 330, 240, 130, "Final check", ["layer 1 again", "high risk left:", "BLOCK"], "guard")
    d.box("led", 1040, 500, 240, 120, "Ledger", ["exact payload,", "bytes, provider"], "data")
    d.box("prov", 1330, 180, 210, 130, "Provider", ["Groq · Anthropic", "claude -p"], "out")
    d.box("rest", 1330, 400, 210, 130, "Restore", ["real values back", "in the answer,", "on the laptop"], "out")
    d.edge("task", "router", "h")
    d.edge("router", "t2", "v", "any no", color="#8dbf93")
    d.edge("router", "min", "h", "all yes", color="#e5736a")
    d.edge("min", "l1", "v")
    d.edge("l1", "l2", "v")
    d.edge("l2", "ph", "h")
    d.edge("ph", "chk", "v")
    d.edge("chk", "led", "v")
    d.edge("led", "prov", "h")
    d.edge("prov", "rest", "v")
    d.note(64, 690, "Also: Ask each time shows the payload first · folder rule \"Never to the cloud\" · encrypted index (SQLCipher) ·", "#8f949c")
    d.note(64, 714, "egress guard in the app · MCP answers pass the same gate · offline: LOCAL ONLY.", "#8f949c")
    d.note(64, 760, "PII recall on 60 test documents: layer 1 91% · layer 2 98% · both 99%.", "#7fa8d6", 16)
    return d


def space() -> D:
    d = D("08", "Space pipeline: find, plan, free safely", "Nothing changes in a scan. Every action is reviewed, checked, and has one undo.")
    d.box("scan", 64, 190, 280, 150, "Scan", ["one walk per drive", "sizes per category", "and age, largest files"], "data")
    d.box("rows", 400, 170, 300, 190, "Reclaim rows", ["caches · trash · junk", "large old files", "extracted archives", "duplicate folders", "two formats · Docker"])
    d.box("dup", 400, 400, 300, 160, "Duplicates", ["size → quick hash", "(64 KB) → BLAKE3", "photos: pHash ≤ 6 bits", "+ color check"])
    d.box("plan", 760, 190, 260, 150, "Plan", ['"Free 10 GB":', "safe rows first,", "reversible first"])
    d.box("rev", 1080, 190, 220, 150, "Review", ["flags: safe,", "check, permanent,", "admin"], "user")
    d.box("act", 1360, 190, 180, 150, "Act", ["trash · delete", "compress ·", "optimize"], "out")
    d.edge("scan", "rows", "h")
    d.edge("scan", "dup", "h")
    d.edge("rows", "plan", "h")
    d.edge("dup", "plan", "h")
    d.edge("plan", "rev", "h")
    d.edge("rev", "act", "h")
    d.note(64, 625, "SAFE COMPRESSION", "#d9b26a", 16)
    steps = [("c1", "Plan", ["7z · tar.zst · zip", "zstd · JPEG XL"]), ("c2", "Space check", ["enough free", "space first"]),
             ("c3", "Create", ["+ manifest", "inside"]), ("c4", "Test", ["extract to temp", "BLAKE3 each file"]),
             ("c5", "Trash originals", ["only if all", "hashes match"]), ("c6", "Restore", ["hash check, then", "times and modes"])]
    x = 64
    for i, (k, t, ls) in enumerate(steps):
        d.box(k, x, 640, 220, 120, t, ls, "guard" if k == "c4" else ("out" if k == "c6" else "proc"))
        if i:
            d.edge(steps[i - 1][0], k, "h")
        x += 248
    d.note(64, 820, "Lossy (AVIF, lossy JPEG XL, H.265): Suggest mode only, \"Not reversible\", same pixels and length checked. Auto mode never uses it.", "#8f949c")
    return d


def organize() -> D:
    d = D("09", "Organize and naming", "The cheapest step runs first. You see each decision and its reason, and one undo reverts the plan.")
    d.note(64, 170, "CATEGORY CASCADE", "#b48ead", 16)
    cas = [("r", "Rules", ["name, extension,", "folder"], "proc"), ("e", "Your examples", ["cosine ≥ 0.80", "Set category"], "data"),
           ("p", "Prototypes", ["19 categories", "≥ 0.32, gap 0.05"], "model"), ("l", "Local LLM", ["10 files per call", "name + text start"], "model")]
    x = 64
    for i, (k, t, ls, kind) in enumerate(cas):
        d.box(k, x, 190, 250, 115, t, ls, kind)
        if i:
            d.edge(cas[i - 1][0], k, "h", "not sure", dashed=True)
        x += 300
    d.box("plan", 1280, 190, 260, 115, "Plan a folder", ["before and after", "trees"], "out")
    d.edge("l", "plan", "h")
    d.box("edit", 1280, 360, 260, 130, "You edit", ["uncheck, drag,", "rename in place"], "user")
    d.box("apply", 1280, 540, 260, 110, "Apply", ["one journal group"], "out")
    d.box("learn", 930, 560, 290, 120, "Learn", ["kept folders per category", "3 same moves → a rule"], "data")
    d.edge("plan", "edit", "v")
    d.edge("edit", "apply", "v")
    d.edge("apply", "learn", "h", "", dashed=True)
    d.note(64, 395, "NAMES FOR UNNAMED FILES", "#b48ead", 16)
    nm = [("g", "Generic name?", ["IMG_2044, Untitled,", "Book1, a hash"], "guard"),
          ("s", "Best source", ["PDF title (largest text)", "OCR · SigLIP label", "transcript · local LLM"], "model"),
          ("st", "Your style", ["date place, case,", "separator"], "data")]
    x = 64
    for i, (k, t, ls, kind) in enumerate(nm):
        d.box(k, x, 415, 260, 135, t, ls, kind)
        if i:
            d.edge(nm[i - 1][0], k, "h")
        x += 290
    d.box("nrev", 64, 610, 780, 90, "Review → rename as one group", ["1706.03762.pdf → Attention Is All You Need.pdf · photo (4).jpg → Receipt.jpg"], "out")
    d.edge("st", "nrev", "v")
    d.note(64, 770, "Watch rules: move or name new files in a folder by category, extension or name, once each file is stable for 20 s. Each run has one undo.", "#8f949c")
    return d


DIAGRAMS = [("01-architecture", architecture), ("02-index", index), ("03-find", find), ("04-media", media), ("05-ask", ask),
            ("06-act", act), ("07-privacy", privacy), ("08-space", space), ("09-organize", organize)]


def main() -> None:
    rs = shutil.which("rsvg-convert")
    for name, fn in DIAGRAMS:
        svg = HERE / f"{name}.svg"
        svg.write_text(fn().svg())
        if rs:
            subprocess.run([rs, "-w", str(W), "-h", str(H), str(svg), "-o", str(HERE / f"{name}.png")], check=True)
        print(name)


if __name__ == "__main__":
    main()
