# Network feature matrix

Made by `testdata/network_lab.py` on 2026-10-16 05:35. Servers run on this computer (`rclone serve`, Samba in docker). Each share mounts with rclone (FUSE) and with gvfs (`gio mount`).

PASS: works. FAIL: broken. --: not run. P0 rows are marked with *.

| Feature | sftp (rclone) | sftp (gvfs) | webdav (rclone) | ftp (rclone) | ftp (gvfs) | s3 (rclone) | smb (rclone) | smb (gvfs) |
|---|---|---|---|---|---|---|---|---|
| list * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| hidden files | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| info * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| preview * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| thumbnail * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| copy in * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| copy out * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| new folder * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| new file | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| move * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| rename * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| batch rename | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| undo * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| trash or delete * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| permanent delete | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| conflicts * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| copy queue * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| archives | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| hashes | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| convert | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| compress | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| space scan | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| duplicates | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| organize | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| agent | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| watch rule | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| index and find | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| name unnamed files | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| change from another computer | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| fault: odd names | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| fault: slow link | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| fault: server stops during copy * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| fault: server gone while open * | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| fault: file over 4 GB | -- | -- | -- | -- | -- | -- | -- | -- |

## Details

### sftp via rclone

- list: PASS 8 items (5 ms)
- hidden files: PASS (1 ms)
- info: PASS kind PNG mode -rw-r--r-- (7 ms)
- preview: PASS (2 ms)
- thumbnail: PASS 3544 bytes (57 ms)
- copy in: PASS (5107 ms)
- copy out: PASS (4 ms)
- new folder: PASS (2 ms)
- new file: PASS (5104 ms)
- move: PASS (12 ms)
- rename: PASS (11 ms)
- batch rename: PASS (5112 ms)
- undo: PASS undo True redo True (7 ms)
- trash or delete: PASS trash ok, undo brings it back: True (5124 ms)
- permanent delete: PASS (5114 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-sftp-rclone.txt", "t (3 ms)
- copy queue: PASS status done in 5.3 s (5351 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (10220 ms)
- hashes: PASS (5 ms)
- convert: PASS (5154 ms)
- compress: PASS (5164 ms)
- space scan: PASS 0.5 s (508 ms)
- duplicates: PASS 2 groups (1005 ms)
- organize: PASS 5 moves (8 ms)
- agent: PASS Create folder agent_made (4 ms)
- watch rule: PASS (3612 ms)
- index and find: PASS 1.6 s (1551 ms)
- name unnamed files: PASS Garden Watering Schedule.txt (2 ms)
- change from another computer: PASS seen after 30 s (30016 ms)
- fault: odd names: PASS (10221 ms)
- fault: slow link: PASS list 8 ms, health 1 ms (423 ms)
- fault: server stops during copy: PASS job done after 0 s, half file left: False, worst health 1 ms (2523 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /tmp/raven-netlab-e6tv4d90/mnt/sftp/rcl after 0.0 s, health 1 ms (708 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### sftp via gvfs

- list: PASS 8 items (7 ms)
- hidden files: PASS (5 ms)
- info: PASS kind PNG mode -rw-r--r-- (7 ms)
- preview: PASS (2 ms)
- thumbnail: PASS 3510 bytes (9 ms)
- copy in: PASS (9 ms)
- copy out: PASS (4 ms)
- new folder: PASS (3 ms)
- new file: PASS (3 ms)
- move: PASS (4 ms)
- rename: PASS (4 ms)
- batch rename: PASS (1214 ms)
- undo: PASS undo True redo True (14 ms)
- trash or delete: PASS trash ok, undo brings it back: True (1219 ms)
- permanent delete: PASS (1209 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-sftp-gvfs.txt", "tar (2 ms)
- copy queue: PASS status done in 0.5 s (505 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (28 ms)
- hashes: PASS (4 ms)
- convert: PASS (37 ms)
- compress: PASS (51 ms)
- space scan: PASS 0.5 s (509 ms)
- duplicates: PASS 2 groups (1004 ms)
- organize: PASS 5 moves (14 ms)
- agent: PASS Create folder agent_made (6 ms)
- watch rule: PASS (1217 ms)
- index and find: PASS 1.5 s (1513 ms)
- name unnamed files: PASS garden watering schedule.txt (2 ms)
- change from another computer: PASS seen after 30 s (30017 ms)
- fault: odd names: PASS (15 ms)
- fault: slow link: PASS list 20 ms, health 1 ms (2179 ms)
- fault: server stops during copy: PASS job error after 0 s, half file left: False, worst health 1 ms (3878 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /run/user/1000/gvfs/sftp:host=127.0.0.1 after 0.0 s, health 1 ms (825 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### webdav via rclone

- list: PASS 8 items (24 ms)
- hidden files: PASS (2 ms)
- info: PASS kind PNG mode -rw-r--r-- (10 ms)
- preview: PASS (9 ms)
- thumbnail: PASS 3534 bytes (15 ms)
- copy in: PASS (5106 ms)
- copy out: PASS (24 ms)
- new folder: PASS (10 ms)
- new file: PASS (5105 ms)
- move: PASS (65 ms)
- rename: PASS (42 ms)
- batch rename: PASS (5276 ms)
- undo: PASS undo True redo True (350 ms)
- trash or delete: PASS trash ok, undo brings it back: True (4656 ms)
- permanent delete: PASS (5121 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-webdav-rclone.txt",  (1 ms)
- copy queue: PASS status done in 5.3 s (5307 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (10311 ms)
- hashes: PASS (24 ms)
- convert: PASS (5132 ms)
- compress: PASS (5310 ms)
- space scan: PASS 0.5 s (517 ms)
- duplicates: PASS 2 groups (1004 ms)
- organize: PASS 5 moves (3 ms)
- agent: PASS Create folder agent_made (3 ms)
- watch rule: PASS (3672 ms)
- index and find: PASS 1.5 s (1536 ms)
- name unnamed files: PASS garden watering schedule.txt (2 ms)
- change from another computer: PASS seen after 30 s (30020 ms)
- fault: odd names: PASS (10253 ms)
- fault: slow link: PASS list 38 ms, health 1 ms (451 ms)
- fault: server stops during copy: PASS job done after 0 s, half file left: False, worst health 1 ms (2413 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /tmp/raven-netlab-e6tv4d90/mnt/webdav/r after 0.0 s, health 1 ms (712 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### ftp via rclone

- list: PASS 8 items (11 ms)
- hidden files: PASS (2 ms)
- info: PASS kind PNG mode -rw-r--r-- (7 ms)
- preview: PASS (6 ms)
- thumbnail: PASS 3542 bytes (16 ms)
- copy in: PASS (5107 ms)
- copy out: PASS (4 ms)
- new folder: PASS (2 ms)
- new file: PASS (5105 ms)
- move: PASS (5 ms)
- rename: PASS (2 ms)
- batch rename: PASS (5112 ms)
- undo: PASS undo True redo True (7 ms)
- trash or delete: PASS trash ok, undo brings it back: True (5117 ms)
- permanent delete: PASS (5112 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-ftp-rclone.txt", "ta (1 ms)
- copy queue: PASS status done in 5.3 s (5305 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (10228 ms)
- hashes: PASS (3 ms)
- convert: PASS (5124 ms)
- compress: PASS (5126 ms)
- space scan: PASS 0.5 s (525 ms)
- duplicates: PASS 2 groups (1003 ms)
- organize: PASS 5 moves (4 ms)
- agent: PASS Create folder agent_made (4 ms)
- watch rule: PASS (3612 ms)
- index and find: PASS 1.5 s (1534 ms)
- name unnamed files: PASS garden watering schedule.txt (1 ms)
- change from another computer: PASS seen after 30 s (30016 ms)
- fault: odd names: PASS (10220 ms)
- fault: slow link: PASS list 4 ms, health 1 ms (417 ms)
- fault: server stops during copy: PASS job done after 0 s, half file left: False, worst health 4 ms (2421 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /tmp/raven-netlab-e6tv4d90/mnt/ftp/rclo after 0.0 s, health 1 ms (707 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### ftp via gvfs

- list: PASS 8 items (10 ms)
- hidden files: PASS (4 ms)
- info: PASS kind PNG mode -rwxrwxrwx (6 ms)
- preview: PASS (2 ms)
- thumbnail: PASS 3468 bytes (7 ms)
- copy in: PASS (7 ms)
- copy out: PASS (4 ms)
- new folder: PASS (3 ms)
- new file: PASS (3 ms)
- move: PASS (3 ms)
- rename: PASS (3 ms)
- batch rename: PASS (1214 ms)
- undo: PASS undo True redo True (15 ms)
- trash or delete: PASS trash ok, undo brings it back: True (1217 ms)
- permanent delete: PASS (1208 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-ftp-gvfs.txt", "targ (2 ms)
- copy queue: PASS status done in 0.5 s (504 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (22 ms)
- hashes: PASS (2 ms)
- convert: PASS (21 ms)
- compress: PASS (36 ms)
- space scan: PASS 0.5 s (525 ms)
- duplicates: PASS 1 groups (1003 ms)
- organize: PASS 5 moves (11 ms)
- agent: PASS Create folder agent_made (4 ms)
- watch rule: PASS (1217 ms)
- index and find: PASS 1.5 s (1515 ms)
- name unnamed files: PASS garden watering schedule.txt (2 ms)
- change from another computer: PASS seen after 30 s (30020 ms)
- fault: odd names: PASS (24 ms)
- fault: slow link: PASS list 8 ms, health 1 ms (436 ms)
- fault: server stops during copy: PASS job error after 0 s, half file left: False, worst health 1 ms (2454 ms)
- fault: server gone while open: PASS HTTP 504: {"error":"The share does not answer. Check the connection, o after 0.0 s, health 1 ms (719 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### s3 via rclone

- list: PASS 8 items (17 ms)
- hidden files: PASS (2 ms)
- info: PASS kind PNG mode -rw-r--r-- (6 ms)
- preview: PASS (2 ms)
- thumbnail: PASS 3546 bytes (6 ms)
- copy in: PASS (5106 ms)
- copy out: PASS (6 ms)
- new folder: PASS (1 ms)
- new file: PASS (5104 ms)
- move: PASS (11 ms)
- rename: PASS (4 ms)
- batch rename: PASS (5118 ms)
- undo: PASS undo True redo True (17 ms)
- trash or delete: PASS trash ok, undo brings it back: True (5118 ms)
- permanent delete: PASS (5113 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-s3-rclone.txt", "tar (2 ms)
- copy queue: PASS status done in 5.3 s (5307 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (10251 ms)
- hashes: PASS (37 ms)
- convert: PASS (5121 ms)
- compress: PASS (5149 ms)
- space scan: PASS 0.5 s (530 ms)
- duplicates: PASS 2 groups (1003 ms)
- organize: PASS 5 moves (3 ms)
- agent: PASS Create folder agent_made (2 ms)
- watch rule: PASS (3645 ms)
- index and find: PASS 1.5 s (1513 ms)
- name unnamed files: PASS garden watering schedule.txt (1 ms)
- change from another computer: PASS seen after 30 s (30021 ms)
- fault: odd names: PASS (10309 ms)
- fault: slow link: PASS list 45 ms, health 1 ms (462 ms)
- fault: server stops during copy: PASS job done after 0 s, half file left: False, worst health 1 ms (2413 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /tmp/raven-netlab-e6tv4d90/mnt/s3/rclon after 0.0 s, health 1 ms (707 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### smb via rclone

- list: PASS 8 items (21 ms)
- hidden files: PASS (3 ms)
- info: PASS kind PNG mode -rw-r--r-- (9 ms)
- preview: PASS (2 ms)
- thumbnail: PASS 3490 bytes (8 ms)
- copy in: PASS (5107 ms)
- copy out: PASS (4 ms)
- new folder: PASS (2 ms)
- new file: PASS (5104 ms)
- move: PASS (5 ms)
- rename: PASS (2 ms)
- batch rename: PASS (5119 ms)
- undo: PASS undo True redo True (17 ms)
- trash or delete: PASS trash ok, undo brings it back: True (5132 ms)
- permanent delete: PASS (5116 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-smb-rclone.txt", "ta (1 ms)
- copy queue: PASS status done in 5.3 s (5307 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (10226 ms)
- hashes: PASS (4 ms)
- convert: PASS (5135 ms)
- compress: PASS (5131 ms)
- space scan: PASS 0.6 s (557 ms)
- duplicates: PASS 2 groups (1003 ms)
- organize: PASS 5 moves (3 ms)
- agent: PASS Create folder agent_made (4 ms)
- watch rule: PASS (3615 ms)
- index and find: PASS 1.5 s (1510 ms)
- name unnamed files: PASS garden watering schedule.txt (1 ms)
- change from another computer: PASS seen after 30 s (30017 ms)
- fault: odd names: PASS (10231 ms)
- fault: slow link: PASS list 4 ms, health 1 ms (682 ms)
- fault: server stops during copy: PASS job done after 0 s, half file left: False, worst health 2 ms (2552 ms)
- fault: server gone while open: PASS HTTP 404: {"error":"Not found: /tmp/raven-netlab-e6tv4d90/mnt/smb/rclo after 0.0 s, health 1 ms (781 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

### smb via gvfs

- list: PASS 8 items (18 ms)
- hidden files: PASS (5 ms)
- info: PASS kind PNG mode -rwx------ (11 ms)
- preview: PASS (3 ms)
- thumbnail: PASS 3554 bytes (11 ms)
- copy in: PASS (9 ms)
- copy out: PASS (5 ms)
- new folder: PASS (3 ms)
- new file: PASS (3 ms)
- move: PASS (5 ms)
- rename: PASS (5 ms)
- batch rename: PASS (12 ms)
- undo: PASS undo True redo True (23 ms)
- trash or delete: PASS trash ok, undo brings it back: True (14 ms)
- permanent delete: PASS (3 ms)
- conflicts: PASS {"conflicts": [{"src": "/tmp/raven-netlab-e6tv4d90/local/in-smb-gvfs.txt", "targ (1 ms)
- copy queue: PASS status done in 0.5 s (504 ms)
- archives: PASS open ['notes.txt', 'readme.md'] extract True (32 ms)
- hashes: PASS (4 ms)
- convert: PASS (24 ms)
- compress: PASS (58 ms)
- space scan: PASS 0.6 s (558 ms)
- duplicates: PASS 2 groups (1003 ms)
- organize: PASS 5 moves (18 ms)
- agent: PASS Create folder agent_made (6 ms)
- watch rule: PASS (14 ms)
- index and find: PASS 1.5 s (1513 ms)
- name unnamed files: PASS garden watering schedule.txt (2 ms)
- change from another computer: PASS seen after 30 s (30019 ms)
- fault: odd names: PASS (21 ms)
- fault: slow link: PASS list 15 ms, health 2 ms (1297 ms)
- fault: server stops during copy: PASS job error after 0 s, half file left: False, worst health 1 ms (3611 ms)
- fault: server gone while open: PASS HTTP 504: {"error":"The share does not answer. Check the connection, o after 0.0 s, health 1 ms (813 ms)
- fault: file over 4 GB: N/A run with --big (0 ms)

NFS: not run. A kernel NFS mount needs root.
