"""Encrypted index: encrypt, swap at start, unlock, wrong passphrase, decrypt."""

import os
import subprocess
import sys
import textwrap


def run(data: str, code: str) -> str:
    env = {**os.environ, "RAVEN_DATA_DIR": data, "XDG_DATA_HOME": os.path.join(data, "xdg"), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, env=env,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_encrypt_unlock_decrypt(tmp_path):
    d = str(tmp_path)
    # Each step is a new process, as each sidecar start is.
    run(d, """
        from raven_core import db, secure
        db.init()
        db.set_setting("probe", "rent agreement for the flat")
        print(secure.encrypt("correct horse", remember=False))
    """)
    out = run(d, """
        from raven_core import secure
        print(secure.startup())
        print(secure.unlock("wrong pass"), secure.unlock("correct horse"))
        from raven_core import db
        print(db.get_setting("probe"))
    """)
    assert out.splitlines() == ["locked", "False True", "rent agreement for the flat"]
    raw = open(os.path.join(d, "raven.db"), "rb").read()
    assert b"rent agreement" not in raw and not raw.startswith(b"SQLite format 3")
    run(d, """
        from raven_core import secure
        secure.startup(); secure.unlock("correct horse")
        print(secure.decrypt("correct horse"))
    """)
    out = run(d, """
        from raven_core import db, secure
        print(secure.startup())
        print(db.get_setting("probe"))
    """)
    assert out.splitlines() == ["plain", "rent agreement for the flat"]
    assert open(os.path.join(d, "raven.db"), "rb").read().startswith(b"SQLite format 3")
