"""Reranker (section 9.2 step 6): a cross-encoder reads the query and each candidate together.

The model is ms-marco-MiniLM-L6 (int8 ONNX, 23 MB) on the CPU. The plan names bge-reranker-v2-m3 first,
but this laptop runs ONNX on the CPU only, and that model is 571 MB in int8. The small model keeps a
30-candidate rerank near 100 ms, inside the Find budget (5.1). It reads English best.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..config import MODELS_DIR

log = logging.getLogger("raven.rerank")

DIR = MODELS_DIR / "reranker"
MODEL = DIR / "onnx" / "model_quantized.onnx"
MAX_LEN = 160


class Reranker:
    def __init__(self) -> None:
        self._sess = None
        self._tok = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def available(self) -> bool:
        return MODEL.exists() and (DIR / "tokenizer.json").exists()

    def _load(self) -> bool:
        if self._sess is not None:
            return True
        if not self.available():
            self.error = "Reranker model files are not in models/reranker"
            return False
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(MODEL), opts, providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(str(DIR / "tokenizer.json"))
        self._tok.enable_truncation(MAX_LEN, strategy="only_second")
        self._tok.enable_padding()
        return True

    def warm(self) -> None:
        try:
            self.scores("warm", ["up"])
        except Exception:  # noqa: BLE001
            log.exception("reranker warm-up failed")

    def scores(self, query: str, passages: list[str]) -> list[float]:
        """One relevance logit per passage. Higher is better."""
        if not passages:
            return []
        with self._lock:
            if not self._load():
                raise RuntimeError(self.error)
            from ..memory import unload_when_idle

            self._used = time.monotonic()
            unload_when_idle(self, "reranker", ("_sess", "_tok"))
            t0 = time.perf_counter()
            enc = self._tok.encode_batch([(query, p) for p in passages])
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
            }
            out = self._sess.run(["logits"], feed)[0][:, 0]
            log.debug("rerank %d in %.0f ms", len(passages), (time.perf_counter() - t0) * 1000)
        return [float(x) for x in out]


model = Reranker()
