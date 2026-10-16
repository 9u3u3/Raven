"""Every request body is a JSON body. A model class that is defined after its route becomes a query
parameter (app.py uses postponed annotations), and the route then answers 422 for every call."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from pydantic import BaseModel  # noqa: E402

from raven_core.app import app  # noqa: E402


def test_no_body_model_is_a_query_parameter():
    bad = []
    for r in app.routes:
        dep = getattr(r, "dependant", None)
        if dep is None:
            continue
        for q in dep.query_params:
            ann = q.field_info.annotation
            if q.name == "b" or (isinstance(ann, type) and issubclass(ann, BaseModel)):
                bad.append(f"{r.path}: {q.name}")
    assert not bad, bad
