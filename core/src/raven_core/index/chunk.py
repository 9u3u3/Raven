"""Chunking for search and RAG (section 9.1).

Chunks are about 350 tokens with a 50-token overlap. Raven counts words and
uses 1 token = 0.75 words, which is close enough for this purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Section

TARGET_WORDS = 260
OVERLAP_WORDS = 38

_WORD = re.compile(r"\S+")


@dataclass
class Chunk:
    text: str
    page: int | None
    heading: str
    start: int
    end: int


def chunk_section(sec: Section) -> list[Chunk]:
    words = [(m.start(), m.end()) for m in _WORD.finditer(sec.text)]
    if not words:
        return []
    out = []
    i = 0
    n = len(words)
    while i < n:
        j = min(n, i + TARGET_WORDS)
        # Prefer to end at a paragraph or sentence break in the last fifth of the window.
        if j < n:
            best = None
            for k in range(j - 1, i + int(TARGET_WORDS * 0.8), -1):
                gap = sec.text[words[k][1]: words[k + 1][0]]
                if "\n\n" in gap:
                    best = k + 1
                    break
                if best is None and sec.text[words[k][1] - 1] in ".!?":
                    best = k + 1
            if best:
                j = best
        s, e = words[i][0], words[j - 1][1]
        out.append(Chunk(sec.text[s:e], sec.page, sec.heading, s, e))
        if j >= n:
            break
        i = max(j - OVERLAP_WORDS, i + 1)
    return out


def header(name: str, folder: str, title: str, heading: str) -> str:
    """The contextual header that goes in front of each chunk before embedding."""
    parts = [f"File: {name}", f"Folder: {folder}"]
    if title:
        parts.append(f"Title: {title}")
    if heading and heading != title:
        parts.append(f"Section: {heading}")
    return " | ".join(parts)
