"""Features and models (PLAN4 section 5.2): one catalog for the first-start picker and for Settings.

Each feature says what it adds, the models and programs it needs, and a fit rule. The fit rule reads the
device report (device.py) and gives one of three levels, each with a short reason:

    good  "Runs well"
    slow  "Runs slowly"
    no    "Not recommended"

A feature with sizes (the local model, the meaning model) has one variant for each size. The picker
recommends the largest variant that runs well. The user can pick any variant and any feature: a
"Not recommended" choice gets a warning, not a block.

apply() turns the features on or off (the lite.py switches), sets the chosen variants and starts the
downloads. The "features_pending" setting keeps every chosen model until it is here, so loop() starts it
again after a quit or a network error, and starts an Ollama model once Ollama runs (resume()).
"""

from __future__ import annotations

import logging
import shutil
import time

from . import db, device, lite, models, setup

log = logging.getLogger("raven.features")

GB = 1024**3
MB = 1024**2

# id: label, what it adds, the lite.py switch, the variants or the models, the programs, the RAM in use (GB)
CATALOG: list[dict] = [
    {"id": "names", "label": "Find by name", "adds": "Find files and folders by name as you type.", "kind": "builtin"},
    {"id": "meaning", "label": "Find by meaning", "adds": "Find files by what they say. Ask uses it for its sources.",
     "kind": "model", "always": True,
     "variants": [
         {"id": "embeddinggemma", "label": "EmbeddingGemma 300M", "note": "best results, needs Ollama",
          "models": ["ollama:embeddinggemma"], "ram_use": 1.0, "ram_good": 8, "ram_min": 6},
         {"id": "potion-retrieval-32m", "label": "Potion 32M", "note": "light, no Ollama",
          "models": ["potion-retrieval-32m"], "ram_use": 0.3, "ram_good": 6, "ram_min": 3},
         {"id": "potion-base-8m", "label": "Potion 8M", "note": "lightest, no Ollama",
          "models": ["potion-base-8m"], "ram_use": 0.15, "ram_good": 2, "ram_min": 1},
     ]},
    {"id": "llm", "label": "Local AI model", "adds": "Ask, summaries, organize plans and the agent, on this computer.",
     "kind": "model", "switch": "llm",
     "variants": [
         {"id": "qwen3:8b", "label": "Qwen3 8B", "note": "most exact", "models": ["ollama:qwen3:8b"],
          "ram_use": 6.5, "gpu_good": 8, "ram_good": 32, "ram_min": 16, "threads_good": 12},
         {"id": "qwen3:4b", "label": "Qwen3 4B", "note": "the usual choice", "models": ["ollama:qwen3:4b"],
          "ram_use": 4.0, "gpu_good": 4, "ram_good": 12, "ram_min": 8, "threads_good": 8},
         {"id": "qwen3:1.7b", "label": "Qwen3 1.7B", "note": "fast, less exact", "models": ["ollama:qwen3:1.7b"],
          "ram_use": 2.0, "gpu_good": 2, "ram_good": 8, "ram_min": 6, "threads_good": 4},
     ]},
    {"id": "reranker", "label": "Better result order", "adds": "A small model sorts the Find and Ask results again.",
     "kind": "model", "switch": "reranker", "models": ["reranker"], "ram_use": 0.2, "ram_good": 4, "ram_min": 2,
     "threads_good": 4},
    {"id": "image", "label": "Find photos by what they show", "adds": "Search photos, PDF pages and video frames with words.",
     "kind": "model", "switch": "image", "models": ["siglip2"], "ram_use": 1.2, "ram_good": 8, "ram_min": 6,
     "threads_good": 4},
    {"id": "speech", "label": "Search what people say", "adds": "Transcripts of audio and video files, so you can search them.",
     "kind": "model", "switch": "speech", "models": ["whisper-base"], "tools": ["ffmpeg"], "ram_use": 0.6,
     "ram_good": 8, "ram_min": 4, "threads_good": 4},
    {"id": "privacy", "label": "Privacy Filter", "adds": "Finds names, addresses and accounts before any cloud call. Cloud AI needs it.",
     "kind": "model", "switch": "pii_index", "models": ["privacy-filter"], "ram_use": 1.5, "ram_good": 8, "ram_min": 6,
     "threads_good": 4},
    {"id": "strict", "label": "Strict privacy", "adds": "A second model finds the names, addresses and health words that the "
     "Privacy Filter misses, before any cloud call. Cloud calls take about 1.5 s more for each 1,000 words.",
     "kind": "model", "switch": "pii_strict", "models": ["gliner-pii"], "needs": ["privacy"], "ram_use": 0.9, "ram_good": 8,
     "ram_min": 6, "threads_good": 4},
    {"id": "galaxy", "label": "File map by meaning", "adds": "A map of your files in groups of the same topic.",
     "kind": "model", "switch": "galaxy", "models": [], "needs": ["meaning"], "ram_use": 0.5, "ram_good": 8, "ram_min": 4},
    {"id": "ocr", "label": "Read scans", "adds": "Read the text in scans and screenshots (OCR).", "kind": "tool", "tools": ["tesseract"]},
    {"id": "convert", "label": "Convert documents", "adds": "Convert Word, Excel, PowerPoint, Markdown and HTML files.",
     "kind": "tool", "tools": ["soffice", "pandoc"]},
    {"id": "media", "label": "Convert images and video", "adds": "Video frames, audio, and image formats.",
     "kind": "tool", "tools": ["ffmpeg", "magick"]},
    {"id": "network", "label": "Network shares and cloud storage", "adds": "Windows shares, SFTP, Google Drive, Dropbox and S3.",
     "kind": "tool", "tools": ["gio", "rclone"]},
]
BY_ID = {f["id"]: f for f in CATALOG}
# Network tools differ by system: gio is a Linux program.
_SYSTEM_TOOLS = {"darwin": {"gio"}, "windows": {"gio"}}


