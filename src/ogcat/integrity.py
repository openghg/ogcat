"""Bounded inspection of registered local artifact paths."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path

from ogcat.models import ArtifactDescriptor, CatalogRecord, MetadataDict
from ogcat.replica_links import symlink_points_to


def check_records(records: Sequence[CatalogRecord]) -> list[MetadataDict]:
    """Inspect local paths and recorded view links in a record snapshot.

    Args:
        records: Records selected by the catalog's visibility policy.

    Returns:
        Issues with record_id, artifact_id, code, path, and message fields.
        An empty list only means no registered local path issues were observed.
        Contents, remote locators, purged artifacts, and unregistered files are
        not inspected.
    """
    issues: list[MetadataDict] = []
    for record in records:
        artifacts = {artifact.id: artifact for artifact in record.artifacts}
        for artifact in record.artifacts:
            if artifact.state == "purged" or artifact.locator is None:
                continue
            path = artifact.locator.as_path()
            if path is None:
                continue
            issue = _check_path(path, artifact, artifacts)
            if issue is not None:
                code, message = issue
                issues.append(
                    {
                        "record_id": record.id,
                        "artifact_id": artifact.id,
                        "code": code,
                        "path": str(path),
                        "message": message,
                    }
                )
    return issues


def _check_path(
    path: Path, artifact: ArtifactDescriptor, artifacts: dict[str, ArtifactDescriptor]
) -> tuple[str, str] | None:
    """Return the first observed availability or view-link issue for a path.

    Args:
        path: Registered local artifact path.
        artifact: Descriptor whose path is being inspected.
        artifacts: Descriptors in the same record, indexed by artifact id.

    Returns:
        An issue code and actionable message, or None if no issue was observed.
        Filesystem inspection failures are represented as unreadable_path issues.
    """
    try:
        try:
            link_stat = path.lstat()
        except FileNotFoundError:
            return "missing_path", "Registered path is missing; restore it or update the record."
        is_symlink = stat.S_ISLNK(link_stat.st_mode)
        try:
            target_stat = path.stat()
        except FileNotFoundError:
            code = "broken_symlink" if is_symlink else "missing_path"
            return code, "Registered path target is missing; restore the target or update the record."
        if artifact.relationship.get("kind") == "view_of":
            target_id = artifact.relationship.get("target_artifact_id")
            target = artifacts.get(target_id) if isinstance(target_id, str) else None
            target_path = None if target is None or target.locator is None else target.locator.as_path()
            if target is None or not is_symlink:
                return "incorrect_symlink", "Recorded view must be a symlink to its target artifact."
            if target_path is not None and not symlink_points_to(path, target_path):
                return "incorrect_symlink", f"Recorded view must point to {target_path}."
        mode = os.R_OK | (os.X_OK if stat.S_ISDIR(target_stat.st_mode) else 0)
        if not os.access(path, mode):
            return "unreadable_path", "Registered path is inaccessible; check filesystem permissions."
    except (OSError, RuntimeError) as exc:
        return "unreadable_path", f"Cannot inspect registered path: {exc}"
    return None
