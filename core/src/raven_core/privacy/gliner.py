"""Strict privacy (PLAN4 7.3): a second layer-2 model, GLiNER PII (knowledgator/gliner-pii-base-v1.0, quint8 ONNX).

The 7.3 test showed that the Privacy Filter misses about half of the names and most addresses in hand-written
text. GLiNER found 92 % of the names in the same set. Strict mode runs both models and joins their spans.

GLiNER is a span model: the input is a prompt with the labels, then the words of the text. The model gives a
score for each label and each span of up to max_width words. This module builds the inputs as the gliner package
does (span mode markerV0, first-subtoken pooling, whitespace words) with onnxruntime and tokenizers only, so the
sidecar needs no torch.

GLiNER looks only for the types that the Privacy Filter misses. Other labels (API keys, PAN, passwords) gave
false hits on code words such as FastAPI and BM25, and layer 1 finds those types with checksums.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

from ..config import MODELS_DIR
from .model import threads
from .rules import Span

log = logging.getLogger("raven.pii")

MODEL_DIR = Path(os.environ.get("RAVEN_GLINER_MODEL") or MODELS_DIR / "gliner-pii")
ONNX = "onnx/model_quint8.onnx"
LABELS = {"person": "PERSON", "address": "ADDRESS", "date of birth": "DOB", "medical condition": "HEALTH"}
THRESHOLD = 0.4  # PLAN4 7.3: 0.4 finds more names than 0.5, with no false hits in the README. 0.3 reads Raven and Groq as names.
WINDOW = 200  # words per window: the labels and 200 words stay under the 512 tokens of DeBERTa
STRIDE = 176
BATCH = 1  # quint8 quantizes each batch as a whole, so padding changes the result. Batch 1 is also the fastest.
WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")  # the gliner WhitespaceTokenSplitter


class Gliner:
    def __init__(self) -> None:
        self._sess = None
        self._tok = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self.load_ms: float | None = None
        self.max_width = 12
        self.ent, self.sep = "<<ENT>>", "<<SEP>>"

    def available(self) -> bool:
        return all((MODEL_DIR / f).exists() for f in (ONNX, "tokenizer.json", "gliner_config.json"))

    def _load(self) -> bool:
        if self._sess is not None:
            return True
        if not self.available():
            self.error = "The strict privacy model files are not in models/gliner-pii"
            return False
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            t0 = time.perf_counter()
            cfg = json.loads((MODEL_DIR / "gliner_config.json").read_text())
            if cfg.get("span_mode") != "markerV0" or cfg.get("subtoken_pooling", "first") != "first":
                raise ValueError(f"unsupported GLiNER config: {cfg.get('span_mode')}, {cfg.get('subtoken_pooling')}")
            self.max_width = int(cfg.get("max_width") or 12)
            self.ent, self.sep = cfg.get("ent_token") or self.ent, cfg.get("sep_token") or self.sep
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = int(os.environ.get("RAVEN_PII_THREADS") or threads())
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._sess = ort.InferenceSession(str(MODEL_DIR / ONNX), opts, providers=["CPUExecutionProvider"])
            self._tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
            self._tok.no_truncation()
            self._tok.no_padding()
            for t in (self.ent, self.sep):
                if self._tok.token_to_id(t) is None:
                    raise ValueError(f"the tokenizer has no {t} token")
            self.load_ms = (time.perf_counter() - t0) * 1000
            log.info("strict privacy model loaded in %.0f ms", self.load_ms)
            self.error = None
            return True
        except Exception as e:  # noqa: BLE001
            self.error = f"The strict privacy model failed to load: {e}"
            log.warning(self.error)
            self._sess = None
            return False

    def _touch(self) -> None:
        from ..memory import unload_when_idle

        self._used = time.monotonic()
        unload_when_idle(self, "strict privacy model", ("_sess", "_tok"))

    def warm(self) -> None:
        if self._sess is not None or not self.available():
            return
        with self._lock:
            if self._load():
                self._touch()

    def _feeds(self, windows: list[list[str]]) -> tuple[dict, list[int]]:
        """The six model inputs for a batch of word windows, padded to the longest."""
        prompt: list[str] = []
        for label in LABELS:
            prompt += [self.ent, label]
        prompt.append(self.sep)
        rows, masks = [], []
        for words in windows:
            enc = self._tok.encode(prompt + words, is_pretokenized=True)
            mask, prev = [], None
            for wid in enc.word_ids:
                # First subtoken of each text word: its word number, from 1. Prompt words and special tokens: 0.
                mask.append(wid - len(prompt) + 1 if wid is not None and wid >= len(prompt) and wid != prev else 0)
                prev = wid
            rows.append(enc.ids)
            masks.append(mask)
        n_tok = max(len(r) for r in rows)
        n_words = max(len(w) for w in windows)
        k = self.max_width
        ids = np.zeros((len(rows), n_tok), dtype=np.int64)
        att = np.zeros_like(ids)
        wm = np.zeros_like(ids)
        for i, (r, m) in enumerate(zip(rows, masks)):
            ids[i, :len(r)] = r
            att[i, :len(r)] = 1
            wm[i, :len(m)] = m
        starts = np.repeat(np.arange(n_words), k)
        ends = starts + np.tile(np.arange(k), n_words)
        span_idx = np.broadcast_to(np.stack([starts, ends], -1), (len(rows), n_words * k, 2)).astype(np.int64)
        lengths = np.array([[len(w)] for w in windows], dtype=np.int64)
        span_mask = ends[None, :] < lengths
        return {"input_ids": ids, "attention_mask": att, "words_mask": wm, "text_lengths": lengths,
                "span_idx": np.ascontiguousarray(span_idx), "span_mask": span_mask}, [len(w) for w in windows]

    def _decode(self, logits: np.ndarray, n: int, threshold: float) -> list[tuple[int, int, int, float]]:
        """Spans above the threshold, then greedy: the best score first, no two spans share a word."""
        probs = 1 / (1 + np.exp(-logits[:n]))
        found = []
        for s, w, c in zip(*np.nonzero(probs > threshold)):
            if s + w < n:
                found.append((int(s), int(s + w), int(c), float(probs[s, w, c])))
        found.sort(key=lambda x: -x[3])
        keep: list[tuple[int, int, int, float]] = []
        for f in found:
            if all(f[1] < g[0] or g[1] < f[0] for g in keep):
                keep.append(f)
        return keep

    def detect(self, text: str, threshold: float | None = None) -> list[Span]:
        """`threshold`: a lower value gives the weak finds too (the cloud review shows them as suspicious words)."""
        if not text.strip():
            return []
        words = [(m.start(), m.end()) for m in WORD.finditer(text)]
        if not words:
            return []
        with self._lock:
            if not self._load():
                return []
            self._touch()
            starts = list(range(0, max(1, len(words) - WINDOW + STRIDE), STRIDE)) or [0]
            windows = [(st, [text[a:b] for a, b in words[st:st + WINDOW]]) for st in starts]
            spans: list[Span] = []
            types = list(LABELS.values())
            for i in range(0, len(windows), BATCH):
                part = windows[i:i + BATCH]
                feeds, lens = self._feeds([w for _, w in part])
                logits = self._sess.run(None, feeds)[0]
                for (st, _), lg, n in zip(part, logits, lens):
                    for a, b, c, p in self._decode(lg, n, THRESHOLD if threshold is None else threshold):
                        s = Span(types[c], words[st + a][0], words[st + b][1], p, layer=2)
                        # A name has no digits: the model can read a code word such as BM25 as a name.
                        if not (s.type == "PERSON" and any(ch.isdigit() for ch in text[s.start:s.end])):
                            spans.append(s)
        # A span never crosses a line: each line of a batch of file names is one name (Redactor.redact_names).
        out = []
        for s in spans:
            a = s.start
            for line in re.finditer(r"[^\n]+", text[s.start:s.end]):
                out.append(Span(s.type, a + line.start(), a + line.end(), s.score, 2))
        return out


model = Gliner()