def _at_least(total: int, n: float) -> bool:
    """A "16 GB" laptop reports about 15.3 GB: allow 10 % below the number."""
    return total >= n * GB * 0.9


def _gb(n: float) -> str:
    return f"{n:.1f} GB" if n < 10 else f"{round(n)} GB"


def _size_mb(names: list[str]) -> int:
    mb = 0
    for n in names:
        if n.startswith("ollama:"):
            mb += models.OLLAMA.get(n.split(":", 1)[1], {}).get("mb", 0)
        elif n in models.CATALOG:
            mb += models.CATALOG[n]["mb"]
    return mb


def fit(spec: dict, d: dict, download_mb: int = 0) -> dict:
    """The fit level and its reason for one feature or variant on this device."""
    ram = d["ram"]["total"]
    gpu = d.get("gpu_memory") or 0
    threads = d["cpu"]["threads"] or 1
    bench = d.get("bench")
    free = d["disk"]["free"]
    need_disk = download_mb * MB * 1.2 + GB
    if download_mb and free < need_disk:
        return {"level": "no", "reason": f"Not enough disk space: needs {_gb(need_disk / GB)}, {_gb(free / GB)} free."}
    if "ram_min" not in spec:
        return {"level": "good", "reason": ""}
    ram_s = _gb(ram / GB)
    if not _at_least(ram, spec["ram_min"]):
        return {"level": "no", "reason": f"Needs {spec['ram_min']} GB of RAM, this computer has {ram_s}."}
    gpu_good = spec.get("gpu_good")
    if gpu_good and gpu >= gpu_good * GB * 0.9:
        where = "the Apple GPU" if any(g["vendor"] == "apple" for g in d["gpus"]) else "the GPU"
        return {"level": "good", "reason": f"Runs on {where} ({_gb(gpu / GB)})."}
    slow_cpu = bench is not None and bench < 40
    if _at_least(ram, spec["ram_good"]) and threads >= spec.get("threads_good", 1) and not (gpu_good and slow_cpu):
        return {"level": "good", "reason": f"{ram_s} of RAM, {threads} threads." if gpu_good else ""}
    if gpu_good:
        return {"level": "slow", "reason": f"Runs on the CPU with {ram_s} of RAM: answers can take 10 to 30 s."}
    if threads < spec.get("threads_good", 1):
        return {"level": "slow", "reason": f"{threads} CPU threads: indexing takes longer."}
    return {"level": "slow", "reason": f"{ram_s} of RAM: other apps can get slower while it runs."}


def _recommend(variants: list[dict], d: dict, present: dict[str, bool]) -> str:
    """The first (largest) variant that runs well, else the first that runs slowly, else the last."""
    for level in ("good", "slow"):
        for v in variants:
            if fit(v, d, _missing_mb(v["models"], present))["level"] == level:
                return v["id"]
    return variants[-1]["id"]


def _missing_mb(names: list[str], present: dict[str, bool]) -> int:
    return _size_mb([n for n in names if not present.get(n)])


def _presence() -> tuple[dict[str, bool], dict, bool]:
    ost = models.status()
    present = {f["name"]: f["present"] for f in ost["files"]}
    present.update({f"ollama:{o['name']}": o["present"] for o in ost["ollama"]})
    jobs = {f["name"]: f.get("job") for f in ost["files"]}
    jobs.update({f"ollama:{o['name']}": o.get("job") for o in ost["ollama"]})
    return present, jobs, ost["ollama_running"]


