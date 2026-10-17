"""Network storage (section 9.12). Linux: `gio mount` puts SMB, SFTP, FTP, WebDAV, NFS and AFP shares under
/run/user/<uid>/gvfs, and Raven browses them like local folders. Network folders get metadata only: Raven
does not read their content unless the user indexes them.

Which protocol for which computer:
- Windows: SMB (smb://host/share). Windows 10 and 11 speak SMB 2 and 3, which gvfs uses.
- Linux: SFTP (sftp://user@host/path) when sshd runs, or SMB when Samba runs. NFS for an NFS export.
- macOS: SMB (System Settings > Sharing > File Sharing) or SFTP (Remote Login).

`gio mount` asks its questions on a terminal. Raven gives it a pseudo-terminal and answers each prompt as it
comes: the user, the domain, the password, or the choice for an unknown host key. The order differs between
protocols, so fixed answers on stdin are not safe.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .. import db
from ..platform import inside

SCHEMES = {"smb", "sftp", "ftp", "ftps", "dav", "davs", "nfs", "afp"}
ALIASES = {"ssh": "sftp", "webdav": "dav", "webdavs": "davs", "cifs": "smb"}
URL = re.compile(r"^([a-z]+)://([^\s'\"`;|&$]+)$", re.I)
# The gvfs mount file for each scheme. A missing file means the backend package is not installed.
MOUNT_FILES = {
    "smb": ["smb.mount", "smb-share.mount"], "sftp": ["sftp.mount"], "ftp": ["ftp.mount"], "ftps": ["ftps.mount", "ftp.mount"],
    "dav": ["dav.mount"], "davs": ["dav.mount"], "nfs": ["nfs.mount"], "afp": ["afp.mount"],
}
MOUNTS_DIR = Path("/usr/share/gvfs/mounts")
TIMEOUT = 60
RECENT_MAX = 8

_PROMPT_USER = re.compile(r"User(?: \[[^\]]*\])?: $")
_PROMPT_DOMAIN = re.compile(r"Domain(?: \[[^\]]*\])?: $")
_PROMPT_PASSWORD = re.compile(r"Password: $")
_PROMPT_CHOICE = re.compile(r"Choice: $")
_CHOICE_LINE = re.compile(r"^\[(\d+)\]\s*(.+)$", re.M)


class NetError(Exception):
    """`code` tells the UI what to show: needs_password, host_key, pick_share, backend_missing, auth_failed, unreachable, bad_address, error."""

    def __init__(self, message: str, code: str = "error", **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra

    def as_dict(self) -> dict:
        return {"message": str(self), "code": self.code, **self.extra}


def gvfs_dir() -> Path:
    if not hasattr(os, "getuid"):
        return Path("/nonexistent-gvfs")  # Windows: UNC paths need no mount (section 9.12)
    return Path(f"/run/user/{os.getuid()}/gvfs")


# ---------------------------------------------------------------- addresses


def normalize(url: str) -> str:
    """Accept smb://, sftp:// and the others, plus Windows forms: \\\\host\\share and //host/share."""
    u = url.strip()
    if u.startswith("\\\\"):
        u = "smb://" + u.lstrip("\\").replace("\\", "/")
    elif u.startswith("//"):
        u = "smb:" + u
    m = URL.match(u)
    if not m:
        raise NetError("Use an address such as smb://host/share, sftp://user@host/folder or \\\\host\\share", "bad_address")
    scheme = m.group(1).lower()
    scheme = ALIASES.get(scheme, scheme)
    if scheme not in SCHEMES:
        raise NetError(f"Raven cannot connect with {m.group(1)}://. Use smb, sftp, ftp, dav, nfs or afp.", "bad_address")
    return f"{scheme}://{m.group(2)}"


def parts(url: str) -> dict:
    """scheme, user, host, port and path of a normalized address."""
    m = re.match(r"^(\w+)://(?:([^@/]*)@)?(\[[^\]]+\]|[^:/]+)(?::(\d+))?(/.*)?$", url)
    if not m:
        raise NetError("The address has no host name", "bad_address")
    return {"scheme": m.group(1), "user": m.group(2) or "", "host": m.group(3), "port": m.group(4) or "",
            "path": m.group(5) or ""}


