"""Shared helpers for internal catalog operation planning.

These helpers are not public API, but they are intentionally shared across
internal modules. ``Catalog`` uses them while preparing add-operation inputs,
and operation runners use them while executing the lifecycle. Keeping them here
avoids importing private names from a concrete runner implementation and keeps
schema-aware normalization behavior in one place.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from ogcat.hooks import OperationContext
from ogcat.models import ArtifactLocator, MetadataDict, normalize_metadata
from ogcat.storage import StoragePlan


def normalize_metadata_for_schema(metadata: object, *, schema_name: str) -> MetadataDict:
    """Normalize user metadata with the existing schema-aware error prefix."""
    return normalize_metadata(
        metadata,
        field_name="metadata",
        label=f"Metadata for schema {schema_name}",
    )


def artifact_locator_from_context(context: OperationContext) -> ArtifactLocator:
    """Return the canonical locator after locator-resolution hooks run."""
    if not context.planned_locators:
        raise ValueError("resolve_artifact_locator hook removed the planned artifact locator.")
    return context.planned_locators[0]


def storage_plan_with_locator(
    plan: StoragePlan, locator: ArtifactLocator, *, storage_root: Path | None = None
) -> StoragePlan:
    """Adjust a storage plan to a hook-resolved canonical locator.

    Args:
        plan: Plan prepared before locator-resolution hooks.
        locator: Canonical target selected by the hooks.
        storage_root: Managed local root for storage-relative path metadata.
            Omit for general artifact plans, whose directory remains absolute.

    Returns:
        Original plan when the locator is unchanged, otherwise an adjusted
        plan preserving write policy and artifact identity.
    """
    if plan.locator == locator:
        return plan
    relative_path = locator.relative_path
    if storage_root is not None and locator.kind == "path":
        with suppress(ValueError):
            relative_path = Path(locator.value).relative_to(storage_root).as_posix()
    resolved_directory = directory_from_locator(locator)
    if storage_root is not None and relative_path is not None:
        resolved_directory = directory_from_relative_path(relative_path)
    return replace(
        plan,
        locator=locator,
        adapter=adapter_name(locator),
        storage_relative_path=relative_path,
        resolved_directory=resolved_directory,
        resolved_filename=filename_from_locator(locator),
    )


def naming_metadata_from_storage_plan(plan: StoragePlan) -> MetadataDict:
    """Build record naming metadata from storage planning outputs."""
    metadata: MetadataDict = {}
    if plan.artifact_uuid is not None:
        metadata["artifact_uuid"] = plan.artifact_uuid
    if plan.primary_location is not None:
        metadata["primary_location"] = plan.primary_location
    if plan.storage_relative_path is not None:
        metadata["storage_relative_path"] = plan.storage_relative_path
        metadata["primary_storage_relative_path"] = plan.storage_relative_path
    if plan.resolved_directory is not None:
        metadata["resolved_directory"] = plan.resolved_directory
        metadata["primary_resolved_directory"] = plan.resolved_directory
    if plan.resolved_filename is not None:
        metadata["resolved_filename"] = plan.resolved_filename
        metadata["primary_resolved_filename"] = plan.resolved_filename
    return metadata


def adapter_name(locator: ArtifactLocator) -> str | None:
    """Return the storage adapter name implied by a locator."""
    if locator.kind == "path":
        return "local"
    if locator.kind == "urlpath":
        return "fsspec"
    return None


def filename_from_locator(locator: ArtifactLocator) -> str:
    """Return the final filename-like component from a locator."""
    if locator.kind == "path":
        return Path(locator.value).name
    return locator.value.rstrip("/").rsplit("/", 1)[-1]


def directory_from_locator(locator: ArtifactLocator) -> str:
    """Return the directory-like component from a locator."""
    if locator.kind == "path":
        return Path(locator.value).parent.as_posix()
    return locator.value.rstrip("/").rsplit("/", 1)[0]


def directory_from_relative_path(relative_path: str | None) -> str | None:
    """Return the directory component from a storage-relative path."""
    if relative_path is None:
        return None
    directory = Path(relative_path).parent.as_posix()
    return "" if directory == "." else directory
