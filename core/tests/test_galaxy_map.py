"""The map by meaning and the photo hash work with NumPy only (PLAN3 P1-4)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import numpy as np  # noqa: E402

from raven_core import galaxy  # noqa: E402
from raven_core.storage import dupes  # noqa: E402


def _three_topics(per: int = 30, dim: int = 64) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    centres = np.eye(dim, dtype=np.float32)[:3] * 3
    X = np.concatenate([c + rng.normal(0, 0.35, (per, dim)) for c in centres]).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    return X, np.repeat(np.arange(3), per)


def test_groups_follow_the_topics():
    X, truth = _three_topics()
    labels, _ = galaxy._clusters(X)
    # Each topic falls in its own groups: no group mixes two topics.
    for g in set(labels.tolist()) - {-1}:
        assert len(set(truth[labels == g].tolist())) == 1
    assert (labels >= 0).mean() > 0.8


def test_layout_keeps_topics_apart_and_is_stable():
    X, truth = _three_topics()
    labels, C = galaxy._clusters(X)
    Y = galaxy._layout(X, labels, C)
    assert Y.shape == (len(X), 2) and np.isfinite(Y).all()
    means = np.array([Y[truth == t].mean(axis=0) for t in range(3)])
    spread = max(np.linalg.norm(Y[truth == t] - means[t], axis=1).mean() for t in range(3))
    gap = min(np.linalg.norm(means[a] - means[b]) for a in range(3) for b in range(a + 1, 3))
    assert gap > 2 * spread
    labels2, C2 = galaxy._clusters(X)
    assert np.array_equal(labels, labels2)
    assert np.allclose(Y, galaxy._layout(X, labels2, C2))


def test_tiny_maps():
    for n in (0, 1, 4, 6):
        X = np.eye(8, dtype=np.float32)[: max(n, 0)] if n else np.zeros((0, 8), dtype=np.float32)
        labels, C = galaxy._clusters(X)
        assert len(labels) == n
        assert galaxy._layout(X, labels, C).shape == (n, 2)


def test_photo_hash_without_scipy(tmp_path):
    from PIL import Image

    rng = np.random.default_rng(5)
    base = (rng.random((256, 256, 3)) * 255).astype(np.uint8)
    Image.fromarray(base).save(tmp_path / "a.png")
    Image.fromarray(base).resize((128, 128)).save(tmp_path / "b.png")  # the same photo, smaller
    Image.fromarray(255 - base).save(tmp_path / "c.png")  # a different photo
    ha, hb, hc = (dupes.phash(str(tmp_path / f"{n}.png"))[0] for n in "abc")
    assert bin(ha ^ hb).count("1") <= 6
    assert bin(ha ^ hc).count("1") > 20



def test_a_late_build_thread_still_runs(monkeypatch):
    """build_async() sets the flag first. The thread must run although it sees the flag."""
    from raven_core import db

    db.init()
    runs = []
    monkeypatch.setattr(galaxy, "_vectors", lambda: (runs.append(1), ([], np.zeros((0, 8), dtype=np.float32)))[1])
    galaxy._state["building"] = True
    galaxy.build(claimed=True)
    assert runs and galaxy._state["building"] is False
    galaxy._state["building"] = True
    galaxy.build()  # not claimed: another build runs, so this call does nothing
    assert len(runs) == 1
    galaxy._state["building"] = False