def _distro() -> str:
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return ""
    ids = " ".join(re.findall(r"^(?:ID|ID_LIKE)=\"?([^\"\n]+)", text, re.M)).lower()
    for name in ("arch", "debian", "ubuntu", "fedora", "suse"):
        if name in ids:
            return name
    return ""


def install_hint(scheme: str) -> str:
    d = _distro()
    if d == "arch":
        pkg = {"smb": "gvfs-smb", "nfs": "gvfs-nfs", "dav": "gvfs-dnssd", "davs": "gvfs-dnssd", "afp": "gvfs-afp"}.get(scheme, "gvfs")
        return f"sudo pacman -S {pkg}"
    if d in ("debian", "ubuntu"):
        return "sudo apt install gvfs-backends gvfs-fuse"
    if d == "fedora":
        pkg = {"smb": "gvfs-smb", "nfs": "gvfs-nfs", "afp": "gvfs-afp"}.get(scheme, "gvfs")
        return f"sudo dnf install {pkg} gvfs-fuse"
    if d == "suse":
        return "sudo zypper install gvfs-backends gvfs-fuse"
    return f"install the gvfs backend for {scheme}"


def backend_ok(scheme: str) -> bool:
    if not MOUNTS_DIR.is_dir():
        # Another layout: let gio decide and report.
        return True
    return any((MOUNTS_DIR / f).exists() for f in MOUNT_FILES.get(scheme, []))


def backends() -> dict:
    """What this computer can connect to, for the dialog."""
    return {
        "gio": bool(shutil.which("gio")),
        "fuse": gvfs_dir().is_dir(),
        "smbclient": bool(shutil.which("smbclient")),
        "schemes": {s: {"ok": backend_ok(s), "install": None if backend_ok(s) else install_hint(s)} for s in sorted(SCHEMES)},
    }


# ---------------------------------------------------------------- gio with a terminal


def _gio_mount(url: str, user: str, password: str, domain: str, trust: bool, anonymous: bool) -> None:
    """Run `gio mount url` and answer its prompts. Raise NetError with a code the UI can act on."""
    master, slave = os.openpty()
    args = ["gio", "mount"] + (["-a"] if anonymous else []) + [url]
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    try:
        proc = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env=env, close_fds=True)
    finally:
        os.close(slave)
    out = ""
    seen = 0
    passwords_sent = 0
    deadline = time.monotonic() + TIMEOUT

    def send(text: str) -> None:
        os.write(master, (text + "\n").encode())

    def stop(err: NetError):
        proc.kill()
        raise err

    try:
        while True:
            if time.monotonic() > deadline:
                stop(NetError(f"The server did not answer in {TIMEOUT} s", "unreachable"))
            r, _, _ = select.select([master], [], [], 0.25)
            if r:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if data:
                    out += data.decode(errors="replace").replace("\r", "")
                    tail = out[seen:]
                    if _PROMPT_USER.search(tail):
                        send(user)
                        seen = len(out)
                    elif _PROMPT_DOMAIN.search(tail):
                        send(domain)
                        seen = len(out)
                    elif _PROMPT_PASSWORD.search(tail):
                        if not password:
                            stop(NetError("This server needs a password", "needs_password"))
                        if passwords_sent:
                            stop(NetError("The user name or the password is wrong", "auth_failed"))
                        send(password)
                        passwords_sent += 1
                        seen = len(out)
                    elif _PROMPT_CHOICE.search(tail):
                        question = tail[: tail.rfind("Choice")].strip()
                        choices = _CHOICE_LINE.findall(question)
                        text = _CHOICE_LINE.sub("", question).strip()
                        yes = next((n for n, label in choices if re.search(r"log in anyway|connect anyway|yes|continue", label, re.I)), None)
                        if not trust or yes is None:
                            stop(NetError(text or "The server asks a question", "host_key", question=text,
                                          fingerprint=_fingerprint(text)))
                        send(yes)
                        seen = len(out)
                    continue
            if proc.poll() is not None:
                # Read what is left after the exit.
                try:
                    while select.select([master], [], [], 0.05)[0]:
                        data = os.read(master, 4096)
                        if not data:
                            break
                        out += data.decode(errors="replace").replace("\r", "")
                except OSError:
                    pass
                break
    finally:
        os.close(master)
    if proc.returncode != 0:
        msg = [ln for ln in out.strip().splitlines() if ln.strip()]
        last = msg[-1] if msg else f"gio mount failed ({proc.returncode})"
        last = re.sub(r"^gio: [^:]+(?::\S+)*: ", "", last).strip()
        if "already mounted" in last.lower():
            return
        raise _classify(last)