def current_variant(fid: str) -> str | None:
    if fid == "meaning":
        from .index import embed

        return embed.model()
    if fid == "llm":
        from .router import local

        return local.LLM
    return None


def _tools(names: list[str], system: str) -> list[dict]:
    rows = {r["name"]: r for r in setup.check_tools()}
    out = []
    for n in names:
        if n in _SYSTEM_TOOLS.get(system, set()):
            continue
        r = rows.get(n) or {"name": n, "ok": shutil.which(n) is not None, "install": "", "adds": ""}
        out.append({"name": n, "ok": r["ok"], "install": r["install"]})
    return out


def _state(on: bool, names: list[str], present: dict, jobs: dict, ollama_running: bool) -> tuple[str, dict | None]:
    running = [jobs[n] for n in names if (jobs.get(n) or {}).get("state") == "running"]
    if running:
        done = sum(j.get("done") or 0 for j in running)
        total = sum(j.get("total") or 0 for j in running)
        return "downloading", {"done": done, "total": total}
    failed = [jobs[n] for n in names if (jobs.get(n) or {}).get("state") == "error" and not present.get(n)]
    if failed:
        return "error", {"error": failed[0].get("error")}
    if not on:
        return "off", None
    missing = [n for n in names if not present.get(n)]
    if not missing:
        return "ready", None
    if any(n.startswith("ollama:") for n in missing) and not ollama_running:
        return "needs_ollama", None
    return "missing", None


def status(fresh: bool = False) -> dict:
    """Every feature with its fit, its state, its sizes and its variants."""
    d = device.info(fresh)
    present, jobs, ollama_running = _presence()
    system = d["os"]["system"]
    out = []
    for f in CATALOG:
        row = {"id": f["id"], "label": f["label"], "adds": f["adds"], "kind": f["kind"], "always": bool(f.get("always")),
               "needs": f.get("needs", [])}
        if f["kind"] == "builtin":
            row.update(on=True, state="ready", fit={"level": "good", "reason": ""}, recommended=True, mb=0)
        elif f["kind"] == "tool":
            tools = _tools(f["tools"], system)
            row.update(tools=tools, on=all(t["ok"] for t in tools) and bool(tools), recommended=False, mb=0,
                       state="ready" if tools and all(t["ok"] for t in tools) else "missing",
                       fit={"level": "good", "reason": ""})
            if not tools:
                continue
        else:
            on = True if f.get("always") else lite.enabled(f["switch"])
            if "variants" in f:
                cur = current_variant(f["id"])
                rec = _recommend(f["variants"], d, present)
                variants = []
                for v in f["variants"]:
                    vmb = _missing_mb(v["models"], present)
                    variants.append({"id": v["id"], "label": v["label"], "note": v["note"], "mb": _size_mb(v["models"]),
                                     "missing_mb": vmb, "present": vmb == 0, "fit": fit(v, d, vmb),
                                     "ram_use": v["ram_use"], "recommended": v["id"] == rec,
                                     "ollama": any(n.startswith("ollama:") for n in v["models"])})
                chosen = cur if any(v["id"] == cur for v in f["variants"]) else rec
                spec = next(v for v in f["variants"] if v["id"] == chosen)
                names = spec["models"]
                row.update(variants=variants, variant=chosen, recommended_variant=rec)
            else:
                spec, names = f, f["models"]
            mb = _missing_mb(names, present)
            ft = fit(spec, d, mb)
            for need in f.get("needs", []):
                if not (BY_ID[need].get("always") or lite.enabled(BY_ID[need].get("switch", need))):
                    ft = {"level": "no", "reason": f"Needs {BY_ID[need]['label']}."}
            state, job = _state(on, names, present, jobs, ollama_running)
            row.update(on=on, fit=ft, state=state, job=job, mb=mb, size_mb=_size_mb(names),
                       ram_use=spec.get("ram_use", 0), recommended=ft["level"] == "good",
                       tools=_tools(f.get("tools", []), system),
                       ollama=any(n.startswith("ollama:") for n in names))
        out.append(row)
    from . import ollama_setup

    return {"device": d, "summary": device.summary(d), "features": out, "ollama": ollama_setup.status(quick=True),
            "pending": db.get_setting("features_pending") or []}


def _names_for(fid: str, variant: str | None) -> list[str]:
    f = BY_ID[fid]
    if "variants" in f:
        v = next((v for v in f["variants"] if v["id"] == variant), f["variants"][0])
        return list(v["models"])
    return list(f.get("models", []))


