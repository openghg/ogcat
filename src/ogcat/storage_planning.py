"""Storage location planning helpers.

This module contains path and URL selection policy for catalog-managed
artifacts. It is intentionally separate from storage adapters, which perform
side effects against already-planned locators.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypeAlias

from ogcat.models import ArtifactLocator
from ogcat.naming import _split_name_and_suffixes, build_naming_context, render_storage_location
from ogcat.operation_helpers import directory_from_relative_path
from ogcat.storage import LocalStorageAdapter

PrimaryLocation: TypeAlias = Literal["uuid", "template"]
PlannedLocatorResult: TypeAlias = tuple[ArtifactLocator, str | None, str | None, str | None]


def _uuid_storage_path(
    *,
    catalog_root: Path,
    objects_root: Path,
    artifact_uuid: str,
    original_path: Path,
) -> tuple[Path, str, str]:
    """Return the local UUID primary path and relative path metadata.

    Args:
        catalog_root: Catalog root used to build catalog-relative locator
            metadata.
        objects_root: Root directory for UUID-managed object storage.
        artifact_uuid: Stable artifact identifier used in the target filename.
        original_path: Original source path used only to preserve naming
            suffixes.

    Returns:
        Target path, catalog-relative path, and objects-root-relative path.
    """
    storage_relative_path = _uuid_storage_relative_path(
        artifact_uuid=artifact_uuid,
        original_path=original_path,
    )
    target = objects_root / storage_relative_path
    catalog_relative_path = target.relative_to(catalog_root).as_posix()
    return target, catalog_relative_path, storage_relative_path


def _render_uuid_planned_locator(
    *,
    catalog_root: Path,
    objects_root: Path,
    storage_root: str | Path | None,
    artifact_uuid: str,
    original_path: Path,
) -> PlannedLocatorResult:
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
        Locator, storage-relative path, resolved directory, and resolved
        filename.
    """
    storage_relative_path = _uuid_storage_relative_path(
        artifact_uuid=artifact_uuid,
        original_path=original_path,
    )
    resolved_directory = directory_from_relative_path(storage_relative_path)
    resolved_filename = Path(storage_relative_path).name

    if storage_root is not None and _is_urlpath_root(storage_root):
        return (
            ArtifactLocator.from_urlpath(_join_urlpath(str(storage_root), storage_relative_path)),
            storage_relative_path,
            resolved_directory,
            resolved_filename,
        )

    target_root = objects_root if storage_root is None else Path(storage_root).expanduser().resolve()
    target = target_root / storage_relative_path
    relative_path = target.relative_to(catalog_root).as_posix() if storage_root is None else None
    return (
        ArtifactLocator.from_path(target, relative_path=relative_path),
        storage_relative_path,
        resolved_directory,
        resolved_filename,
    )


def _render_planned_locator(
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
) -> PlannedLocatorResult:
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
        Locator, storage-relative path, resolved directory, and resolved
        filename.
    """
    naming_source = source_path or Path("artifact")
    naming_context = build_naming_context(
        record_id=operation_id,
        operation_id=operation_id,
        original_path=naming_source,
        metadata=metadata,
        date_added=date_added,
    )
    artifact_uuid = operation_id
    naming_context["artifact_uuid"] = artifact_uuid
    if primary_location == "uuid":
        return _render_uuid_planned_locator(
            catalog_root=catalog_root,
            objects_root=objects_root,
            storage_root=storage_root,
            artifact_uuid=artifact_uuid,
            original_path=naming_source,
        )

    if storage_root is not None and _is_urlpath_root(storage_root):
        return _render_urlpath_template_locator(
            root_url=str(storage_root),
            directory_template=directory_template,
            filename_template=filename_template,
            naming_context=naming_context,
        )

    local_files_root = files_root if storage_root is None else Path(storage_root).expanduser().resolve()
    storage_adapter = LocalStorageAdapter()
    target, _catalog_relative_path, resolved_filename = render_storage_location(
        files_root=local_files_root,
        directory_template=directory_template,
        filename_template=filename_template,
        context=naming_context,
        exists=lambda candidate: storage_adapter.exists(ArtifactLocator.from_path(candidate)),
    )
    relative_path = target.relative_to(catalog_root).as_posix() if storage_root is None else None
    storage_relative_path = target.relative_to(local_files_root).as_posix()
    return (
        ArtifactLocator.from_path(target, relative_path=relative_path),
        storage_relative_path,
        directory_from_relative_path(storage_relative_path),
        resolved_filename,
    )


def _urlpath_exists_if_supported(urlpath: str) -> bool:
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


def _join_urlpath(root_url: str, relative_path: str) -> str:
    """Join an fsspec URL root and relative path without local path coercion."""
    return f"{root_url.rstrip('/')}/{relative_path.lstrip('/')}"


def _storage_relative_path_for_locator(locator: ArtifactLocator, *, storage_root: Path) -> str | None:
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
) -> PlannedLocatorResult:
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
        exists=lambda candidate: _urlpath_exists_if_supported(
            _join_urlpath(normalized_root_url, candidate.relative_to(fake_root).as_posix())
        ),
    )
    storage_relative_path = target.relative_to(fake_root).as_posix()
    return (
        ArtifactLocator.from_urlpath(_join_urlpath(normalized_root_url, storage_relative_path)),
        storage_relative_path,
        directory_from_relative_path(storage_relative_path),
        resolved_filename,
    )


def _uuid_storage_relative_path(*, artifact_uuid: str, original_path: Path) -> str:
    """Return the objects-root-relative path for a UUID primary artifact."""
    _stem, suffix = _split_name_and_suffixes(original_path.name)
    return f"{artifact_uuid[:2]}/{artifact_uuid}{suffix}"