def _fingerprint(text: str) -> str:
    m = re.search(r"((?:SHA256:)?[A-Za-z0-9+/=]{30,}|(?:[0-9a-f]{2}:){15}[0-9a-f]{2})", text)
    return m.group(1) if m else ""


def _classify(msg: str) -> NetError:
    low = msg.lower()
    if "not supported" in low or "not mountable" in low:
        return NetError(msg, "backend_missing")
    if "permission denied" in low or "access denied" in low or "logon failure" in low or "authentication" in low:
        return NetError(f"{msg}. Check the user name and the password.", "auth_failed")
    if "refused" in low or "no route" in low or "timed out" in low or "unreachable" in low or "resolve" in low \
            or "not found" in low or "connection" in low:
        return NetError(f"{msg}. Check that the other computer is on, on the same network, and shares this folder.", "unreachable")
    return NetError(msg)


# ---------------------------------------------------------------- shares


def list_shares(host: str, user: str = "", password: str = "", domain: str = "") -> list[str]:
    """The SMB shares of a host (Windows or Samba), without the hidden admin shares such as C$ and IPC$."""
    if not shutil.which("smbclient"):
        raise NetError("Type the share name: smb://host/share. Install smbclient to see the list of shares.", "pick_share", shares=[])
    host = host.strip("[]")
    with tempfile.NamedTemporaryFile("w", prefix="raven-smb-", delete=False) as f:
        os.chmod(f.name, 0o600)
        if user:
            f.write(f"username = {user}\npassword = {password}\n")
            if domain:
                f.write(f"domain = {domain}\n")
        auth = f.name
    try:
        args = ["smbclient", "-g", "-L", f"//{host}", "-t", "15"]
        args += ["-A", auth] if user else ["-N"]
        r = subprocess.run(args, capture_output=True, timeout=30, env={**os.environ, "LANG": "C"})
    except subprocess.TimeoutExpired as e:
        raise NetError(f"{host} did not answer in 30 s", "unreachable") from e
    finally:
        os.unlink(auth)
    text = (r.stdout or b"").decode(errors="replace")
    shares = [ln.split("|")[1] for ln in text.splitlines() if ln.startswith("Disk|") and not ln.split("|")[1].endswith("$")]
    if r.returncode != 0 and not shares:
        err = ((r.stderr or b"").decode(errors="replace") + text).strip().splitlines()
        last = err[-1] if err else "smbclient failed"
        if "NT_STATUS_LOGON_FAILURE" in last or "NT_STATUS_ACCESS_DENIED" in last:
            if not password:
                raise NetError("This computer needs a user name and a password to show its shares", "needs_password")
            raise NetError("The user name or the password is wrong", "auth_failed")
        raise _classify(last.replace("do_connect: ", ""))
    return sorted(shares, key=str.lower)


# ---------------------------------------------------------------- connect


