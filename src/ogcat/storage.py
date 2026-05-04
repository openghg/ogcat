"""Storage planning and lightweight adapter primitives.

This module keeps storage decisions explicit without turning ``ogcat`` into a
domain storage framework.  Plans describe where an artifact should live and how
it should be materialised; :class:`ogcat.hooks.ArtifactWriter` implementations
perform any side effects.
"""

from __future__ import annotations

import importlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal, Protocol

from ogcat.models import ArtifactLocator

TargetKind = Literal["file", "directory"]
WriteMode = Literal["copy", "move", "write", "reference"]
ChecksumPolicy = Literal["none"]


@dataclass(frozen=True, slots=True)
class StoragePlan:
    """Concrete storage decision for a catalog operation.

    Args:
        locator: Canonical artifact locator that should be stored on the record.
        target_kind: Whether the target is a file-like or directory-like
            artifact.
        write_mode: How data should be materialised, or ``"reference"`` for
            record-only external artifacts.
        checksum: Checksum policy requested for the write.
        ogcat_owned: Whether ``ogcat`` is responsible for cleanup on rollback.
        profile: Optional storage profile name or hint.
        adapter: Optional adapter identifier, such as ``"local"`` or
            ``"fsspec"``.
        time_added: Optional timestamp used when rendering date-based storage
            templates.
        storage_relative_path: Optional target path relative to the configured
            storage root.
        resolved_directory: Optional rendered directory path for template-based
            storage.
        resolved_filename: Optional rendered filename or final path component.
    """

    locator: ArtifactLocator
    target_kind: TargetKind = "file"
    write_mode: WriteMode = "reference"
    checksum: ChecksumPolicy = "none"
    ogcat_owned: bool = False
    profile: str | None = None
    adapter: str | None = None
    time_added: str | None = None
    storage_relative_path: str | None = None
    resolved_directory: str | None = None
    resolved_filename: str | None = None


