"""Models on first run (section 13.12, packaging): what Raven needs, what is here, and the downloads.

A packaged Raven ships no model files. The first-run screen lists these, shows what each one adds, and
downloads them into <data dir>/models with progress events. Each file goes to a .part file first, so a
stopped download never leaves a broken model. Ollama models download through Ollama's own /api/pull.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import httpx

from . import config, events

log = logging.getLogger("raven.models")

HF = "https://huggingface.co"

CATALOG: dict[str, dict] = {
    "siglip2": {
        "label": "SigLIP 2 base", "adds": "Find photos, PDF pages and video moments by what they show", "mb": 660,
        "repo": "onnx-community/siglip2-base-patch16-224-ONNX",
        "files": ["config.json", "preprocessor_config.json", "tokenizer.json", "onnx/vision_model.onnx", "onnx/text_model_quantized.onnx"],
    },
    "reranker": {
        "label": "MiniLM reranker", "adds": "Better order of Find and Ask results", "mb": 23,
        "repo": "Xenova/ms-marco-MiniLM-L-6-v2",
        "files": ["config.json", "tokenizer.json", "onnx/model_quantized.onnx"],
    },
    "whisper-base": {
        "label": "Whisper base", "adds": "Search what people say in audio and video", "mb": 142,
        "repo": "Systran/faster-whisper-base",
        "files": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
    },
    "privacy-filter": {
        "label": "Privacy Filter (q4)", "adds": "Finds names, addresses and accounts that patterns miss, before any cloud call", "mb": 945,
        "repo": "openai/privacy-filter",
        "files": ["config.json", "tokenizer.json", "tokenizer_config.json", "viterbi_calibration.json", "onnx/model_q4.onnx", "onnx/model_q4.onnx_data"],
    },
    # Strict privacy (PLAN4 7.3): GLiNER PII, quint8 ONNX, Apache-2.0.
    "gliner-pii": {
        "label": "GLiNER PII (quint8)", "adds": "Strict privacy: finds the names and addresses that the Privacy Filter misses", "mb": 205,
        "repo": "knowledgator/gliner-pii-base-v1.0",
        "files": ["gliner_config.json", "tokenizer.json", "onnx/model_quint8.onnx"],
    },
    # Lite mode (PLAN2 P2-6): static embeddings on the CPU, no Ollama. One table lookup per token.
    "potion-retrieval-32m": {
        "label": "Potion retrieval (static)", "adds": "Search by meaning without Ollama (lite mode)", "mb": 128,
        "repo": "minishlab/potion-retrieval-32M",
        "files": ["config.json", "model.safetensors", "tokenizer.json"],
    },
    "potion-base-8m": {
        "label": "Potion base (static)", "adds": "Search by meaning without Ollama, smallest (lite mode)", "mb": 30,
        "repo": "minishlab/potion-base-8M",
        "files": ["config.json", "model.safetensors", "tokenizer.json"],
    },
}
OLLAMA = {
    "embeddinggemma": {"label": "EmbeddingGemma 300M", "adds": "Search by meaning (needed for Ask)", "mb": 620},
    "qwen3:4b": {"label": "Qwen3 4B", "adds": "Ask, summaries, organize plans and the agent, on this computer", "mb": 2500},
    # The other sizes of the local model (PLAN4 section 5.2): the device check picks one by RAM and GPU.
    "qwen3:1.7b": {"label": "Qwen3 1.7B", "adds": "The local model for 8 GB of RAM: faster, less exact", "mb": 1400},
    "qwen3:8b": {"label": "Qwen3 8B", "adds": "The local model for a GPU with 8 GB or a large Mac: more exact", "mb": 5200},
}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _dir(name: str) -> Path:
    return config.MODELS_DIR / name


def present(name: str) -> bool:
    return all((_dir(name) / f).exists() for f in CATALOG[name]["files"])


def _ollama_names() -> set[str] | None:
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2)
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:  # noqa: BLE001
        return None


def status() -> dict:
    names = _ollama_names()
    files = [{"name": k, **{x: v[x] for x in ("label", "adds", "mb")}, "present": present(k), "job": _jobs.get(k)} for k, v in CATALOG.items()]
    oll = [{"name": k, **v, "present": names is not None and (k in names or f"{k}:latest" in names), "job": _jobs.get(f"ollama:{k}")}
           for k, v in OLLAMA.items()]
    return {"dir": str(config.MODELS_DIR), "files": files, "ollama": oll, "ollama_running": names is not None}


def _emit(key: str) -> None:
    events.publish("models_progress", key=key, job=_jobs.get(key))


def _download(name: str) -> None:
    spec = CATALOG[name]
    job = _jobs[name]
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=10)) as c:
            sizes = {}
            for f in spec["files"]:
                h = c.head(f"{HF}/{spec['repo']}/resolve/main/{f}")
                sizes[f] = int(h.headers.get("x-linked-size") or h.headers.get("content-length") or 0)
            job["total"] = sum(sizes.values())
            for f in spec["files"]:
                out = _dir(name) / f
                if out.exists() and (not sizes[f] or out.stat().st_size == sizes[f]):
                    job["done"] += sizes[f]
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                part = out.with_name(out.name + ".part")
                with c.stream("GET", f"{HF}/{spec['repo']}/resolve/main/{f}") as r:
                    r.raise_for_status()
                    with open(part, "wb") as fh:
                        last = 0
                        for chunk in r.iter_bytes(1024 * 1024):
                            if job.get("cancel"):
                                raise RuntimeError("cancelled")
                            fh.write(chunk)
                            job["done"] += len(chunk)
                            if job["done"] - last > 8 * 1024 * 1024:
                                last = job["done"]
                                _emit(name)
                part.replace(out)
        job["state"] = "done"
    except Exception as e:  # noqa: BLE001
        job.update(state="error", error=str(e)[:200])
        log.warning("model %s: %s", name, e)
    _emit(name)


def _pull(model: str) -> None:
    key = f"ollama:{model}"
    job = _jobs[key]
    try:
        import json

        with httpx.stream("POST", f"{config.OLLAMA_URL}/api/pull", json={"model": model}, timeout=httpx.Timeout(None, connect=5)) as r:
            last = 0
            for line in r.iter_lines():
                if not line:
                    continue
                e = json.loads(line)
                if e.get("error"):
                    raise RuntimeError(e["error"])
                if e.get("total"):
                    job["total"], job["done"] = e["total"], e.get("completed", 0)
                if job["done"] - last > 16 * 1024 * 1024 or e.get("status") == "success":
                    last = job["done"]
                    _emit(key)
        job["state"] = "done"
    except Exception as e:  # noqa: BLE001
        job.update(state="error", error=str(e)[:200])
    _emit(key)


def download(names: list[str]) -> dict:
    with _lock:
        for n in names:
            if n.startswith("ollama:"):
                model = n.split(":", 1)[1]
                if model not in OLLAMA or (_jobs.get(n) or {}).get("state") == "running":
                    continue
                _jobs[n] = {"state": "running", "done": 0, "total": 0, "error": None}
                threading.Thread(target=_pull, args=(model,), name=f"pull-{model}", daemon=True).start()
            elif n in CATALOG and not present(n) and (_jobs.get(n) or {}).get("state") != "running":
                _jobs[n] = {"state": "running", "done": 0, "total": 0, "error": None}
                threading.Thread(target=_download, args=(n,), name=f"model-{n}", daemon=True).start()
    return status()


def ollama_has(model: str, names: set[str] | None) -> bool:
    return names is not None and (model in names or f"{model}:latest" in names)


def remove(name: str) -> None:
    """Delete a model to free its disk space: the files in the models folder, or the model in Ollama."""
    import shutil

    if name.startswith("ollama:"):
        model = name.split(":", 1)[1]
        r = httpx.request("DELETE", f"{config.OLLAMA_URL}/api/delete", json={"model": model}, timeout=30)
        if r.status_code not in (200, 404):
            raise RuntimeError(f"Ollama could not delete {model}: {r.text[:120]}")
    elif name in CATALOG:
        shutil.rmtree(_dir(name), ignore_errors=True)
    else:
        raise ValueError(f"Unknown model {name}")
    _jobs.pop(name, None)