def _connect_windows(p: dict, user: str, password: str, domain: str) -> str:
    """Windows (section 9.12): an SMB share is a UNC path. `net use` gives it the user and password once."""
    if p["scheme"] != "smb":
        raise NetError("On Windows, Raven opens SMB shares (\\\\host\\share). For SFTP or WebDAV, use the rclone tab.", "bad_address")
    share = p["path"].strip("/").split("/")[0]
    if not share:
        raise NetError("Add the share name: \\\\host\\share", "pick_share", shares=[], host=p["host"])
    unc = f"\\\\{p['host']}\\{share}"
    if user:
        who = f"{domain}\\{user}" if domain else user
        r = subprocess.run(["net", "use", unc, password, f"/user:{who}", "/persistent:no"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and "1219" not in (r.stderr or ""):  # 1219: already connected with other credentials
            raise NetError((r.stderr or r.stdout).strip()[:200] or "net use failed", "auth_failed")
    if not os.path.isdir(unc):
        raise NetError(f"Windows cannot open {unc}", "unreachable")
    rest = "/".join(p["path"].strip("/").split("/")[1:])
    return unc + ("\\" + rest.replace("/", "\\") if rest else "")


def _connect_mac(url: str) -> str:
    """macOS: Finder mounts the share under /Volumes and asks for the password itself."""
    before = set(os.listdir("/Volumes"))
    r = subprocess.run(["osascript", "-e", f'mount volume "{url}"'], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise NetError((r.stderr or "").strip()[:200] or "macOS could not mount the share", "unreachable")
    new = sorted(set(os.listdir("/Volumes")) - before)
    return f"/Volumes/{new[0]}" if new else "/Volumes"


def connect(url: str, user: str = "", password: str = "", domain: str = "", trust: bool = False) -> str:
    """Mount `url` with gio and return the local path of the mount."""
    url = normalize(url)
    p = parts(url)
    scheme = p["scheme"]
    from .. import platform as plat

    if plat.IS_WIN:
        return _connect_windows(p, user, password, domain)
    if plat.IS_MAC:
        return _connect_mac(url)
    if not shutil.which("gio"):
        raise NetError("gio is not installed, so Raven cannot connect to servers", "backend_missing", install=install_hint(scheme))
    if not backend_ok(scheme):
        raise NetError(f"This computer has no {scheme.upper()} support for gio yet. Run: {install_hint(scheme)}",
                       "backend_missing", install=install_hint(scheme))
    if not gvfs_dir().is_dir():
        raise NetError("gvfs-fuse is not running, so Raven cannot open the share as a folder. "
                       f"Run: {install_hint(scheme)}, then log out and in.", "backend_missing", install=install_hint(scheme))
    if scheme == "smb" and p["path"].strip("/") == "":
        # A host without a share: list the shares, so the user can pick one.
        shares = list_shares(p["host"], user or p["user"], password, domain)
        raise NetError(f"Pick a shared folder on {p['host']}", "pick_share", shares=shares, host=p["host"])
    # A user in the address wins. A user from the form goes into the address for SFTP and FTP.
    if user and not p["user"] and scheme in ("sftp", "ftp", "ftps", "dav", "davs"):
        url = f"{scheme}://{user}@{url.split('://', 1)[1]}"
    before = set(os.listdir(gvfs_dir()))
    anonymous = scheme in ("ftp", "ftps") and not (user or p["user"])
    _gio_mount(url, user or p["user"], password, domain, trust, anonymous)
    # gvfsd-fuse shows a new mount a moment after `gio mount` returns: look again for up to 3 s.
    path = None
    for _ in range(30):
        after = set(os.listdir(gvfs_dir()))
        path = _find_mount(url, sorted(after - before), sorted(after), guess=False)
        if path:
            break
        time.sleep(0.1)
    if not path:
        after = set(os.listdir(gvfs_dir()))
        path = _find_mount(url, sorted(after - before), sorted(after))
    if not path:
        raise NetError("The share is mounted, but Raven did not find its folder")
    _remember(url, user or p["user"], domain)
    return path


def _find_mount(url: str, new: list[str], all_: list[str], guess: bool = True) -> str | None:
    p = parts(url)
    host = p["host"].strip("[]").lower()
    share = p["path"].strip("/").split("/")[0].lower() if p["scheme"] == "smb" else ""
    port = p.get("port")
    for name in new + all_:
        low = name.lower()
        if port and "port=" in low and f"port={port}" not in low:
            continue  # the same host on another port is another server
        if host and host in low and (not share or f"share={share}" in low):
            sub = p["path"].strip("/")
            base = gvfs_dir() / name
            # sftp://host/folder mounts the whole host. Open the folder that the address names.
            if p["scheme"] != "smb" and sub and (base / sub).is_dir():
                return str(base / sub)
            if p["scheme"] == "smb" and "/" in sub and (base / sub.split("/", 1)[1]).is_dir():
                return str(base / sub.split("/", 1)[1])
            return str(base)
    return str(gvfs_dir() / new[0]) if new and guess else None


def disconnect(path: str) -> None:
    p = os.path.abspath(path)
    g = str(gvfs_dir())
    if not p.startswith(g + "/"):
        raise NetError("Not a network folder")
    # Unmount the share, not a folder inside it.
    top = os.path.join(g, Path(p).relative_to(g).parts[0])
    r = subprocess.run(["gio", "mount", "-u", top], capture_output=True, timeout=30)
    if r.returncode != 0:
        raise NetError((r.stderr or b"").decode(errors="replace").strip() or "gio could not disconnect")


# ---------------------------------------------------------------- labels and recent servers


def label(mount_name: str) -> dict:
    """A gvfs folder name such as smb-share:server=pc,share=docs -> "docs on pc", with the protocol and the address."""
    kind, _, rest = mount_name.partition(":")
    kv = dict(x.split("=", 1) for x in rest.split(",") if "=" in x)
    host = kv.get("server") or kv.get("host") or ""
    user = kv.get("user", "")
    scheme = {"smb-share": "smb", "sftp": "sftp", "ftp": "ftp", "ftps": "ftps", "dav": "dav", "davs": "davs",
              "nfs": "nfs", "afp-volume": "afp"}.get(kind, kind)
    if kind == "smb-share":
        text = f"{kv.get('share', '')} on {host}"
        url = f"smb://{host}/{kv.get('share', '')}"
    else:
        port = f":{kv['port']}" if kv.get("port") else ""
        text = f"{user + '@' if user else ''}{host}{port}"
        url = f"{scheme}://{user + '@' if user else ''}{host}{port}/"
    return {"label": text or mount_name, "protocol": scheme.upper(), "url": url}


def _remember(url: str, user: str, domain: str) -> None:
    """Keep the last servers (never a password)."""
    recent = [r for r in db.get_setting("net_recent", []) if r.get("url") != url]
    recent.insert(0, {"url": url, "user": user, "domain": domain, "ts": int(time.time())})
    db.set_setting("net_recent", recent[:RECENT_MAX])


def recent() -> list[dict]:
    return db.get_setting("net_recent", [])


def forget(url: str) -> None:
    db.set_setting("net_recent", [r for r in recent() if r.get("url") != url])


NET_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs2", "fuse.gvfsd-fuse",
          "afpfs", "webdav", "9p", "fuse.s3fs", "ncpfs"}
_net_cache: dict = {"ts": 0.0, "mounts": []}


def _net_mounts() -> list[str]:
    """Mount points of network file systems, longest first. Cached for 5 s: a listing asks for each entry."""
    now = time.monotonic()
    if now - _net_cache["ts"] > 5:
        from .. import platform as plat

        mnts = [m for _, m, t in plat.mounts() if t in NET_FS]
        mnts.append(str(gvfs_dir()))
        try:
            from .rclone import MNT

            mnts.append(str(MNT))
        except ImportError:
            pass
        _net_cache.update(ts=now, mounts=sorted(set(mnts), key=len, reverse=True))
    return _net_cache["mounts"]


def is_network(path: str) -> bool:
    """True when `path` is on a network share (gvfs, rclone, a kernel SMB or NFS mount, a Windows UNC path)."""
    p = str(path)
    if p.startswith(("\\\\", "//")):
        return True
    for m in _net_mounts():
        if inside(p, m):
            return True
    return False


_COPY_LIMIT = 2 * 1024**3


def readable(path: str) -> str:
    """A path that supports random reads. Zip and 7z readers jump around in the file, and gvfs FTP cannot.
    A file on a share gets a local copy (one sequential read), cached by path, size and time."""
    if not is_network(path):
        return path
    import hashlib
    import shutil

    from ..config import DATA_DIR

    st = os.stat(path)
    d = DATA_DIR / "netcache"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
    out = d / (key + "".join(Path(path).suffixes[-2:]))
    if out.exists():
        os.utime(out)
        return str(out)
    tmp = out.with_name(out.name + ".part")
    with open(path, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo, 4 * 1024 * 1024)
    os.replace(tmp, out)
    # Keep the cache under 2 GB: the copies used longest ago go first.
    files = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        if total <= _COPY_LIMIT or p == out:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
    return str(out)


def scratch_for(target: str | Path) -> Path:
    """Where to write a file that ends up at `target`. A share gets a local scratch folder: zip, 7z and
    PDF writers jump back in the file, and gvfs on SFTP answers that with an I/O error. The caller then
    moves the finished file to the share (shutil.move copies it in one pass)."""
    t = Path(target)
    if not is_network(str(t)):
        return t.parent
    from ..config import DATA_DIR

    d = DATA_DIR / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def kernel_mounts() -> list[dict]:
    """Network file systems that the system mounted (fstab, mount -t cifs, sshfs). Raven lists them as network places."""
    out = []
    from .. import platform as plat

    for dev, mnt, fstype in plat.mounts():
        if fstype in ("cifs", "smb3", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs2"):
            out.append({"label": f"{Path(mnt).name or mnt} ({dev})", "path": mnt, "protocol": fstype.replace("fuse.", "").upper()})
    return out
