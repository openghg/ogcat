"""Shared local symlink helpers for replica materialization."""

from __future__ import annotations

import os
from pathlib import Path


def relative_symlink_target(source_path: Path, *, link_path: Path) -> str | Path:
    """Return a relative symlink target when the platform can represent one."""
    try:
        return os.path.relpath(source_path, start=link_path.parent)
    except ValueError:
        return source_path


def symlink_points_to(link_path: Path, source_path: Path) -> bool:
    """Return whether a symlink points at a source path."""
    try:
        link_target = Path(os.readlink(link_path))
    except OSError:
        return False
    if not link_target.is_absolute():
        link_target = (link_path.parent / link_target).resolve()
    else:
        link_target = link_target.resolve()
    return link_target == source_path.resolve()


__all__ = [
    "relative_symlink_target",
    "symlink_points_to",
]
