"""SHA-256, the content identity of every blob (SPEC §3.2, §7.1)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import BinaryIO, Callable

CHUNK = 4 << 20


def sha256_file(path: str | Path, chunk: int = CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def copy_hashing(source: BinaryIO, dest: BinaryIO, *, chunk: int = CHUNK, throttle: Callable[[int], None] | None = None) -> tuple[str, int]:
    """Stream ``source`` into ``dest``, returning (sha256, bytes). ``throttle``
    is called with each block's size (bandwidth limits)."""
    digest = hashlib.sha256()
    total = 0
    while block := source.read(chunk):
        digest.update(block)
        dest.write(block)
        total += len(block)
        if throttle:
            throttle(len(block))
    dest.flush()
    try:
        os.fsync(dest.fileno())
    except (AttributeError, OSError, ValueError):
        pass
    return digest.hexdigest(), total
