"""Connect to server: address forms, and answers to the gio prompts through a pseudo-terminal.

A fake `gio` script asks the same questions as the real one, in the order of each protocol.
"""

import os
import stat
import tempfile
import textwrap

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.fs import network  # noqa: E402

db.init()

FAKE_GIO = textwrap.dedent("""\
    #!/bin/bash
    # Fake gio: mount <url>. FAKE_MODE picks the prompts. Success makes the gvfs folder.
    [ "$1" = "mount" ] || exit 2
    url="${@: -1}"
    case "$FAKE_MODE" in
      smb)
        echo "Password required for share docs on pc"
        printf "User [harsh]: "; read u
        printf "Domain [WORKGROUP]: "; read d
        printf "Password: "; read -s p; echo
        while [ "$p" != "secret" ]; do
          echo "Password required for share docs on pc"
          printf "Password: "; read -s p; echo
        done
        [ "$u" = "venu" ] || { echo "gio: $url: Permission denied"; exit 2; }
        mkdir -p "$FAKE_GVFS/smb-share:server=pc,share=docs,user=$u" ;;
      sftp)
        echo "The identity of the remote computer (pc) is unknown."
        echo "The identity sent by the remote computer is SHA256:AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg. If you want to be absolutely sure it is safe to continue, contact the system administrator."
        echo "[1] Log In Anyway"
        echo "[2] Cancel Login"
        printf "Choice: "; read c
        [ "$c" = "1" ] || { echo "gio: $url: Login dialog cancelled"; exit 2; }
        echo "Enter password for ssh as venu on pc"
        printf "Password: "; read -s p; echo
        [ "$p" = "secret" ] || { echo "gio: $url: Permission denied"; exit 2; }
        mkdir -p "$FAKE_GVFS/sftp:host=pc,user=venu/home/venu" ;;
      refused)
        echo "gio: $url: Connection refused by server"; exit 2 ;;
    esac
    exit 0
""")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gio = bin_dir / "gio"
    gio.write_text(FAKE_GIO)
    gio.chmod(gio.stat().st_mode | stat.S_IEXEC)
    gvfs = tmp_path / "gvfs"
    gvfs.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GVFS", str(gvfs))
    monkeypatch.setattr(network, "gvfs_dir", lambda: gvfs)
    monkeypatch.setattr(network, "backend_ok", lambda s: True)

    def mode(m):
        monkeypatch.setenv("FAKE_MODE", m)

    return mode, gvfs


@pytest.mark.parametrize("given,want", [
    ("\\\\192.168.1.20\\Users\\venu", "smb://192.168.1.20/Users/venu"),
    ("//pc/docs", "smb://pc/docs"),
    ("ssh://venu@pc/home", "sftp://venu@pc/home"),
    ("webdavs://cloud.example/remote.php", "davs://cloud.example/remote.php"),
    ("SMB://PC/Docs", "smb://PC/Docs"),
])
def test_normalize(given, want):
    assert network.normalize(given) == want


@pytest.mark.parametrize("bad", ["http://x", "pc/docs", "smb://pc/docs;rm -rf ~", "smb://pc/$(id)"])
def test_bad_address(bad):
    with pytest.raises(network.NetError) as e:
        network.normalize(bad)
    assert e.value.code == "bad_address"


def test_smb_prompts_in_order(fake):
    mode, gvfs = fake
    mode("smb")
    path = network.connect("smb://pc/docs", user="venu", password="secret", domain="")
    assert path == str(gvfs / "smb-share:server=pc,share=docs,user=venu")
    # The password is never kept.
    r = network.recent()[0]
    assert r["url"] == "smb://pc/docs" and r["user"] == "venu"
    assert "secret" not in str(network.recent())


def test_wrong_password_stops(fake):
    mode, _ = fake
    mode("smb")
    with pytest.raises(network.NetError) as e:
        network.connect("smb://pc/docs", user="venu", password="nope")
    assert e.value.code == "auth_failed"


def test_needs_password(fake):
    mode, _ = fake
    mode("smb")
    with pytest.raises(network.NetError) as e:
        network.connect("smb://pc/docs", user="venu")
    assert e.value.code == "needs_password"


def test_unknown_host_asks_first(fake):
    mode, gvfs = fake
    mode("sftp")
    with pytest.raises(network.NetError) as e:
        network.connect("sftp://venu@pc/home/venu", password="secret")
    assert e.value.code == "host_key"
    assert e.value.extra["fingerprint"].startswith("SHA256:AbCd")
    # After the user trusts the key, Raven answers "Log In Anyway" and opens the folder in the address.
    path = network.connect("sftp://venu@pc/home/venu", password="secret", trust=True)
    assert path == str(gvfs / "sftp:host=pc,user=venu/home/venu")


def test_refused_is_unreachable(fake):
    mode, _ = fake
    mode("refused")
    with pytest.raises(network.NetError) as e:
        network.connect("sftp://pc/")
    assert e.value.code == "unreachable"


def test_label():
    assert network.label("smb-share:server=pc,share=docs,user=venu")["label"] == "docs on pc"
    lab = network.label("sftp:host=pc,port=2222,user=venu")
    assert lab["label"] == "venu@pc:2222" and lab["protocol"] == "SFTP"


def test_missing_backend_names_package(monkeypatch):
    monkeypatch.setattr(network, "backend_ok", lambda s: False)
    with pytest.raises(network.NetError) as e:
        network.connect("smb://pc/docs")
    assert e.value.code == "backend_missing"
    assert e.value.extra["install"]
