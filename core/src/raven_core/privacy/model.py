"""PII layer 2 (section 10.3 step 4): OpenAI Privacy Filter, q4 ONNX on the CPU.

The model tags tokens with BIOES labels for 8 entity types. Raven merges the tags into
character spans with the tokenizer offsets. Without the model files, layer 2 is off and
the gate says so.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from .rules import Span

log = logging.getLogger("raven.pii")

from ..config import MODELS_DIR

MODEL_DIR = Path(os.environ.get("RAVEN_PII_MODEL") or MODELS_DIR / "privacy-filter")
MAX_TOKENS = 512
STRIDE = 448

LABEL_TO_TYPE = {
    "account_number": "ACCOUNT", "private_address": "ADDRESS", "private_date": "DATE", "private_email": "EMAIL",
    "private_person": "PERSON", "private_phone": "PHONE", "private_url": "URL", "secret": "SECRET",
}


class PrivacyFilter:
    def __init__(self) -> None:
        self._sess = None
        self._tok = None
        self._labels: list[str] = []
        self._lock = threading.Lock()
        self.error: str | None = None
        self.load_ms: float | None = None

    def available(self) -> bool:
        return (MODEL_DIR / "onnx" / "model_q4.onnx").exists() and (MODEL_DIR / "tokenizer.json").exists()

    def _load(self) -> bool:
        if self._sess is not None:
            return True
        if not self.available():
            self.error = "Privacy Filter model files are not in models/privacy-filter"
            return False
        try:
            import json

            import onnxruntime as ort
            from tokenizers import Tokenizer

            t0 = time.perf_counter()
            cfg = json.loads((MODEL_DIR / "config.json").read_text())
            self._labels = [cfg["id2label"][str(i)] for i in range(len(cfg["id2label"]))]
            opts = ort.SessionOptions()
            # Few threads: the model must not starve the local LLM or the UI (it runs on the CPU).
            opts.intra_op_num_threads = int(os.environ.get("RAVEN_PII_THREADS", "2"))
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._sess = ort.InferenceSession(str(MODEL_DIR / "onnx" / "model_q4.onnx"), opts, providers=["CPUExecutionProvider"])
            self._tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
            self.load_ms = (time.perf_counter() - t0) * 1000
            log.info("privacy filter loaded in %.0f ms", self.load_ms)
            self.error = None
            return True
        except Exception as e:  # noqa: BLE001
            self.error = f"Privacy Filter failed to load: {e}"
            log.warning(self.error)
            self._sess = None
            return False

    def detect(self, text: str) -> list[Span]:
        if not text.strip():
            return []
        with self._lock:
            if not self._load():
                return []
            from ..memory import unload_when_idle

            self._used = time.monotonic()
            unload_when_idle(self, "privacy filter", ("_sess", "_tok"))
            enc = self._tok.encode(text, add_special_tokens=False)
            ids, offsets = enc.ids, enc.offsets
            spans: list[Span] = []
            # Long text: overlapping windows. A token keeps the tag from the window where it sits most centrally.
            best: dict[int, tuple[float, int, float]] = {}
            start = 0
            while True:
                window = ids[start: start + MAX_TOKENS]
                if not window:
                    break
                feeds = {"input_ids": np.array([window], dtype=np.int64), "attention_mask": np.ones((1, len(window)), dtype=np.int64)}
                names = {i.name for i in self._sess.get_inputs()}
                feeds = {k: v for k, v in feeds.items() if k in names}
                logits = self._sess.run(None, feeds)[0][0]
                probs = np.exp(logits - logits.max(-1, keepdims=True))
                probs /= probs.sum(-1, keepdims=True)
                for j in range(len(window)):
                    centrality = min(j, len(window) - 1 - j)
                    k = start + j
                    if k not in best or centrality > best[k][0]:
                        lab = int(probs[j].argmax())
                        best[k] = (centrality, lab, float(probs[j][lab]))
                if start + MAX_TOKENS >= len(ids):
                    break
                start += STRIDE
            cur_type, cur_start, cur_end, scores = None, 0, 0, []

            def close():
                if cur_type and scores:
                    spans.append(Span(cur_type, cur_start, cur_end, float(np.mean(scores)), layer=2))

            for k in range(len(ids)):
                _, lab, p = best[k]
                tag = self._labels[lab]
                a, b = offsets[k]
                if tag == "O":
                    close()
                    cur_type, scores = None, []
                    continue
                prefix, _, name = tag.partition("-")
                typ = LABEL_TO_TYPE.get(name, name.upper())
                if prefix in ("B", "S") or typ != cur_type:
                    close()
                    cur_type, cur_start, cur_end, scores = typ, a, b, [p]
                else:
                    cur_end = b
                    scores.append(p)
                if prefix in ("E", "S"):
                    close()
                    cur_type, scores = None, []
            close()
        # Trim spaces that the tokenizer put inside the span.
        out = []
        for s in spans:
            st, en = s.start, s.end
            while st < en and text[st].isspace():
                st += 1
            while en > st and text[en - 1].isspace():
                en -= 1
            if en > st:
                out.append(Span(s.type, st, en, s.score, 2))
        return out


model = PrivacyFilter()
