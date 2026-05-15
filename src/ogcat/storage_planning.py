"""Storage location planning helpers.

This module contains path and URL selection policy for catalog-managed
artifacts. It is intentionally separate from storage adapters, which perform
side effects against already-planned locators.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from ogcat.materialization import (
    MaterializationIntent,
    MaterializationPlan,
    MaterializationTarget,
)
from ogcat.models import ArtifactLocator
from ogcat.naming import build_naming_context, render_storage_location, split_name_and_suffixes
from ogcat.operation_helpers import (
    adapter_name,
    directory_from_locator,
    directory_from_relative_path,
    filename_from_locator,
)
from ogcat.storage import (
    ChecksumPolicy,
    LocalStorageAdapter,
    StoragePlan,
    TargetKind,
    WriteMode,
)

PrimaryLocation: TypeAlias = Literal["uuid", "template"]
StoragePrimaryLocation: TypeAlias = Literal["uuid", "template", "user_provided"]


@dataclass(frozen=True, slots=True)
class PrimaryStoragePlanningContext:
    """Inputs required to plan a primary artifact storage location.

    Args:
        catalog_root: Catalog root used for catalog-local relative paths.
        files_root: Catalog template-managed files root.
        objects_root: Catalog UUID-managed object root.
        operation_id: Operation id used as the planned artifact UUID.
        metadata: Normalized metadata available to naming templates.
        directory_template: Directory naming template from the record schema.
        filename_template: Filename naming template from the record schema.
        source_path: Optional source path used for naming context.
        storage_root: Optional external local root or fsspec URL root.
        date_added: ISO date string used by date-based templates.
        primary_location: Primary placement policy to render.
        locator: User-provided locator when ``primary_location`` is
            ``"user_provided"``.
    """

    catalog_root: Path
    files_root: Path
    objects_root: Path
    operation_id: str
    metadata: Mapping[str, object]
    directory_template: str
    filename_template: str
    source_path: Path | None
    storage_root: str | Path | None
    date_added: str
    primary_location: StoragePrimaryLocation
    locator: ArtifactLocator | None = None


@dataclass(frozen=True, slots=True)
class PrimaryStoragePlanResult:
    """Planned primary storage locator and derived metadata.

    Args:
        locator: Target artifact locator.
        storage_relative_path: Path relative to the relevant storage root.
        resolved_directory: Rendered storage directory, when available.
        resolved_filename: Rendered final path component, when available.
        artifact_uuid: UUID-style artifact storage identifier, when generated.
        primary_location: Primary placement policy used for the plan.
        storage_root: Local storage root used to recalculate path metadata when
            hooks replace the planned locator.
    """

    locator: ArtifactLocator
    storage_relative_path: str | None
    resolved_directory: str | None
    resolved_filename: str | None
    artifact_uuid: str | None
    primary_location: StoragePrimaryLocation
    storage_root: Path | None = None

    def to_storage_plan(
        self,
        *,
        locator: ArtifactLocator | None = None,
        target_kind: TargetKind = "file",
        write_mode: WriteMode = "reference",
        checksum: ChecksumPolicy = "none",
        ogcat_owned: bool = False,
        profile: str | None = None,
        adapter: str | None = None,
        time_added: str | None = None,
        artifact_uuid: str | None = None,
    ) -> StoragePlan:
        """Build a concrete :class:`~ogcat.storage.StoragePlan`.

        Args:
            locator: Optional hook-resolved canonical locator.
            target_kind: Whether the target is file-like or directory-like.
            write_mode: Intended materialisation mode.
            checksum: Checksum policy requested for the write.
            ogcat_owned: Whether ogcat should treat the target as managed.
            profile: Optional storage profile name or hint.
            adapter: Optional adapter identifier. When omitted, it is inferred
                from the planned locator.
            time_added: Optional timestamp used for the storage plan.
            artifact_uuid: Optional artifact UUID override for compatibility
                with operations that record the operation id separately from
                primary storage identity.

        Returns:
            Storage plan carrying the primary storage planning metadata.
        """
        intent = MaterializationIntent(
            writer=None,
            target_kind=target_kind,
            write_mode=write_mode,
            ogcat_owned=ogcat_owned,
        )
        return MaterializationPlan(
            primary_target=self.to_materialization_target(
                locator=locator,
                target_kind=target_kind,
                adapter=adapter,
                artifact_uuid=artifact_uuid,
            ),
            intent=intent,
        ).to_storage_plan(
            checksum=checksum,
            profile=profile,
            time_added=time_added,
        )

    def to_materialization_target(
        self,
        *,
        locator: ArtifactLocator | None = None,
        target_kind: TargetKind = "file",
        adapter: str | None = None,
        artifact_uuid: str | None = None,
    ) -> MaterializationTarget:
        """Build the resolved primary target for a materialization plan.

        Args:
            locator: Optional hook-resolved canonical locator.
            target_kind: Whether the target is file-like or directory-like.
            adapter: Optional adapter identifier. When omitted, it is inferred
                from the planned locator.
            artifact_uuid: Optional artifact UUID override for compatibility
                with operations that record the operation id separately from
                primary storage identity.

        Returns:
            Materialization target carrying primary storage planning metadata.
        """
        canonical_locator = self.locator if locator is None else locator
        if canonical_locator == self.locator:
            storage_relative_path = self.storage_relative_path
            resolved_directory = self.resolved_directory
            resolved_filename = self.resolved_filename
        else:
            storage_relative_path = self._storage_relative_path_for(canonical_locator)
            resolved_directory = (
                directory_from_locator(canonical_locator)
                if storage_relative_path is None
                else directory_from_relative_path(storage_relative_path)
            )
            resolved_filename = filename_from_locator(canonical_locator)
        return MaterializationTarget(
            locator=canonical_locator,
            target_kind=target_kind,
            adapter=adapter_name(canonical_locator) if adapter is None else adapter,
            storage_relative_path=storage_relative_path,
            resolved_directory=resolved_directory,
            resolved_filename=resolved_filename,
            artifact_uuid=self.artifact_uuid if artifact_uuid is None else artifact_uuid,
            primary_location=self.primary_location,
        )

    def naming_metadata(self) -> dict[str, object]:
        """Build record naming metadata from the planned primary storage."""
        metadata: dict[str, object] = {"primary_location": self.primary_location}
        if self.artifact_uuid is not None:
            metadata["artifact_uuid"] = self.artifact_uuid
        if self.storage_relative_path is not None:
            metadata["storage_relative_path"] = self.storage_relative_path
            metadata["primary_storage_relative_path"] = self.storage_relative_path
        if self.resolved_directory is not None:
            metadata["resolved_directory"] = self.resolved_directory
            metadata["primary_resolved_directory"] = self.resolved_directory
        if self.resolved_filename is not None:
            metadata["resolved_filename"] = self.resolved_filename
            metadata["primary_resolved_filename"] = self.resolved_filename
        return metadata

    def _storage_relative_path_for(self, locator: ArtifactLocator) -> str | None:
        """Return storage-relative metadata for a hook-resolved locator."""
        if self.storage_root is None:
            return locator.relative_path
        return storage_relative_path_for_locator(locator, storage_root=self.storage_root)


@dataclass(frozen=True, slots=True)
class PlannedLocator:
    """Rendered locator and path metadata for a planned storage target.

    Args:
        locator: Target artifact locator.
        storage_relative_path: Path relative to the relevant storage root.
        resolved_directory: Rendered storage directory, when available.
        resolved_filename: Rendered final path component, when available.
    """

    locator: ArtifactLocator
    storage_relative_path: str | None
    resolved_directory: str | None
    resolved_filename: str | None


@dataclass(frozen=True, slots=True)
class UuidStoragePath:
    """Local UUID storage path and relative path metadata.

    Args:
        target: Local UUID target path.
        catalog_relative_path: Target path relative to the catalog root.
        storage_relative_path: Target path relative to the objects root.
    """

    target: Path
    catalog_relative_path: str
    storage_relative_path: str


def uuid_storage_path(
    *,
    catalog_root: Path,
    objects_root: Path,
    artifact_uuid: str,
    original_path: Path,
) -> UuidStoragePath:
    """Return the local UUID primary path and relative path metadata.

    Args:
        catalog_root: Catalog root used to build catalog-relative locator
            metadata.
        objects_root: Root directory for UUID-managed object storage.
        artifact_uuid: Stable artifact identifier used in the target filename.
        original_path: Original source path used only to preserve naming
            suffixes.

    Returns:
        Local UUID storage path and relative path metadata.
    """
    storage_relative_path = _uuid_storage_relative_path(
        artifact_uuid=artifact_uuid,
        original_path=original_path,
    )
    target = objects_root / storage_relative_path
    catalog_relative_path = target.relative_to(catalog_root).as_posix()
    return UuidStoragePath(
        target=target,
        catalog_relative_path=catalog_relative_path,
        storage_relative_path=storage_relative_path,
    )


def render_uuid_planned_locator(
    *,
    catalog_root: Path,
    objects_root: Path,
    storage_root: str | Path | None,
    artifact_uuid: str,
    original_path: Path,
) -> PlannedLocator:
    """Render a UUID primary locator for local or fsspec storage roots.

    Args:
        catalog_root: Catalog root used for catalog-local relative paths.
        objects_root: Catalog object-storage root used when no explicit storage
            root is supplied.
        storage_root: Optional external local root or fsspec URL root.
        artifact_uuid: Stable artifact identifier used in the target filename.
        original_path: Original source path used only to preserve naming
            suffixes.

    Returns:
        Rendered locator and path metadata.
    """
    storage_relative_path = _uuid_storage_relative_path(
        artifact_uuid=artifact_uuid,
        original_path=original_path,
    )
    resolved_directory = directory_from_relative_path(storage_relative_path)
    resolved_filename = Path(storage_relative_path).name

    if storage_root is not None and _is_urlpath_root(storage_root):
        return PlannedLocator(
            locator=ArtifactLocator.from_urlpath(join_urlpath(str(storage_root), storage_relative_path)),
            storage_relative_path=storage_relative_path,
            resolved_directory=resolved_directory,
            resolved_filename=resolved_filename,
        )

    target_root = objects_root if storage_root is None else Path(storage_root).expanduser().resolve()
    target = target_root / storage_relative_path
    relative_path = target.relative_to(catalog_root).as_posix() if storage_root is None else None
    return PlannedLocator(
        locator=ArtifactLocator.from_path(target, relative_path=relative_path),
        storage_relative_path=storage_relative_path,
        resolved_directory=resolved_directory,
        resolved_filename=resolved_filename,
    )


def plan_primary_storage(context: PrimaryStoragePlanningContext) -> PrimaryStoragePlanResult:
    """Plan the primary storage location for a catalog artifact.

    Args:
        context: Storage planning inputs including roots, naming templates,
            metadata, and primary placement policy.

    Returns:
        Planned primary storage locator and metadata suitable for constructing
        a storage plan or record naming metadata.

    Raises:
        ValueError: If ``primary_location`` is ``"user_provided"`` without a
            locator, or an unsupported primary placement policy is supplied.
    """
    if context.primary_location == "user_provided":
        if context.locator is None:
            raise ValueError("locator is required when primary_location is 'user_provided'.")
        return PrimaryStoragePlanResult(
            locator=context.locator,
            storage_relative_path=context.locator.relative_path,
            resolved_directory=directory_from_locator(context.locator),
            resolved_filename=filename_from_locator(context.locator),
            artifact_uuid=None,
            primary_location="user_provided",
            storage_root=None,
        )

    if context.locator is not None:
        raise ValueError("locator can only be provided when primary_location is 'user_provided'.")

    naming_source = context.source_path or Path("artifact")
    artifact_uuid = context.operation_id
    naming_context = build_naming_context(
        record_id=context.operation_id,
        operation_id=context.operation_id,
        original_path=naming_source,
        metadata=context.metadata,
        date_added=context.date_added,
    )
    naming_context["artifact_uuid"] = artifact_uuid
    if context.primary_location == "uuid":
        planned = render_uuid_planned_locator(
            catalog_root=context.catalog_root,
            objects_root=context.objects_root,
            storage_root=context.storage_root,
            artifact_uuid=artifact_uuid,
            original_path=naming_source,
        )
        return _primary_result_from_planned_locator(
            planned,
            artifact_uuid=artifact_uuid,
            primary_location="uuid",
            storage_root=_primary_storage_root(context),
        )

    if context.primary_location != "template":
        raise ValueError("primary_location must be 'uuid', 'template', or 'user_provided'.")
    if context.storage_root is not None and _is_urlpath_root(context.storage_root):
        planned = _render_urlpath_template_locator(
            root_url=str(context.storage_root),
            directory_template=context.directory_template,
            filename_template=context.filename_template,
            naming_context=naming_context,
        )
        return _primary_result_from_planned_locator(
            planned,
            artifact_uuid=None,
            primary_location="template",
            storage_root=_primary_storage_root(context),
        )

    local_files_root = (
        context.files_root
        if context.storage_root is None
        else Path(context.storage_root).expanduser().resolve()
    )
    storage_adapter = LocalStorageAdapter()
    target, _catalog_relative_path, resolved_filename = render_storage_location(
        files_root=local_files_root,
        directory_template=context.directory_template,
        filename_template=context.filename_template,
        context=naming_context,
        exists=lambda candidate: storage_adapter.exists(ArtifactLocator.from_path(candidate)),
    )
    relative_path = (
        target.relative_to(context.catalog_root).as_posix() if context.storage_root is None else None
    )
    storage_relative_path = target.relative_to(local_files_root).as_posix()
    return PrimaryStoragePlanResult(
        locator=ArtifactLocator.from_path(target, relative_path=relative_path),
        storage_relative_path=storage_relative_path,
        resolved_directory=directory_from_relative_path(storage_relative_path),
        resolved_filename=resolved_filename,
        artifact_uuid=None,
        primary_location="template",
        storage_root=_primary_storage_root(context),
    )


def render_planned_locator(
    *,
    catalog_root: Path,
    files_root: Path,
    objects_root: Path,
    operation_id: str,
    metadata: Mapping[str, object],
    directory_template: str,
    filename_template: str,
    source_path: Path | None,
    storage_root: str | Path | None,
    date_added: str,
    primary_location: PrimaryLocation,
) -> PlannedLocator:
    """Render schema naming templates into a local or fsspec target locator.

    Args:
        catalog_root: Catalog root used for catalog-local relative paths.
        files_root: Catalog template-managed files root.
        objects_root: Catalog UUID-managed object root.
        operation_id: Operation id used as the planned artifact UUID.
        metadata: Normalized metadata available to naming templates.
        directory_template: Directory naming template from the record schema.
        filename_template: Filename naming template from the record schema.
        source_path: Optional source path used for naming context.
        storage_root: Optional external local root or fsspec URL root.
        date_added: ISO date string used by date-based templates.
        primary_location: Primary placement policy to render.

    Returns:
        Rendered locator and path metadata.
    """
    planned = plan_primary_storage(
        PrimaryStoragePlanningContext(
            catalog_root=catalog_root,
            files_root=files_root,
            objects_root=objects_root,
            operation_id=operation_id,
            metadata=metadata,
            directory_template=directory_template,
            filename_template=filename_template,
            source_path=source_path,
            storage_root=storage_root,
            date_added=date_added,
            primary_location=primary_location,
        )
    )
    return PlannedLocator(
        locator=planned.locator,
        storage_relative_path=planned.storage_relative_path,
        resolved_directory=planned.resolved_directory,
        resolved_filename=planned.resolved_filename,
    )


def urlpath_exists_if_supported(urlpath: str) -> bool:
    """Return whether a URL path exists when fsspec is installed.

    Args:
        urlpath: URL path to check.

    Returns:
        ``True`` when the fsspec target exists. Returns ``False`` when fsspec or
        a protocol-specific dependency is not installed so planning can remain a
        dry-run without optional storage dependencies.
    """
    if importlib.util.find_spec("fsspec") is None:
        return False
    from ogcat.storage import adapter_for_locator

    locator = ArtifactLocator.from_urlpath(urlpath)
    try:
        return adapter_for_locator(locator).exists(locator)
    except ImportError:
        return False


def _is_urlpath_root(value: str | Path) -> bool:
    """Return whether a storage root should be treated as an fsspec URL."""
    return isinstance(value, str) and "://" in value


def join_urlpath(root_url: str, relative_path: str) -> str:
    """Join an fsspec URL root and relative path without local path coercion."""
    return f"{root_url.rstrip('/')}/{relative_path.lstrip('/')}"


def storage_relative_path_for_locator(locator: ArtifactLocator, *, storage_root: Path) -> str | None:
    """Return a storage-root-relative path for a locator when available.

    Args:
        locator: Locator to inspect.
        storage_root: Local storage root used for path-backed locators.

    Returns:
        Storage-root-relative path, existing locator relative path, or ``None``
        when no relative storage metadata is available.
    """
    if locator.kind == "path":
        locator_path = Path(locator.value)
        try:
            return locator_path.relative_to(storage_root).as_posix()
        except ValueError:
            return locator.relative_path
    return locator.relative_path


def _render_urlpath_template_locator(
    *,
    root_url: str,
    directory_template: str,
    filename_template: str,
    naming_context: dict[str, object],
) -> PlannedLocator:
    """Render a template-managed fsspec URL-path locator.

    Args:
        root_url: URL-path root under which the rendered path should live.
        directory_template: Directory naming template from the record schema.
        filename_template: Filename naming template from the record schema.
        naming_context: Precomputed naming context used by the templates.

    Returns:
        Locator, storage-relative path, resolved directory, and resolved
        filename.
    """
    normalized_root_url = root_url.rstrip("/")
    fake_root = Path("/__ogcat_storage__")

    target, _rel_path, resolved_filename = render_storage_location(
        files_root=fake_root,
        directory_template=directory_template,
        filename_template=filename_template,
        context=naming_context,
        exists=lambda candidate: urlpath_exists_if_supported(
            join_urlpath(normalized_root_url, candidate.relative_to(fake_root).as_posix())
        ),
    )
    storage_relative_path = target.relative_to(fake_root).as_posix()
    return PlannedLocator(
        locator=ArtifactLocator.from_urlpath(join_urlpath(normalized_root_url, storage_relative_path)),
        storage_relative_path=storage_relative_path,
        resolved_directory=directory_from_relative_path(storage_relative_path),
        resolved_filename=resolved_filename,
    )


def _primary_result_from_planned_locator(
    planned: PlannedLocator,
    *,
    artifact_uuid: str | None,
    primary_location: StoragePrimaryLocation,
    storage_root: Path | None,
) -> PrimaryStoragePlanResult:
    """Return a primary storage planning result from locator metadata."""
    return PrimaryStoragePlanResult(
        locator=planned.locator,
        storage_relative_path=planned.storage_relative_path,
        resolved_directory=planned.resolved_directory,
        resolved_filename=planned.resolved_filename,
        artifact_uuid=artifact_uuid,
        primary_location=primary_location,
        storage_root=storage_root,
    )


def _primary_storage_root(context: PrimaryStoragePlanningContext) -> Path | None:
    """Return the local root used for storage-relative metadata."""
    if context.storage_root is not None:
        if _is_urlpath_root(context.storage_root):
            return None
        return Path(context.storage_root).expanduser().resolve()
    if context.primary_location == "uuid":
        return context.objects_root
    if context.primary_location == "template":
        return context.files_root
    return None


def _uuid_storage_relative_path(*, artifact_uuid: str, original_path: Path) -> str:
    """Return the objects-root-relative path for a UUID primary artifact."""
    _stem, suffix = split_name_and_suffixes(original_path.name)
    return f"{artifact_uuid[:2]}/{artifact_uuid}{suffix}"


__all__ = [
    "PlannedLocator",
    "PrimaryLocation",
    "PrimaryStoragePlanningContext",
    "PrimaryStoragePlanResult",
    "StoragePrimaryLocation",
    "UuidStoragePath",
    "join_urlpath",
    "plan_primary_storage",
    "render_planned_locator",
    "render_uuid_planned_locator",
    "storage_relative_path_for_locator",
    "urlpath_exists_if_supported",
    "uuid_storage_path",
]
