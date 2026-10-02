"""Atomic local JSON publication shared by specifications and record storage."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path


def write_json(path: Path, data: object, *, exclusive: bool = False) -> None:
    """Publish complete JSON through a flushed temporary file in the same directory.

    Args:
        path: Destination file in an existing directory.
        data: JSON-compatible value to serialize before touching the filesystem.
        exclusive: Fail if the destination already exists.
    """
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
    descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if not exclusive and path.exists():
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o7777)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
