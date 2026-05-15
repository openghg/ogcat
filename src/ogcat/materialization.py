"""Internal artifact materialization planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ogcat.hooks import ArtifactWriter
from ogcat.models import ArtifactLocator
from ogcat.operation_helpers import adapter_name, directory_from_locator, filename_from_locator
from ogcat.storage import (
    ChecksumPolicy,
    StoragePlan,
    StoragePrimaryLocation,
    TargetKind,
    WriteMode,
    plan_storage,
)


@dataclass(frozen=True, slots=True)
class MaterializationIntent:
    """Operation intent for artifact materialization.

    Args:
        writer: Optional writer used to materialize artifact data.
        target_kind: Whether the materialized target is file-like or
            directory-like.
        write_mode: How the target is materialized, or ``"reference"`` when no
            write should occur.
        ogcat_owned: Whether ogcat should treat the materialized target as
            owned for storage-plan metadata.
    """

    writer: ArtifactWriter | None
    target_kind: TargetKind
    write_mode: WriteMode
    ogcat_owned: bool


@dataclass(frozen=True, slots=True)
class MaterializationTarget:
    """Resolved primary target for artifact materialization.

    Args:
        locator: Canonical artifact locator to store on the record.
        target_kind: Whether the target is file-like or directory-like.
        adapter: Optional storage adapter identifier.
        storage_relative_path: Optional target path relative to the relevant
            storage root.
        resolved_directory: Optional rendered directory metadata.
        resolved_filename: Optional rendered final path component.
        artifact_uuid: Optional UUID-style primary storage identifier.
        primary_location: Optional primary placement policy used for the plan.
    """

    locator: ArtifactLocator
    target_kind: TargetKind
    adapter: str | None = None
    storage_relative_path: str | None = None
    resolved_directory: str | None = None
    resolved_filename: str | None = None
    artifact_uuid: str | None = None
    primary_location: StoragePrimaryLocation | None = None


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    """Resolved primary target paired with materialization intent."""

    primary_target: MaterializationTarget
    intent: MaterializationIntent

    def to_storage_plan(
        self,
        *,
        checksum: ChecksumPolicy = "none",
        profile: str | None = None,
        time_added: str | None = None,
    ) -> StoragePlan:
        """Build the concrete storage plan for the primary target."""
        target = self.primary_target
        return plan_storage(
            target.locator,
            target_kind=target.target_kind,
            write_mode=self.intent.write_mode,
            checksum=checksum,
            ogcat_owned=self.intent.ogcat_owned,
            profile=profile,
            adapter=target.adapter,
            time_added=time_added,
            storage_relative_path=target.storage_relative_path,
            resolved_directory=target.resolved_directory,
            resolved_filename=target.resolved_filename,
            artifact_uuid=target.artifact_uuid,
            primary_location=target.primary_location,
        )


def reference_intent() -> MaterializationIntent:
    """Return the materialization intent for record-only references."""
    return MaterializationIntent(
        writer=None,
        target_kind="file",
        write_mode="reference",
        ogcat_owned=False,
    )


def writer_intent(writer: ArtifactWriter) -> MaterializationIntent:
    """Return the materialization intent declared by a writer."""
    return MaterializationIntent(
        writer=writer,
        target_kind=target_kind_from_writer(writer),
        write_mode=write_mode_from_writer(writer),
        ogcat_owned=True,
    )


def storage_plan_intent(
    plan: StoragePlan,
    *,
    writer: ArtifactWriter | None = None,
) -> MaterializationIntent:
    """Return materialization intent with an explicit storage plan as authority."""
    return MaterializationIntent(
        writer=None if plan.write_mode == "reference" else writer,
        target_kind=plan.target_kind,
        write_mode=plan.write_mode,
        ogcat_owned=plan.ogcat_owned,
    )


def target_from_locator(locator: ArtifactLocator, *, target_kind: TargetKind) -> MaterializationTarget:
    """Build a materialization target directly from a canonical locator."""
    return MaterializationTarget(
        locator=locator,
        target_kind=target_kind,
        adapter=adapter_name(locator),
        storage_relative_path=locator.relative_path,
        resolved_directory=directory_from_locator(locator),
        resolved_filename=filename_from_locator(locator),
    )


def materialization_plan_from_locator(
    locator: ArtifactLocator,
    *,
    intent: MaterializationIntent,
) -> MaterializationPlan:
    """Build a materialization plan directly from a canonical locator."""
    return MaterializationPlan(
        primary_target=target_from_locator(locator, target_kind=intent.target_kind),
        intent=intent,
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
    "MaterializationIntent",
    "MaterializationPlan",
    "MaterializationTarget",
    "materialization_plan_from_locator",
    "reference_intent",
    "storage_plan_intent",
    "target_from_locator",
    "target_kind_from_writer",
    "validate_writer_matches_storage_plan",
    "write_mode_from_writer",
    "writer_intent",
]
