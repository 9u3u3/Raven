"""The device check and the feature picker (PLAN4 R2): fit rules, suggested sizes, apply and remove."""

import os
import tempfile
import time

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db, device, features, lite, models  # noqa: E402
from raven_core.router import local  # noqa: E402

db.init()
GB = 1024**3


def dev(ram=16, gpu=None, vendor="nvidia", threads=8, free=200, bench=None, shared=False):
    gpus = [{"name": "GPU", "vendor": vendor, "vram": int(gpu * GB), "shared": shared}] if gpu else []
    return {"os": {"system": "linux", "name": "Test", "arch": "x86_64"}, "cpu": {"model": "CPU", "cores": threads // 2, "threads": threads},
            "ram": {"total": int(ram * GB * 0.96), "free": int(ram * GB / 2)}, "gpus": gpus,
            "gpu_memory": int(gpu * GB) if gpu else 0, "disk": {"path": "/", "free": int(free * GB), "total": int(500 * GB)},
            "battery": None, "bench": bench}


def variants(fid):
    return features.BY_ID[fid]["variants"]


def rec(fid, d):
    return features._recommend(variants(fid), d, {})


def test_the_device_report_has_every_part():
    d = device.info(fresh=True)
    for k in ("os", "cpu", "ram", "gpus", "gpu_memory", "disk", "battery"):
        assert k in d
    assert d["cpu"]["threads"] >= 1 and d["ram"]["total"] > 0
    assert d["os"]["system"] in ("linux", "darwin", "windows")
    assert "GB RAM" in device.summary(d)


@pytest.mark.parametrize("d, llm, meaning", [
    (dev(ram=8, threads=4), "qwen3:1.7b", "embeddinggemma"),
    (dev(ram=16, gpu=6, threads=16), "qwen3:4b", "embeddinggemma"),
    (dev(ram=32, gpu=12, threads=16), "qwen3:8b", "embeddinggemma"),
    (dev(ram=16, gpu=12, vendor="apple", shared=True, threads=8), "qwen3:8b", "embeddinggemma"),
    (dev(ram=4, threads=4), "qwen3:1.7b", "potion-base-8m"),
    (dev(ram=6, threads=4), "qwen3:1.7b", "potion-retrieval-32m"),
])
def test_the_suggested_sizes_follow_the_device(d, llm, meaning):
    assert rec("llm", d) == llm
    assert rec("meaning", d) == meaning


def test_fit_levels_and_reasons():
    big = features.BY_ID["llm"]["variants"][0]  # qwen3:8b
    assert features.fit(big, dev(ram=32, gpu=12))["level"] == "good"
    assert "GPU" in features.fit(big, dev(ram=32, gpu=12))["reason"]
    slow = features.fit(big, dev(ram=16, threads=8))
    assert slow["level"] == "slow" and "CPU" in slow["reason"]
    no = features.fit(big, dev(ram=8))
    assert no["level"] == "no" and "16 GB" in no["reason"]
    # A slow CPU (the speed test) makes a CPU model slow, also with enough RAM.
    mid = features.BY_ID["llm"]["variants"][1]
    assert features.fit(mid, dev(ram=16, threads=8))["level"] == "good"
    assert features.fit(mid, dev(ram=16, threads=8, bench=20))["level"] == "slow"


def test_not_enough_disk_is_not_recommended():
    mid = features.BY_ID["llm"]["variants"][1]
    f = features.fit(mid, dev(ram=16, gpu=6, free=2), download_mb=2500)
    assert f["level"] == "no" and "disk" in f["reason"]


def test_a_16_gb_laptop_counts_as_16_gb():
    assert features._at_least(int(15.3 * GB), 16)
    assert not features._at_least(int(13 * GB), 16)


@pytest.fixture
def fake_models(monkeypatch):
    present = {"reranker": True}
    calls = []
    running = {"on": False}
    jobs: dict[str, dict] = {}

    def status():
        return {"files": [{"name": k, "present": present.get(k, False), "job": jobs.get(k)} for k in models.CATALOG],
                "ollama": [{"name": k, "present": present.get(f"ollama:{k}", False), "job": jobs.get(f"ollama:{k}")}
                           for k in models.OLLAMA],
                "ollama_running": running["on"]}

    def download(names):
        # The fake download ends at once: the model is here.
        calls.append(list(names))
        present.update({n: True for n in names})

    monkeypatch.setattr(models, "status", status)
    monkeypatch.setattr(models, "download", download)
    monkeypatch.setattr(models, "jobs", lambda: [{"key": k, "label": k, **j} for k, j in jobs.items()])
    monkeypatch.setattr(models, "remove", lambda name: present.pop(name, None))
    monkeypatch.setattr(device, "info", lambda fresh=False: dev(ram=16, gpu=6, threads=16))
    yield present, calls, running, jobs
    db.set_setting("features", {})
    db.set_setting("features_pending", [])
    db.set_setting("embed_model", None)
    db.set_setting("llm_model", None)
    db.set_setting("mode", None)
    local.use_model("qwen3:4b")


def test_status_lists_every_feature_with_a_fit(fake_models):
    st = features.status()
    ids = [f["id"] for f in st["features"]]
    assert {"names", "meaning", "llm", "reranker", "image", "speech", "privacy"} <= set(ids)
    for f in st["features"]:
        assert f["fit"]["level"] in ("good", "slow", "no")
    rer = next(f for f in st["features"] if f["id"] == "reranker")
    assert rer["state"] == "ready" and rer["mb"] == 0
    llm = next(f for f in st["features"] if f["id"] == "llm")
    assert llm["state"] == "needs_ollama" and llm["recommended_variant"] == "qwen3:4b"


def test_apply_sets_switches_and_sizes_and_waits_for_ollama(fake_models):
    present, calls, running, _jobs = fake_models
    out = features.apply({"image": True, "speech": False, "llm": True}, {"llm": "qwen3:1.7b", "meaning": "embeddinggemma"})
    assert lite.enabled("image") and not lite.enabled("speech")
    assert local.LLM == "qwen3:1.7b" and db.get_setting("llm_model") == "qwen3:1.7b"
    assert db.get_setting("mode") == "full"
    assert "siglip2" in out["downloads"] and "whisper-base" not in out["downloads"]
    assert "ollama:qwen3:1.7b" in out["waiting"] and "ollama:embeddinggemma" in out["waiting"]
    assert calls and all(not n.startswith("ollama:") for n in calls[-1])
    # Ollama runs: the waiting downloads start.
    running["on"] = True
    started = features.resume()
    assert "ollama:qwen3:1.7b" in started and calls[-1] == started
    assert features.resume() == []
    assert db.get_setting("features_pending") == []


def test_resume_starts_a_stopped_download_again(fake_models):
    present, calls, running, jobs = fake_models
    running["on"] = True
    db.set_setting("features_pending", ["siglip2", "whisper-base", "ollama:qwen3:4b"])
    # siglip2 runs, whisper-base failed just now, and Raven quit during the Ollama pull (no job).
    jobs["siglip2"] = {"state": "running", "done": 1, "total": 9, "error": None}
    jobs["whisper-base"] = {"state": "error", "done": 0, "total": 0, "error": "network", "ended": time.time()}
    assert features.resume() == ["ollama:qwen3:4b"]
    # After RETRY_S, the failed download starts again.
    jobs["whisper-base"]["ended"] = time.time() - features.RETRY_S - 1
    assert features.resume() == ["whisper-base"]
    # A model that is here leaves the list.
    present["siglip2"] = True
    features.resume()
    assert "siglip2" not in db.get_setting("features_pending")


def test_a_static_meaning_model_means_lite_mode(fake_models):
    out = features.apply({}, {"meaning": "potion-base-8m"})
    assert db.get_setting("mode") == "lite" and db.get_setting("embed_model") == "potion-base-8m"
    assert out["reembed"] is True
    assert "potion-base-8m" in out["downloads"]


def test_unknown_names_are_refused(fake_models):
    with pytest.raises(ValueError):
        features.apply({"nope": True})
    with pytest.raises(ValueError):
        features.apply({}, {"llm": "qwen9:900b"})


def test_remove_frees_the_model_and_turns_the_feature_off(fake_models):
    present, calls, running, _jobs = fake_models
    present["siglip2"] = True
    features.apply({"image": True})
    out = features.remove("image")
    assert out["removed"] == ["siglip2"] and not lite.enabled("image")
    # The meaning model in use stays.
    features.apply({}, {"meaning": "potion-base-8m"})
    with pytest.raises(PermissionError):
        features.remove("meaning")
