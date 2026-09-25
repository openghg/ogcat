"""Internal helpers for writer-backed storage plans."""

from __future__ import annotations

from typing import cast

from ogcat.hooks import ArtifactWriter
from ogcat.models import ArtifactLocator
from ogcat.operation_helpers import adapter_name, directory_from_locator, filename_from_locator
from ogcat.storage import (
    StoragePlan,
    TargetKind,
    WriteMode,
    plan_storage,
)


def storage_plan_for_locator(
    locator: ArtifactLocator,
    *,
    writer: ArtifactWriter | None,
) -> StoragePlan:
    """Build a reference or writer-backed plan from a resolved locator."""
    return plan_storage(
        locator,
        target_kind="file" if writer is None else target_kind_from_writer(writer),
        write_mode="reference" if writer is None else write_mode_from_writer(writer),
        ogcat_owned=writer is not None,
        adapter=adapter_name(locator),
        storage_relative_path=locator.relative_path,
        resolved_directory=directory_from_locator(locator),
        resolved_filename=filename_from_locator(locator),
    )


def validate_writer_matches_storage_plan(writer: ArtifactWriter, plan: StoragePlan) -> None:
    """Raise when a writer declares target semantics that conflict with a plan."""
    declared_target_kind = getattr(writer, "target_kind", plan.target_kind)
    if declared_target_kind in {"file", "directory"} and declared_target_kind != plan.target_kind:
        raise ValueError(
            f"Artifact writer target_kind {declared_target_kind!r} does not match "
            f"storage plan target_kind {plan.target_kind!r}."
        )
    declared_write_mode = getattr(writer, "write_mode", plan.write_mode)
    if (
        declared_write_mode in {"copy", "move", "write", "reference"}
        and declared_write_mode != plan.write_mode
    ):
        raise ValueError(
            f"Artifact writer write_mode {declared_write_mode!r} does not match "
            f"storage plan write_mode {plan.write_mode!r}."
        )


def target_kind_from_writer(writer: ArtifactWriter) -> TargetKind:
    """Infer a storage target kind from a writer when it declares one."""
    target_kind = getattr(writer, "target_kind", "file")
    if target_kind in {"file", "directory"}:
        return cast(TargetKind, target_kind)
    return "file"


def write_mode_from_writer(writer: ArtifactWriter) -> WriteMode:
    """Infer a storage write mode from a writer when it declares one."""
    write_mode = getattr(writer, "write_mode", "write")
    if write_mode in {"copy", "move", "write", "reference"}:
        return cast(WriteMode, write_mode)
    return "write"


__all__ = [
    "storage_plan_for_locator",
    "target_kind_from_writer",
    "validate_writer_matches_storage_plan",
    "write_mode_from_writer",
]
