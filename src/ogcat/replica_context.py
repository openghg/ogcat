"""Template context helpers shared by replica planners and materializers."""

from __future__ import annotations

from pathlib import Path

from ogcat.models import CatalogRecord
from ogcat.naming import (
    RESERVED_TEMPLATE_FIELDS,
    build_naming_context,
    normalise_segment,
    split_name_and_suffixes,
)


def replica_template_context(record: CatalogRecord) -> dict[str, object]:
    """Build a template context from record metadata and locator fields."""
    context: dict[str, object] = {}
    context.update(record.derived_metadata)
    context.update(record.user_metadata)

    artifact_uuid = record.naming_metadata.get("artifact_uuid")
    record_id = "" if record.id is None else str(record.id)
    uuid_value = str(artifact_uuid or record_id)
    original_name = _record_original_name(record)
    naming_metadata = {
        key: value for key, value in record.user_metadata.items() if key not in RESERVED_TEMPLATE_FIELDS
    }
    context.update(
        build_naming_context(
            record_id=record_id,
            operation_id=uuid_value,
            original_path=Path(original_name),
            metadata=naming_metadata,
            date_added=record.time_added[:10],
        )
    )
    locator_path = record.path()
    locator_name = "" if locator_path is None else locator_path.name
    locator_stem, locator_suffix = split_name_and_suffixes(locator_name)

    context.update(
        {
            "id": record_id,
            "uuid": uuid_value,
            "artifact_uuid": uuid_value,
            "operation_id": uuid_value,
            "date_added": record.time_added[:10],
            "year_added": record.time_added[:4],
            "record_type": record.record_type,
            "storage_mode": record.storage_mode or "",
            "locator_kind": record.locator.kind,
            "locator_value": record.locator.value,
            "locator_filename": locator_name,
            "locator_stem": normalise_segment(locator_stem) if locator_stem else "",
            "locator_suffix": locator_suffix,
            "stored_relpath": record.stored_relpath or "",
            "path": "" if locator_path is None else str(locator_path),
        }
    )
    return context


def _record_original_name(record: CatalogRecord) -> str:
    """Return the best filename-like value available for a record."""
    if record.original_filename:
        return record.original_filename
    locator_path = record.path()
    if locator_path is not None:
        return locator_path.name
    locator_value = record.locator.value.rstrip("/")
    if locator_value:
        return locator_value.rsplit("/", 1)[-1]
    return "artifact"


__all__ = [
    "replica_template_context",
]