def apply(on: dict[str, bool], variants: dict[str, str] | None = None) -> dict:
    """Turn features on or off, set the variants, and download what the features need.
    Returns {reembed, downloads, waiting}. "waiting" lists the Ollama models that wait for Ollama."""
    variants = variants or {}
    reembed = False
    for fid, want in on.items():
        f = BY_ID.get(fid)
        if not f:
            raise ValueError(f"Unknown feature {fid}")
        if f.get("switch"):
            lite.set_feature(f["switch"], bool(want))
    for fid, v in variants.items():
        f = BY_ID.get(fid)
        if not f or "variants" not in f or not any(x["id"] == v for x in f["variants"]):
            raise ValueError(f"Unknown variant {v} for {fid}")
        if fid == "meaning":
            from . import config

            old = db.get_setting("embed_model") or config.EMBED_MODEL
            # Lite mode follows the meaning model: a static model needs no Ollama (lite.py).
            db.set_setting("mode", "lite" if v.startswith("potion") else "full")
            if not v.startswith("potion"):
                db.set_setting("full_embed_model", v)
            db.set_setting("embed_model", v)
            lite.apply_priority()
            reembed = reembed or old != v
        elif fid == "llm":
            from .router import local

            db.set_setting("llm_model", v)
            local.use_model(v)
    want_names: list[str] = []
    for f in CATALOG:
        if f["kind"] != "model":
            continue
        enabled = f.get("always") or lite.enabled(f["switch"])
        if enabled:
            want_names += _names_for(f["id"], variants.get(f["id"]) or current_variant(f["id"]))
    present, _jobs, ollama_running = _presence()
    missing = [n for n in dict.fromkeys(want_names) if not present.get(n)]
    now = [n for n in missing if not n.startswith("ollama:") or ollama_running]
    waiting = [n for n in missing if n not in now]
    db.set_setting("features_pending", missing)
    if now:
        models.download(now)
    return {"reembed": reembed, "downloads": now, "waiting": waiting}


# A failed download starts again after this many seconds (a network error, a full disk).
RETRY_S = 120


def resume() -> list[str]:
    """Start the chosen downloads that do not run: after a quit, after an error, and once Ollama runs.
    A model that is here leaves the list."""
    pending = db.get_setting("features_pending") or []
    if not pending:
        return []
    present, jobs, ollama_running = _presence()
    left = [n for n in pending if not present.get(n)]
    if left != pending:
        db.set_setting("features_pending", left)
    start = []
    for n in left:
        job = jobs.get(n) or {}
        if job.get("state") == "running" or (n.startswith("ollama:") and not ollama_running):
            continue
        if job.get("state") == "error" and time.time() - (job.get("ended") or 0) < RETRY_S:
            continue
        start.append(n)
    if start:
        log.info("resume downloads: %s", ", ".join(start))
        models.download(start)
    return start


def waiting() -> list[dict]:
    """The chosen models with no download in this run yet: most wait for Ollama, the rest for loop()."""
    pending = db.get_setting("features_pending") or []
    return [{"key": n, "label": models.label(n), "ollama": n.startswith("ollama:")} for n in pending if not models.job_state(n)]


def loop(stop) -> None:
    """Keep the chosen downloads going in the background, while Raven runs."""
    while True:
        try:
            resume()
        except Exception:  # noqa: BLE001
            log.exception("resume downloads")
        if stop.wait(20):
            return


def remove(fid: str, variant: str | None = None) -> dict:
    """Free the disk space of a feature's model. The feature turns off. The meaning model in use stays."""
    f = BY_ID.get(fid)
    if not f or f["kind"] != "model":
        raise ValueError(f"Unknown feature {fid}")
    variant = variant or current_variant(fid)
    if f.get("always") and variant == current_variant(fid):
        raise PermissionError("Raven uses this model now. Pick another model first.")
    names = _names_for(fid, variant)
    # A model that another feature in use needs stays.
    for other in CATALOG:
        if other is f or other["kind"] != "model":
            continue
        if other.get("always") or lite.enabled(other["switch"]):
            keep = set(_names_for(other["id"], current_variant(other["id"])))
            names = [n for n in names if n not in keep]
    for n in names:
        models.remove(n)
    pending = db.get_setting("features_pending") or []
    db.set_setting("features_pending", [n for n in pending if n not in names])
    if f.get("switch") and ("variants" not in f or variant == current_variant(fid)):
        lite.set_feature(f["switch"], False)
    return {"removed": names}
