"""Keep the access time of a file when Raven reads it for itself.

Cold-file detection (section 9.8) uses the access time. When the indexer, a hash or a
sample test reads a file, the OS would mark it as opened. This puts the old time back.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def keep_atime(path: str) -> Iterator[None]:
    try:
        before = os.stat(path)
    except OSError:
        yield
        return
    try:
        yield
    finally:
        try:
            after = os.stat(path)
            # Only when nothing else changed the file in the meantime.
            if after.st_atime_ns != before.st_atime_ns and after.st_mtime_ns == before.st_mtime_ns:
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        except OSError:
            pass
