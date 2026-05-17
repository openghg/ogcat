"""Default template-link replica materialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ogcat.models import CatalogRecord, MetadataDict
from ogcat.naming import render_storage_location
from ogcat.operation_helpers import directory_from_relative_path
from ogcat.replica_context import replica_template_context
from ogcat.replica_links import relative_symlink_target


@dataclass(frozen=True, slots=True)
class TemplateLinkReplicaMaterialization:
    """Materialized default template replica details.

    Args:
        primary_path: Local primary path the symlink points at.
        target_path: Local template replica symlink path.
        catalog_relative_path: Replica path relative to the catalog root.
        storage_relative_path: Replica path relative to the readable files root.
        resolved_directory: Rendered template replica directory.
        resolved_filename: Rendered template replica filename.
        naming_metadata: Naming metadata to merge onto the catalog record.
    """

    primary_path: Path
    target_path: Path
    catalog_relative_path: str
    storage_relative_path: str
    resolved_directory: str
    resolved_filename: str
    naming_metadata: MetadataDict


def materialize_template_link_replica(
    *,
    catalog_root: Path,
    files_root: Path,
    record: CatalogRecord,
    directory_template: str,
    filename_template: str,
    register_rollback: Callable[[Callable[[], None], str], object] | None = None,
) -> TemplateLinkReplicaMaterialization | None:
    """Create the default template symlink replica for a path-backed record.

    Args:
        catalog_root: Catalog root used for catalog-relative path metadata.
        files_root: Human-readable template replica root.
        record: Record whose primary path should be linked.
        directory_template: Directory template to render.
        filename_template: Filename template to render.
        register_rollback: Optional rollback registration callback accepting an
            action and a human-readable description.

    Returns:
        Materialized replica details, or ``None`` if the record is not
        path-backed.
    """
    primary_path = record.path()
    if primary_path is None:
        return None
    target, _catalog_relative_path, resolved_filename = render_storage_location(
        files_root=files_root,
        directory_template=directory_template,
        filename_template=filename_template,
        context=replica_template_context(record),
        exists=lambda candidate: candidate.exists() or candidate.is_symlink(),
    )
    catalog_relative_path = target.relative_to(catalog_root).as_posix()
    storage_relative_path = target.relative_to(files_root).as_posix()
    resolved_directory = directory_from_relative_path(storage_relative_path) or ""
    target.parent.mkdir(parents=True, exist_ok=True)
    if register_rollback is not None:
        register_rollback(
            lambda path=target: path.unlink(missing_ok=True),
            f"remove template symlink replica {target}",
        )
    target.symlink_to(relative_symlink_target(primary_path, link_path=target))

    naming_metadata = dict(record.naming_metadata)
    naming_metadata.update(
        {
            "template_replica_path": str(target),
            "template_replica_relative_path": catalog_relative_path,
            "template_replica_storage_relative_path": storage_relative_path,
            "template_resolved_directory": resolved_directory,
            "template_resolved_filename": resolved_filename,
        }
    )
    return TemplateLinkReplicaMaterialization(
        primary_path=primary_path,
        target_path=target,
        catalog_relative_path=catalog_relative_path,
        storage_relative_path=storage_relative_path,
        resolved_directory=resolved_directory,
        resolved_filename=resolved_filename,
        naming_metadata=naming_metadata,
    )


__all__ = [
    "TemplateLinkReplicaMaterialization",
    "materialize_template_link_replica",
]
