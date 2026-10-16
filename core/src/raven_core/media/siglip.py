"""SigLIP 2 base (section 8): images and text in one vector space, ONNX on the CPU.

The vision model loads only while the media job runs and unloads after IDLE_UNLOAD seconds (5.3).
The text model is small (int8) and stays loaded after the first visual query.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..config import MODELS_DIR

log = logging.getLogger("raven.siglip")

DIR = MODELS_DIR / "siglip2"
VISION = DIR / "onnx" / "vision_model.onnx"
TEXT = DIR / "onnx" / "text_model_quantized.onnx"
TOKENIZER = DIR / "tokenizer.json"
SIZE = 224
TEXT_LEN = 64
DIM = 768
IDLE_UNLOAD = 90.0
THREADS = 4


def available() -> bool:
    return VISION.exists() and TEXT.exists() and TOKENIZER.exists()


def _session(path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    # Few threads: the model must not starve the UI, the LLM or the indexer.
    opts.intra_op_num_threads = THREADS
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def _norm(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def pixels(img) -> np.ndarray:
    """A PIL image to the model input: RGB, 224 x 224 (no crop, as the SigLIP processor), scaled to [-1, 1]."""
    from PIL import Image

    im = img.convert("RGB").resize((SIZE, SIZE), Image.BICUBIC)
    a = np.asarray(im, dtype=np.float32) / 127.5 - 1.0
    return a.transpose(2, 0, 1)


class SigLIP:
    def __init__(self) -> None:
        self._vision = None
        self._text = None
        self._tok = None
        self._lock = threading.Lock()
        self._last_vision = 0.0
        self.error: str | None = None
        threading.Thread(target=self._reaper, name="siglip-unload", daemon=True).start()

    def _reaper(self) -> None:
        while True:
            time.sleep(15)
            with self._lock:
                if self._vision is not None and time.monotonic() - self._last_vision > IDLE_UNLOAD:
                    self._vision = None
                    from .. import platform

                    platform.trim_memory()
                    log.info("SigLIP vision model unloaded")

    def embed_images(self, batch: list[np.ndarray]) -> np.ndarray:
        """Inputs from pixels(). Returns unit vectors, one row per image."""
        with self._lock:
            if self._vision is None:
                t0 = time.perf_counter()
                self._vision = _session(VISION)
                log.info("SigLIP vision model loaded in %.1f s", time.perf_counter() - t0)
            self._last_vision = time.monotonic()
            out = self._vision.run(["pooler_output"], {"pixel_values": np.stack(batch).astype(np.float32)})[0]
            self._last_vision = time.monotonic()
        return _norm(out)

    def embed_text(self, text: str) -> np.ndarray:
        with self._lock:
            if self._text is None:
                from tokenizers import Tokenizer

                self._tok = Tokenizer.from_file(str(TOKENIZER))
                self._tok.enable_padding(length=TEXT_LEN, pad_id=0)
                self._tok.enable_truncation(TEXT_LEN)
                self._text = _session(TEXT)
            # SigLIP was trained on lower-case text.
            ids = np.array([self._tok.encode(text.lower()).ids], dtype=np.int64)
            out = self._text.run(["pooler_output"], {"input_ids": ids})[0]
        return _norm(out)[0]

    def loaded(self) -> dict:
        return {"vision": self._vision is not None, "text": self._text is not None}


model = SigLIP()
