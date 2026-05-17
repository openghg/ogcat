"""Shared replica type aliases."""

from __future__ import annotations

from typing import Literal

ReplicaMode = Literal["symlink"]
ReplicaRole = Literal["template_link", "view_link"]

__all__ = [
    "ReplicaMode",
    "ReplicaRole",
]