class StorageAdapter(Protocol):
    """Minimal storage operations needed by plans and bundled writers."""

    def exists(self, locator: ArtifactLocator) -> bool:
        """Return whether a locator currently exists."""
        ...

    def open(self, locator: ArtifactLocator, mode: str = "rb") -> IO[bytes]:
        """Open a locator for binary file I/O."""
        ...

    def copy_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Copy a local source path to the target locator."""
        ...

    def move_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Move a local source path to the target locator."""
        ...

    def mkdir(self, locator: ArtifactLocator) -> None:
        """Create a directory-like target."""
        ...

    def mkdir_parent(self, locator: ArtifactLocator) -> None:
        """Create the parent directory for a file-like target."""
        ...

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a file-like or directory-like locator."""
        ...


class _RollbackRegistrar(Protocol):
    """Callable shape accepted by writer rollback helpers."""

    def __call__(self, action: Callable[[], None], *, description: str | None = None) -> object:
        """Register a rollback action with an optional description."""
        ...


@dataclass(frozen=True, slots=True)
class LocalStorageAdapter:
    """Storage adapter for local path locators."""

    def exists(self, locator: ArtifactLocator) -> bool:
        """Return whether a local path-backed locator exists."""
        return require_local_path(locator).exists()

    def open(self, locator: ArtifactLocator, mode: str = "rb") -> IO[bytes]:
        """Open a local path-backed locator."""
        return require_local_path(locator).open(mode)

    def copy_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Copy a local source path to a local target."""
        self.mkdir_parent(target)
        target_path = require_local_path(target)
        shutil.copy2(source, target_path)

    def move_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Move a local source path to a local target."""
        self.mkdir_parent(target)
        target_path = require_local_path(target)
        shutil.move(str(source), str(target_path))

    def mkdir(self, locator: ArtifactLocator) -> None:
        """Create a local directory target."""
        require_local_path(locator).mkdir(parents=True, exist_ok=False)

    def mkdir_parent(self, locator: ArtifactLocator) -> None:
        """Create the parent directory for a local file target."""
        require_local_path(locator).parent.mkdir(parents=True, exist_ok=True)

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a local file or directory target."""
        path = require_local_path(locator)
        if target_kind == "directory":
            shutil.rmtree(path, ignore_errors=True)
            return
        path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FsspecStorageAdapter:
    """Storage adapter for fsspec-addressable ``urlpath`` locators."""

    storage_options: dict[str, object] | None = None

    def exists(self, locator: ArtifactLocator) -> bool:
        """Return whether a fsspec URL exists."""
        fs, path = self._filesystem_and_path(locator)
        return bool(fs.exists(path))

    def open(self, locator: ArtifactLocator, mode: str = "rb") -> IO[bytes]:
        """Open a fsspec URL."""
        fsspec = _load_fsspec()
        return fsspec.open(locator.value, mode=mode, **self._storage_options()).open()

    def copy_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Copy a local file to a fsspec target."""
        fs, path = self._filesystem_and_path(target)
        self.mkdir_parent(target)
        fs.put_file(str(source), path)

    def move_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Move a local file to a fsspec target."""
        self.copy_from_path(source, target)
        source.unlink()

    def mkdir(self, locator: ArtifactLocator) -> None:
        """Create a fsspec directory target."""
        fs, path = self._filesystem_and_path(locator)
        fs.makedirs(path, exist_ok=False)

    def mkdir_parent(self, locator: ArtifactLocator) -> None:
        """Create the parent directory for a fsspec target."""
        fs, path = self._filesystem_and_path(locator)
        parent = str(Path(path).parent)
        if parent not in {"", "."}:
            fs.makedirs(parent, exist_ok=True)

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a fsspec target."""
        fs, path = self._filesystem_and_path(locator)
        if fs.exists(path):
            fs.rm(path, recursive=target_kind == "directory")

    def _filesystem_and_path(self, locator: ArtifactLocator) -> tuple[Any, str]:
        """Return the fsspec filesystem and adapter path for a locator."""
        if locator.kind != "urlpath":
            raise ValueError(f"fsspec adapter requires locator kind 'urlpath', got {locator.kind!r}")
        fsspec = _load_fsspec()
        fs, path = fsspec.core.url_to_fs(locator.value, **self._storage_options())
        return fs, path

    def _storage_options(self) -> dict[str, object]:
        """Return a defensive copy of configured storage options."""
        return {} if self.storage_options is None else dict(self.storage_options)


def adapter_for_locator(locator: ArtifactLocator) -> StorageAdapter:
    """Return the storage adapter that can interpret a locator.

    Args:
        locator: Artifact locator to inspect.

    Returns:
        Storage adapter for local path or fsspec URL-path locators.

    Raises:
        ValueError: If ogcat does not provide a storage adapter for the locator
            kind.
    """
    if locator.kind == "path":
        return LocalStorageAdapter()
    if locator.kind == "urlpath":
        return FsspecStorageAdapter()
    raise ValueError(f"No storage adapter for locator kind {locator.kind!r}")


def plan_storage(
    locator: ArtifactLocator,
    *,
    target_kind: TargetKind = "file",
    write_mode: WriteMode = "reference",
    checksum: ChecksumPolicy = "none",
    ogcat_owned: bool = False,
    profile: str | None = None,
    adapter: str | None = None,
    time_added: str | None = None,
    storage_relative_path: str | None = None,
    resolved_directory: str | None = None,
    resolved_filename: str | None = None,
) -> StoragePlan:
    """Build a storage plan from already-resolved storage decisions.

    Args:
        locator: Target locator that should be recorded for the artifact.
        target_kind: Whether the target is file-like or directory-like.
        write_mode: Intended materialisation mode.
        checksum: Checksum policy requested for the write.
        ogcat_owned: Whether ogcat should treat the target as managed.
        profile: Optional storage profile name or hint.
        adapter: Optional adapter identifier, such as ``"local"`` or
            ``"fsspec"``.
        time_added: Optional timestamp used when rendering date-based storage
            templates.
        storage_relative_path: Optional target path relative to the configured
            storage root.
        resolved_directory: Optional rendered directory path for template-based
            storage.
        resolved_filename: Optional rendered filename or final path component.

    Returns:
        Storage plan describing the target and intended write.
    """
    return StoragePlan(
        locator=locator,
        target_kind=target_kind,
        write_mode=write_mode,
        checksum=checksum,
        ogcat_owned=ogcat_owned,
        profile=profile,
        adapter=adapter,
        time_added=time_added,
        storage_relative_path=storage_relative_path,
        resolved_directory=resolved_directory,
        resolved_filename=resolved_filename,
    )


def require_storage_target(locator: ArtifactLocator) -> StorageAdapter:
    """Return an adapter for a filesystem-like target locator.

    Args:
        locator: Artifact locator that should be writable through a storage
            adapter.

    Returns:
        Storage adapter that can operate on the locator.

    Raises:
        ValueError: If the locator kind is not filesystem-like.
    """
    return adapter_for_locator(locator)


def ensure_target_absent(
    locator: ArtifactLocator,
    *,
    adapter: StorageAdapter | None = None,
) -> None:
    """Raise if a target already exists.

    Args:
        locator: Target locator to check.
        adapter: Optional pre-resolved storage adapter.

    Raises:
        FileExistsError: If the target exists.
        ValueError: If no adapter can interpret the locator.
    """
    storage = require_storage_target(locator) if adapter is None else adapter
    if storage.exists(locator):
        raise FileExistsError(f"target already exists: {locator.value}")


def ensure_parent_directory(
    locator: ArtifactLocator,
    *,
    adapter: StorageAdapter | None = None,
) -> None:
    """Create parent directories for a file-like target.

    Args:
        locator: File-like target locator whose parent should exist.
        adapter: Optional pre-resolved storage adapter.

    Raises:
        ValueError: If no adapter can interpret the locator.
    """
    storage = require_storage_target(locator) if adapter is None else adapter
    storage.mkdir_parent(locator)


def create_directory_target(
    locator: ArtifactLocator,
    *,
    adapter: StorageAdapter | None = None,
) -> None:
    """Create an empty directory target after checking it does not exist.

    Args:
        locator: Directory-like target locator to create.
        adapter: Optional pre-resolved storage adapter.

    Raises:
        FileExistsError: If the directory target already exists.
        ValueError: If no adapter can interpret the locator.
    """
    storage = require_storage_target(locator) if adapter is None else adapter
    ensure_target_absent(locator, adapter=storage)
    storage.mkdir(locator)


def remove_target(
    locator: ArtifactLocator,
    *,
    target_kind: TargetKind = "file",
    adapter: StorageAdapter | None = None,
) -> None:
    """Remove a file-like or directory-like target.

    Args:
        locator: Target locator to remove.
        target_kind: Whether to remove a file-like or directory-like target.
        adapter: Optional pre-resolved storage adapter.

    Raises:
        ValueError: If no adapter can interpret the locator.
    """
    storage = require_storage_target(locator) if adapter is None else adapter
    storage.remove(locator, target_kind=target_kind)


def register_remove_on_rollback(
    rollback: _RollbackRegistrar,
    locator: ArtifactLocator,
    *,
    target_kind: TargetKind = "file",
    adapter: StorageAdapter | None = None,
    description: str | None = None,
) -> None:
    """Register target removal with a writer's rollback callback.

    Args:
        rollback: Rollback registration callback, such as
            :meth:`ogcat.hooks.OperationContext.rollback`.
        locator: Target locator to remove if rollback runs.
        target_kind: Whether the target is file-like or directory-like.
        adapter: Optional pre-resolved storage adapter.
        description: Optional human-readable rollback description.

    Raises:
        ValueError: If no adapter can interpret the locator.
    """
    storage = require_storage_target(locator) if adapter is None else adapter
    rollback(
        lambda: storage.remove(locator, target_kind=target_kind),
        description=description or f"remove written {target_kind} artifact {locator.value}",
    )


def require_local_path(locator: ArtifactLocator) -> Path:
    """Return a local path for a path-backed locator.

    Args:
        locator: Artifact locator expected to contain a local path.

    Returns:
        Local filesystem path represented by the locator.

    Raises:
        ValueError: If the locator is not path-backed.
    """
    path = locator.as_path()
    if path is None:
        raise ValueError(f"local adapter requires locator kind 'path', got {locator.kind!r}")
    return path


def _load_fsspec() -> Any:
    """Import fsspec lazily and raise a clear optional-dependency error."""
    try:
        return importlib.import_module("fsspec")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "fsspec support requires the optional dependency. Install with 'ogcat[fsspec]'."
        ) from exc


__all__ = [
    "ChecksumPolicy",
    "FsspecStorageAdapter",
    "LocalStorageAdapter",
    "StorageAdapter",
    "StoragePlan",
    "TargetKind",
    "WriteMode",
    "adapter_for_locator",
    "create_directory_target",
    "ensure_parent_directory",
    "ensure_target_absent",
    "plan_storage",
    "register_remove_on_rollback",
    "remove_target",
    "require_local_path",
    "require_storage_target",
]
