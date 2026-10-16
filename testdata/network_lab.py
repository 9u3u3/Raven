"""Network test lab (PLAN2 P2-9): run Raven's file features on real network shares, on this computer.

1. Servers: `rclone serve` for SFTP, WebDAV, FTP and S3, and Samba in a container (docker) for SMB.
2. Mounts: each share mounts two ways, as Raven does it: `rclone mount` (FUSE) and `gio mount` (gvfs, through
   Raven's own /net/connect). gvfs on this computer has no WebDAV, S3 or NFS backend.
3. Features: a fresh sidecar (temporary data folder) runs each feature on each mount through its HTTP API.
4. Faults: the server stops during a copy and while a folder is open, a slow link, a change from another
   computer, odd names, and (with --big) a file larger than 4 GB.
5. Result: docs/network-matrix.md, and the exit code is 0 when no P0 feature fails.

Usage: core/.venv/bin/python testdata/network_lab.py [--protocols sftp,webdav,ftp,s3,smb] [--paths rclone,gvfs]
                                                    [--no-faults] [--big] [--keep]
NFS: `rclone serve nfs` works, but a kernel NFS mount needs root, so the lab does not test it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from run_device_test import Sidecar  # noqa: E402

USER, PASS = "raven", "labpass"
SMB_IMAGE = "dockurr/samba"
P0 = {"list", "info", "preview", "thumbnail", "copy in", "copy out", "move", "rename", "new folder", "trash or delete",
      "undo", "copy queue", "conflicts", "fault: server stops during copy", "fault: server gone while open"}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def obscure(pw: str) -> str:
    return subprocess.run(["rclone", "obscure", pw], capture_output=True, text=True, check=True).stdout.strip()


def wait_port(port: int, timeout: float = 15) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


class Server:
    """One share: a folder on this computer, served over one protocol."""

    def __init__(self, proto: str, lab: Path) -> None:
        self.proto = proto
        self.dir = lab / "share" / proto
        self.dir.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.container: str | None = None
        self.bwlimit: str | None = None
        self.log = open(lab / f"server-{proto}.log", "a")

    # The folder that a mount shows. S3 serves the folders below its root as buckets.
    @property
    def data(self) -> Path:
        return self.dir / "bucket" if self.proto == "s3" else self.dir

    def start(self) -> bool:
        p = self.proto
        addr = f"127.0.0.1:{self.port}"
        if p == "smb":
            if not shutil.which("docker"):
                return False
            # gvfs ignores a port in an smb:// address, so the share uses 445 when nothing else does.
            try:
                socket.create_connection(("127.0.0.1", 445), 0.3).close()
            except OSError:
                self.port = 445
            self.container = f"raven-lab-smb-{self.port}"
            r = subprocess.run(["docker", "run", "-d", "--rm", "--name", self.container, "-p", f"127.0.0.1:{self.port}:445",
                                "-e", f"USER={USER}", "-e", f"PASS={PASS}", "-e", f"UID={os.getuid()}", "-e", f"GID={os.getgid()}",
                                "-v", f"{self.dir}:/storage", SMB_IMAGE], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  smb: docker failed: {r.stderr.strip()[:200]}")
                self.container = None
                return False
            return wait_port(self.port, 60)
        if p == "s3":
            self.data.mkdir(exist_ok=True)
            cmd = ["rclone", "serve", "s3", str(self.dir), "--addr", addr, "--auth-key", f"{USER},{PASS}", "--vfs-cache-mode", "off"]
        elif p == "sftp":
            cmd = ["rclone", "serve", "sftp", str(self.dir), "--addr", addr, "--user", USER, "--pass", PASS]
        elif p == "webdav":
            cmd = ["rclone", "serve", "webdav", str(self.dir), "--addr", addr, "--user", USER, "--pass", PASS]
        elif p == "ftp":
            cmd = ["rclone", "serve", "ftp", str(self.dir), "--addr", addr, "--user", USER, "--pass", PASS,
                   "--passive-port", "-".join(map(str, sorted((free_port(), free_port()))))]
        else:
            return False
        # rclone serve caches its folder list for 5 minutes. A real SFTP or SMB server does not.
        cmd += ["--dir-cache-time", "1s"]
        if self.bwlimit:
            cmd += ["--bwlimit", self.bwlimit]
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
        return wait_port(self.port)

    def stop(self) -> None:
        if self.container:
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
            self.container = None
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(5)
        self.proc = None

    def rclone_remote(self) -> str:
        ob = obscure(PASS)
        h = f"host=127.0.0.1,port={self.port},user={USER},pass={ob}"
        return {
            "sftp": f":sftp,{h}:", "ftp": f":ftp,{h}:",
            "webdav": f":webdav,url='http://127.0.0.1:{self.port}',vendor=other,user={USER},pass={ob}:",
            "s3": f":s3,provider=Rclone,endpoint='http://127.0.0.1:{self.port}',access_key_id={USER},secret_access_key={PASS}:bucket",
            "smb": f":smb,{h}:Shared",  # dockurr/samba names its share "Shared"
        }[self.proto]

    def gvfs_url(self) -> str | None:
        port = "" if self.port == 445 else f":{self.port}"
        return {"sftp": f"sftp://{USER}@127.0.0.1:{self.port}/", "ftp": f"ftp://{USER}@127.0.0.1:{self.port}/",
                "smb": f"smb://127.0.0.1{port}/Shared"}.get(self.proto)


class Lab:
    def __init__(self, a) -> None:
        self.a = a
        self.lab = Path(tempfile.mkdtemp(prefix="raven-netlab-"))
        self.local = self.lab / "local"
        self.local.mkdir()
        self.servers: dict[str, Server] = {}
        self.mounts: list[tuple[str, str, Path, Path, Server]] = []  # (proto, way, mount root, server folder, server)
        self.rclone_procs: list[tuple[Path, subprocess.Popen]] = []
        self.results: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
        self.sc: Sidecar | None = None

    # ------------------------------------------------------------ http helpers

    def get(self, route, timeout=60, **q):
        return json.load(self.raw(route, timeout, **q))

    def raw(self, route, timeout=60, **q):
        url = f"http://127.0.0.1:{self.sc.port}{route}" + ("?" + urllib.parse.urlencode(q) if q else "")
        r = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.sc.token}"})
        return urllib.request.urlopen(r, timeout=timeout)

    def post(self, route, body, timeout=120):
        r = urllib.request.Request(f"http://127.0.0.1:{self.sc.port}{route}", json.dumps(body).encode(),
                                   {"Authorization": f"Bearer {self.sc.token}", "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(r, timeout=timeout))

    # ------------------------------------------------------------ set-up

    def seed(self, d: Path) -> None:
        """Files that the features use, written on the server side."""
        d.mkdir(parents=True, exist_ok=True)
        (d / "notes.txt").write_text("Lab notes: the pelican lantern phrase is quixotic heron.\n")
        (d / "readme.md").write_text("# Lab readme\n\nThe network lab checks every feature.\n")
        (d / "Untitled(1).txt").write_text("Garden Watering Schedule\nWater the roses on Monday.\n")
        from PIL import Image

        noise = Image.effect_noise((320, 200), 60).convert("RGB")
        noise.save(d / "photo.png")
        noise.save(d / "photo copy.png")
        Image.new("RGB", (64, 64), "teal").save(d / "noext_image", "PNG")
        (d / ".hidden_file").write_text("hidden\n")
        sub = d / "folder"
        sub.mkdir(exist_ok=True)
        (sub / "inner.txt").write_text("inner file, the same line again and again.\n" * 5000)
        old = time.time() - 3600
        for p in d.rglob("*"):
            os.utime(p, (old, old))

    def start(self) -> None:
        for proto in self.a.protocols:
            s = Server(proto, self.lab)
            print(f"server {proto} … ", end="", flush=True)
            ok = s.start()
            print(f"ok on port {s.port}" if ok else "NOT RUNNING")
            if ok:
                self.servers[proto] = s
        data = self.lab / "sidecar"
        data.mkdir()
        self.sc = Sidecar(str(data))
        for proto, s in self.servers.items():
            for way in self.a.paths:
                sub = f"{way}"
                self.seed(s.data / sub)
                root = self.mount(proto, way, s)
                if root is None:
                    continue
                self.mounts.append((proto, way, root / sub, s.data / sub, s))

    def mount(self, proto: str, way: str, s: Server) -> Path | None:
        if way == "rclone":
            mp = self.lab / "mnt" / proto
            mp.mkdir(parents=True, exist_ok=True)
            p = subprocess.Popen(["rclone", "mount", s.rclone_remote(), str(mp), "--vfs-cache-mode", "writes",
                                  "--dir-cache-time", "5s", "--cache-dir", str(self.lab / "rclone-cache"), "--volname", f"lab-{proto}"],
                                 stdout=s.log, stderr=subprocess.STDOUT)
            for _ in range(100):
                if os.path.ismount(mp):
                    self.rclone_procs.append((mp, p))
                    print(f"  mounted {proto} with rclone at {mp}")
                    return mp
                time.sleep(0.1)
            p.kill()
            print(f"  {proto}: rclone mount failed")
            return None
        url = s.gvfs_url()
        if not url:
            return None
        try:
            r = self.post("/net/connect", {"url": url, "user": USER, "password": PASS, "trust": True}, timeout=90)
        except urllib.error.HTTPError as e:
            print(f"  {proto}: gio mount failed: {e.read().decode()[:200]}")
            return None
        path = Path(r.get("path") or r.get("mount") or "")
        print(f"  mounted {proto} with gvfs at {path}")
        return path if str(path) else None

    def stop(self) -> None:
        for mp, p in self.rclone_procs:
            subprocess.run(["fusermount3", "-uz", str(mp)], capture_output=True)
            p.kill()
        if self.sc:
            for proto, way, root, _, _ in self.mounts:
                if way == "gvfs":
                    try:
                        self.post("/net/disconnect", {"path": str(root.parent)}, timeout=30)
                    except Exception:  # noqa: BLE001
                        pass
            self.sc.stop()
        for s in self.servers.values():
            s.stop()
        if not self.a.keep:
            shutil.rmtree(self.lab, ignore_errors=True)
        else:
            print(f"lab kept: {self.lab}")

    # ------------------------------------------------------------ features

    def record(self, key, feature, status, detail="") -> None:
        self.results.setdefault(key, {})[feature] = (status, str(detail)[:140])
        mark = {"pass": "PASS", "fail": "FAIL", "n/a": " -- "}[status]
        print(f"    {mark}  {feature:34} {str(detail)[:90]}")

    def run_feature(self, key, name, fn, *args) -> None:
        t = time.perf_counter()
        try:
            res = fn(*args)
            status, detail = res if isinstance(res, tuple) else (("pass", "") if res else ("fail", ""))
        except urllib.error.HTTPError as e:
            status, detail = "fail", f"HTTP {e.code}: {e.read().decode()[:120]}"
        except Exception as e:  # noqa: BLE001
            status, detail = "fail", f"{type(e).__name__}: {e}"
        ms = (time.perf_counter() - t) * 1000
        self.record(key, name, status, f"{detail} ({ms:.0f} ms)".strip())

    @staticmethod
    def until(fn, timeout: float = 25) -> bool:
        """rclone uploads a write a few seconds later, and a mount caches the folder list: wait for the result."""
        t = time.time()
        while time.time() - t < timeout:
            try:
                if fn():
                    return True
            except OSError:
                pass
            time.sleep(0.3)
        return False

    def journal_undo(self):
        return self.post("/fs/undo", {})

    def features(self, proto, way, M: Path, S: Path) -> None:
        key = (proto, way)
        L = self.local
        print(f"\n== {proto} via {way}: {M}")

        def f_list():
            r = self.get("/fs/list", path=str(M))
            names = {e["name"] for e in r["entries"]}
            ok = {"notes.txt", "photo.png", "folder"} <= names
            return ("pass" if ok else "fail", f"{len(names)} items")

        def f_hidden():
            r = self.get("/fs/list", path=str(M), hidden="true")
            e = [x for x in r["entries"] if x["name"] == ".hidden_file"]
            return ("pass" if e and e[0]["is_hidden"] else "fail", "")

        def f_info():
            r = self.get("/fs/info", path=str(M / "noext_image"))
            return ("pass" if r["kind"] == "PNG" and r.get("mode") else "fail", f"kind {r['kind']} mode {r.get('mode')}")

        def f_preview():
            r = self.get("/fs/text", path=str(M / "notes.txt"))
            return ("pass" if "pelican" in json.dumps(r) else "fail", "")

        def f_thumb():
            b = self.raw("/fs/thumb", path=str(M / "photo.png"), size=128).read()
            return ("pass" if b[:4] == b"RIFF" and b[8:12] == b"WEBP" else "fail", f"{len(b)} bytes")

        def f_copy_in():
            src = L / f"in-{proto}-{way}.txt"
            src.write_text("copied in\n")
            self.post("/fs/copy", {"paths": [str(src)], "dst": str(M)})
            return ("pass" if self.until(lambda: (S / src.name).exists()) else "fail", "")

        def f_copy_out():
            out = L / f"out-{proto}-{way}"
            out.mkdir(exist_ok=True)
            self.post("/fs/copy", {"paths": [str(M / "notes.txt")], "dst": str(out)})
            return ("pass" if (out / "notes.txt").read_text().startswith("Lab notes") else "fail", "")

        def f_mkdir():
            self.post("/fs/mkdir", {"parent": str(M), "name": "made here"})
            # S3 has no folders: an empty one exists only on the mount side until a file goes in.
            where = M if proto == "s3" else S
            return ("pass" if self.until(lambda: (where / "made here").is_dir()) else "fail", "")

        def f_newfile():
            self.post("/fs/newfile", {"parent": str(M), "name": "new.txt"})
            return ("pass" if self.until(lambda: (S / "new.txt").exists()) else "fail", "")

        def f_move():
            self.until(lambda: (M / "new.txt").exists())
            self.post("/fs/move", {"paths": [str(M / "new.txt")], "dst": str(M / "made here")})
            return ("pass" if self.until(lambda: (S / "made here" / "new.txt").exists() and not (S / "new.txt").exists()) else "fail", "")

        def f_rename():
            self.post("/fs/rename", {"path": str(M / "made here" / "new.txt"), "name": "renamed.txt"})
            return ("pass" if self.until(lambda: (S / "made here" / "renamed.txt").exists()) else "fail", "")

        def f_batch():
            (S / "b1.txt").write_text("1")
            (S / "b2.txt").write_text("2")
            self.until(lambda: (M / "b1.txt").exists() and (M / "b2.txt").exists())
            self.post("/fs/rename_many", {"items": [{"path": str(M / "b1.txt"), "name": "batch 1.txt"},
                                                     {"path": str(M / "b2.txt"), "name": "batch 2.txt"}]})
            return ("pass" if self.until(lambda: (S / "batch 1.txt").exists() and (S / "batch 2.txt").exists()) else "fail", "")

        def f_undo():
            self.journal_undo()  # undoes the batch rename
            ok = self.until(lambda: (S / "b1.txt").exists() and (S / "b2.txt").exists())
            self.post("/fs/redo", {})
            ok2 = self.until(lambda: (S / "batch 1.txt").exists())
            return ("pass" if ok and ok2 else "fail", f"undo {ok} redo {ok2}")

        def f_trash():
            (S / "to_trash.txt").write_text("bye")
            self.until(lambda: (M / "to_trash.txt").exists())
            r = self.post("/fs/trash", {"paths": [str(M / "to_trash.txt")]})
            gone = self.until(lambda: not (S / "to_trash.txt").exists())
            if not gone:
                return ("fail", f"still there: {json.dumps(r)[:80]}")
            self.journal_undo()
            back = self.until(lambda: (S / "to_trash.txt").exists())
            return ("pass" if back else "fail", f"trash ok, undo brings it back: {back}")

        def f_delete():
            (S / "to_delete.txt").write_text("bye")
            self.until(lambda: (M / "to_delete.txt").exists())
            self.post("/fs/delete", {"paths": [str(M / "to_delete.txt")]})
            return ("pass" if self.until(lambda: not (S / "to_delete.txt").exists()) else "fail", "")

        def f_conflicts():
            r = self.post("/fs/conflicts", {"paths": [str(L / f"in-{proto}-{way}.txt")], "dst": str(M)})
            return ("pass" if r.get("conflicts") else "fail", json.dumps(r)[:80])

        def f_queue():
            big = L / "queue_20mb.bin"
            if not big.exists():
                big.write_bytes(os.urandom(20 * 1024 * 1024))
            j = self.post("/fs/jobs", {"kind": "copy", "paths": [str(big)], "dst": str(M)})
            t = time.time()
            while time.time() - t < 180:
                js = self.get("/fs/jobs")
                job = next((x for x in js.get("jobs", js if isinstance(js, list) else []) if x.get("id") == j.get("id")), None)
                if job and job.get("status") in ("done", "error", "cancelled", "failed"):
                    break
                time.sleep(0.5)
            ok = self.until(lambda: (S / big.name).stat().st_size == big.stat().st_size, 60)
            return ("pass" if ok else "fail", f"status {job.get('status') if job else '?'} in {time.time() - t:.1f} s")

        def f_archive():
            r = self.post("/fs/archive", {"paths": [str(M / "notes.txt"), str(M / "readme.md")], "format": "zip", "name": "lab"})
            z = S / "lab.zip"
            if not self.until(lambda: z.exists()):
                return ("fail", json.dumps(r)[:80])
            inside = self.get("/fs/list", path=str(M / "lab.zip"))
            names = {e["name"] for e in inside["entries"]}
            self.post("/fs/extract", {"path": str(M / "lab.zip")})
            ext = self.until(lambda: any(p.parent != S for p in S.rglob("notes.txt")))
            return ("pass" if "notes.txt" in names and ext else "fail", f"open {sorted(names)[:3]} extract {ext}")

        def f_hash():
            r = self.get("/fs/hash", path=str(M / "notes.txt"))
            return ("pass" if r.get("sha256") or r.get("blake3") else "fail", "")

        def f_convert():
            r = self.post("/convert/run", {"paths": [str(M / "photo.png")], "target": "jpg"}, timeout=120)
            return ("pass" if self.until(lambda: (S / "photo.jpg").exists()) else "fail",
                    "; ".join(str(e.get("error", e)) for e in r.get("errors") or [])[:120])

        def f_compress():
            r = self.post("/compress/run", {"root": str(M), "paths": [str(M / "folder")], "method": "zip"}, timeout=180)
            return ("pass" if self.until(lambda: (S / "folder.zip").exists()) else "fail", "")

        def f_space():
            self.post("/space/scan", {"path": str(M)})
            t = time.time()
            while time.time() - t < 60:
                st = self.get("/space/status")
                if not st.get("scanning"):
                    break
                time.sleep(0.5)
            tree = self.get("/space/tree", path=str(M), depth=1)
            return ("pass" if tree else "fail", f"{time.time() - t:.1f} s")

        def f_dupes():
            self.post("/space/duplicates", {"path": str(M)})
            t = time.time()
            groups = []
            while time.time() - t < 90:
                r = self.get("/space/duplicates", path=str(M))
                groups = r.get("groups") or []
                if not r.get("running") and (groups or time.time() - t > 10):
                    break
                time.sleep(1)
            ok = any({"photo.png", "photo copy.png"} <= {os.path.basename(f.get("path", "")) for f in g.get("files", [])} for g in groups)
            return ("pass" if ok else "fail", f"{len(groups)} groups")

        def f_name():
            r = self.post("/name/suggest", {"path": str(M / "Untitled(1).txt"), "use_llm": False})
            items = r.get("items") or []
            return ("pass" if items else "fail", items[0]["name"] if items else "none (the file is not indexed yet)")

        def f_organize():
            r = self.post("/organize/suggest", {"path": str(M)})
            return ("pass", f"{len(r.get('moves', []))} moves")

        def f_agent():
            r = self.post("/agent/plan", {"command": "make folder agent_made", "cwd": str(M), "selection": []}, timeout=120)
            run = self.post("/agent/run", {"id": r["id"], "keep": {}})
            where = M if proto == "s3" else S
            return ("pass" if self.until(lambda: (where / "agent_made").is_dir()) else "fail", run.get("label", ""))

        def f_rule():
            (S / "sub_for_rule").mkdir(exist_ok=True)
            r = self.post("/rules", {"folder": str(M), "match": "ext", "value": "rulex", "action": "move", "target": str(M / "sub_for_rule")})
            (S / "x.rulex").write_text("rule me")
            old = time.time() - 120
            os.utime(S / "x.rulex", (old, old))
            self.until(lambda: (M / "x.rulex").exists())
            self.post(f"/rules/{r['id']}/run", {})
            ok = self.until(lambda: (S / "sub_for_rule" / "x.rulex").exists())
            requests_delete(self, f"/rules/{r['id']}")
            return ("pass" if ok else "fail", "")

        def f_index():
            self.post("/index/roots", {"roots": [str(M)]})
            t = time.time()
            hits: list[str] = []
            while time.time() - t < 150:
                r = self.get("/search", q="quixotic heron", limit=5)
                hits = [h.get("path", "") for h in r.get("results", [])]
                if any(h.startswith(str(M)) and h.endswith("notes.txt") for h in hits):
                    break
                time.sleep(1.5)
            ok = any(h.startswith(str(M)) and h.endswith("notes.txt") for h in hits)
            # The rename tool reads the indexed text: wait until the generic file has its title.
            self.until(lambda: bool(self.post("/name/suggest", {"path": str(M / "Untitled(1).txt"), "use_llm": False})["items"]), 30)
            return ("pass" if ok else "fail", f"{time.time() - t:.1f} s")

        def f_watch_external():
            # Another computer adds a file: the share changes on the server side, not through the mount.
            (S / "from_another_pc_zebra.txt").write_text("The zebra mandolin phrase.\n")
            t = time.time()
            while time.time() - t < 75:
                n = self.get("/index/names").get("items") or []
                if "from_another_pc_zebra" in json.dumps(n):
                    return ("pass", f"seen after {time.time() - t:.0f} s")
                time.sleep(3)
            return ("fail", "not seen in 75 s")

        steps = [
            ("list", f_list), ("hidden files", f_hidden), ("info", f_info), ("preview", f_preview), ("thumbnail", f_thumb),
            ("copy in", f_copy_in), ("copy out", f_copy_out), ("new folder", f_mkdir), ("new file", f_newfile), ("move", f_move),
            ("rename", f_rename), ("batch rename", f_batch), ("undo", f_undo), ("trash or delete", f_trash),
            ("permanent delete", f_delete), ("conflicts", f_conflicts), ("copy queue", f_queue), ("archives", f_archive),
            ("hashes", f_hash), ("convert", f_convert), ("compress", f_compress), ("space scan", f_space),
            ("duplicates", f_dupes), ("organize", f_organize), ("agent", f_agent), ("watch rule", f_rule),
            ("index and find", f_index), ("name unnamed files", f_name), ("change from another computer", f_watch_external),
        ]
        for name, fn in steps:
            self.run_feature(key, name, fn)

    # ------------------------------------------------------------ faults

    def faults(self, proto, way, M: Path, S: Path, srv: Server) -> None:
        key = (proto, way)
        print(f"\n== faults: {proto} via {way}")
        if way == "gvfs":
            # The rclone fault tests restart the same server, and gvfs does not reconnect by itself.
            self.mount(proto, "gvfs", srv)
            self.until(lambda: M.is_dir(), 20)

        def restart(bw: str | None = None):
            """Restart the server. A gvfs mount does not reconnect by itself: mount it again."""
            srv.stop()
            srv.bwlimit = bw
            srv.start()
            if way == "gvfs":
                self.mount(proto, "gvfs", srv)
            self.until(lambda: M.is_dir(), 20)

        def health_ms():
            t = time.perf_counter()
            self.get("/health", timeout=10)
            return (time.perf_counter() - t) * 1000

        def f_names():
            bad = []
            for n in ("ünïcödé ñame.txt", "with space and (parens).txt"):
                try:
                    self.post("/fs/newfile", {"parent": str(M), "name": n})
                    if not self.until(lambda: (S / n).exists()):
                        bad.append(n)
                except urllib.error.HTTPError as e:
                    bad.append(f"{n}: {e.code}")
            for n in ("colon:name.txt", "question?.txt"):
                try:
                    self.post("/fs/newfile", {"parent": str(M), "name": n})
                except urllib.error.HTTPError as e:
                    if e.code >= 500:
                        bad.append(f"{n}: HTTP {e.code} (a crash, not a clear error)")
            try:
                self.post("/fs/newfile", {"parent": str(M), "name": "Case.txt"})
                self.post("/fs/newfile", {"parent": str(M), "name": "case.txt"})
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    bad.append(f"case clash: HTTP {e.code}")
            return ("fail" if bad else "pass", "; ".join(bad))

        def f_slow():
            restart("512k")
            t = time.perf_counter()
            self.get("/fs/list", path=str(M), timeout=30)
            lst = (time.perf_counter() - t) * 1000
            h = health_ms()
            restart(None)
            return ("pass" if h < 500 else "fail", f"list {lst:.0f} ms, health {h:.0f} ms")

        def f_stop_during_copy():
            # A slow link, so the copy still runs when the server stops.
            restart("4M")
            big = self.local / "fault_40mb.bin"
            if not big.exists():
                big.write_bytes(os.urandom(40 * 1024 * 1024))
            j = self.post("/fs/jobs", {"kind": "copy", "paths": [str(big)], "dst": str(M)})
            time.sleep(2)
            srv.stop()
            t = time.time()
            status, hs = None, []
            while time.time() - t < 90:
                hs.append(health_ms())
                js = self.get("/fs/jobs")
                job = next((x for x in js.get("jobs", js if isinstance(js, list) else []) if x.get("id") == j.get("id")), None)
                status = job.get("status") if job else None
                if status not in ("running", "queued", "paused", None):
                    break
                time.sleep(1)
            waited = time.time() - t
            restart(None)
            # The share must hold the whole file or nothing (rclone may finish its upload after the restart).
            settled = self.until(lambda: not (S / big.name).exists() or (S / big.name).stat().st_size == big.stat().st_size, 90)
            half = not settled
            # rclone's write cache takes the whole file at once: "done" is right there, the upload comes later.
            status_ok = status in ("error", "failed", "cancelled") or (status == "done" and way == "rclone")
            ok = status_ok and not half and max(hs) < 1000
            return ("pass" if ok else "fail",
                    f"job {status} after {waited:.0f} s, half file left: {half}, worst health {max(hs):.0f} ms")

        def f_gone_while_open():
            srv.stop()
            time.sleep(0.5)
            t = time.perf_counter()
            err = ""
            try:
                self.get("/fs/list", path=str(M / "folder"), timeout=60)
                err = "listed (cache)"
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}: {e.read().decode()[:60]}"
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}"
            took = time.perf_counter() - t
            h = health_ms()
            restart(None)
            return ("pass" if took <= 6 and h < 1000 else "fail", f"{err} after {took:.1f} s, health {h:.0f} ms")

        def f_big():
            if not self.a.big:
                return ("n/a", "run with --big")
            big = self.local / "sparse_4100mb.bin"
            with open(big, "wb") as f:
                f.truncate(4100 * 1024 * 1024)
            self.post("/fs/copy", {"paths": [str(big)], "dst": str(M)}, timeout=1800)
            ok = (S / big.name).exists() and (S / big.name).stat().st_size == big.stat().st_size
            return ("pass" if ok else "fail", "")

        for name, fn in (("fault: odd names", f_names), ("fault: slow link", f_slow),
                         ("fault: server stops during copy", f_stop_during_copy),
                         ("fault: server gone while open", f_gone_while_open), ("fault: file over 4 GB", f_big)):
            self.run_feature(key, name, fn)

    # ------------------------------------------------------------ report

    def report(self) -> int:
        cols = list(self.results)
        feats: list[str] = []
        for c in cols:
            for f in self.results[c]:
                if f not in feats:
                    feats.append(f)
        lines = [
            "# Network feature matrix",
            "",
            f"Made by `testdata/network_lab.py` on {time.strftime('%Y-%m-%d %H:%M')}. Servers run on this computer "
            "(`rclone serve`, Samba in docker). Each share mounts with rclone (FUSE) and with gvfs (`gio mount`).",
            "",
            "PASS: works. FAIL: broken. --: not run. P0 rows are marked with *.",
            "",
            "| Feature | " + " | ".join(f"{p} ({w})" for p, w in cols) + " |",
            "|---|" + "---|" * len(cols),
        ]
        p0_fail = 0
        for f in feats:
            row = []
            for c in cols:
                st, _ = self.results[c].get(f, ("n/a", ""))
                row.append({"pass": "PASS", "fail": "**FAIL**", "n/a": "--"}[st])
                if st == "fail" and f in P0:
                    p0_fail += 1
            lines.append(f"| {f}{' *' if f in P0 else ''} | " + " | ".join(row) + " |")
        lines += ["", "## Details", ""]
        for c in cols:
            lines.append(f"### {c[0]} via {c[1]}")
            lines.append("")
            for f, (st, d) in self.results[c].items():
                if st != "pass" or "ms" in d:
                    lines.append(f"- {f}: {st.upper()} {d}")
            lines.append("")
        missing = [p for p in self.a.protocols if p not in self.servers]
        if missing:
            lines.append(f"Not run (the server did not start): {', '.join(missing)}.")
        lines.append("NFS: not run. A kernel NFS mount needs root.")
        out = HERE.parent / "docs" / "network-matrix.md"
        out.write_text("\n".join(lines) + "\n")
        print(f"\nwrote {out}  ·  P0 failures: {p0_fail}")
        return 1 if p0_fail else 0


def requests_delete(lab: Lab, route: str) -> None:
    r = urllib.request.Request(f"http://127.0.0.1:{lab.sc.port}{route}", headers={"Authorization": f"Bearer {lab.sc.token}"},
                               method="DELETE")
    urllib.request.urlopen(r, timeout=30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocols", default="sftp,webdav,ftp,s3,smb")
    ap.add_argument("--paths", default="rclone,gvfs")
    ap.add_argument("--no-faults", action="store_true")
    ap.add_argument("--only-faults", action="store_true")
    ap.add_argument("--big", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    a.protocols = [p for p in a.protocols.split(",") if p]
    a.paths = [p for p in a.paths.split(",") if p]
    lab = Lab(a)
    # SIGTERM (timeout, a kill) must still stop the servers, the mounts and the container.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    code = 1
    try:
        lab.start()
        for proto, way, M, S, srv in lab.mounts if not a.only_faults else []:
            lab.features(proto, way, M, S)
        if not a.no_faults:
            for proto, way, M, S, srv in lab.mounts:
                lab.faults(proto, way, M, S, srv)
        code = lab.report()
    finally:
        lab.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
