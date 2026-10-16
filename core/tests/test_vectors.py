"""Binary vectors with a float16 rescore give the same nearest chunks as the float search."""

import os
import tempfile

import numpy as np

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.search import vectors  # noqa: E402

db.init()


def test_binary_matches_float():
    rng = np.random.default_rng(3)
    dim = 64
    centers = rng.normal(size=(20, dim))
    X = (centers[rng.integers(0, 20, 600)] + 0.3 * rng.normal(size=(600, dim))).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    db.ensure_vec_table(dim, "test-model")
    with db.tx() as c:
        c.execute("DELETE FROM chunks")
        for i, v in enumerate(X, 1):
            c.execute("INSERT INTO chunks(id, file_id, text, embedded) VALUES (?, NULL, 'x', 1)", (i,))
            c.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)", (i, v.tobytes()))
    db.set_setting("binary_vectors", "off")
    vectors.forget_cache()
    q = X[5] + 0.05 * rng.normal(size=dim).astype(np.float32)
    exact = [i for i, _ in vectors.knn(q, 10)]
    db.set_setting("binary_vectors", "on")
    assert vectors.fill() == 600
    vectors.forget_cache()
    assert vectors.ready()
    approx = [i for i, _ in vectors.knn(q, 10)]
    assert len(set(exact) & set(approx)) >= 9 and approx[0] == exact[0]
    db.set_setting("binary_vectors", "auto")
    vectors.drop()
    vectors.forget_cache()
