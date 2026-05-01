"""Storage planning and lightweight backend primitives.

This module keeps storage decisions explicit without turning ``ogcat`` into a
domain storage framework.  Plans describe where an artifact should live and how
it should be materialised; writers and catalog operations decide whether to
execute the plan.
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
OverwritePolicy = Literal["error", "overwrite"]
ChecksumPolicy = Literal["none"]


@dataclass(frozen=True, slots=True)
class StoragePlan:
    """Concrete storage decision for a catalog operation.

    Args:
        locator: Canonical artifact locator that should be stored on the record.
        source: Optional source locator or descriptor.
        target_kind: Whether the target is a file-like or directory-like
            artifact.
        write_mode: How data should be materialised, or ``"reference"`` for
            record-only external artifacts.
        overwrite: Collision policy for the target.
        checksum: Checksum policy requested for the write.
        ogcat_owned: Whether ``ogcat`` is responsible for cleanup on rollback.
        profile: Optional storage profile name or hint.
        backend: Optional backend identifier, such as ``"local"`` or
            ``"fsspec"``.
    """

    locator: ArtifactLocator
    source: ArtifactLocator | None = None
    target_kind: TargetKind = "file"
    write_mode: WriteMode = "reference"
    overwrite: OverwritePolicy = "error"
    checksum: ChecksumPolicy = "none"
    ogcat_owned: bool = False
    profile: str | None = None
    backend: str | None = None


class StorageBackend(Protocol):
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

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a file-like or directory-like locator."""
        ...


@dataclass(frozen=True, slots=True)
class LocalStorageBackend:
    """Storage backend for local path locators."""

    def exists(self, locator: ArtifactLocator) -> bool:
        """Return whether a local path-backed locator exists."""
        return _path_locator(locator).exists()

    def open(self, locator: ArtifactLocator, mode: str = "rb") -> IO[bytes]:
        """Open a local path-backed locator."""
        return _path_locator(locator).open(mode)

    def copy_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Copy a local source path to a local target."""
        target_path = _path_locator(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_path)

    def move_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Move a local source path to a local target."""
        target_path = _path_locator(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target_path))

    def mkdir(self, locator: ArtifactLocator) -> None:
        """Create a local directory target."""
        _path_locator(locator).mkdir(parents=True, exist_ok=False)

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a local file or directory target."""
        path = _path_locator(locator)
        if target_kind == "directory":
            shutil.rmtree(path, ignore_errors=True)
            return
        path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FsspecStorageBackend:
    """Storage backend for fsspec-addressable ``urlpath`` locators."""

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
        parent = str(Path(path).parent)
        if parent not in {"", "."}:
            fs.makedirs(parent, exist_ok=True)
        fs.put_file(str(source), path)

    def move_from_path(self, source: Path, target: ArtifactLocator) -> None:
        """Move a local file to a fsspec target."""
        self.copy_from_path(source, target)
        source.unlink()

    def mkdir(self, locator: ArtifactLocator) -> None:
        """Create a fsspec directory target."""
        fs, path = self._filesystem_and_path(locator)
        fs.makedirs(path, exist_ok=False)

    def remove(self, locator: ArtifactLocator, *, target_kind: TargetKind = "file") -> None:
        """Remove a fsspec target."""
        fs, path = self._filesystem_and_path(locator)
        if fs.exists(path):
            fs.rm(path, recursive=target_kind == "directory")

    def _filesystem_and_path(self, locator: ArtifactLocator) -> tuple[Any, str]:
        """Return the fsspec filesystem and backend path for a locator."""
        if locator.kind != "urlpath":
            raise ValueError(f"fsspec backend requires locator kind 'urlpath', got {locator.kind!r}")
        fsspec = _load_fsspec()
        fs, path = fsspec.core.url_to_fs(locator.value, **self._storage_options())
        return fs, path

    def _storage_options(self) -> dict[str, object]:
        """Return a defensive copy of configured storage options."""
        return {} if self.storage_options is None else dict(self.storage_options)


def backend_for_locator(locator: ArtifactLocator) -> StorageBackend:
    """Return the storage backend that can interpret a locator."""
    if locator.kind == "path":
        return LocalStorageBackend()
    if locator.kind == "urlpath":
        return FsspecStorageBackend()
    raise ValueError(f"No storage backend for locator kind {locator.kind!r}")


def plan_storage(
    locator: ArtifactLocator,
    *,
    source: ArtifactLocator | None = None,
    target_kind: TargetKind = "file",
    write_mode: WriteMode = "reference",
    overwrite: OverwritePolicy = "error",
    checksum: ChecksumPolicy = "none",
    ogcat_owned: bool = False,
    profile: str | None = None,
    backend: str | None = None,
) -> StoragePlan:
    """Build a storage plan from already-resolved storage decisions."""
    return StoragePlan(
        locator=locator,
        source=source,
        target_kind=target_kind,
        write_mode=write_mode,
        overwrite=overwrite,
        checksum=checksum,
        ogcat_owned=ogcat_owned,
        profile=profile,
        backend=backend,
    )


def execute_storage_plan(
    plan: StoragePlan,
    *,
    source_path: Path | None,
    register_rollback: Callable[[Callable[[], None], str], None] | None = None,
) -> None:
    """Execute a simple owned storage plan.

    Args:
        plan: Storage plan to execute.
        source_path: Local source path for copy or move plans.
        register_rollback: Optional callback that receives a cleanup callable
            and description before data is written.
    """
    if plan.write_mode == "reference":
        return
    if not plan.ogcat_owned:
        raise ValueError("Only ogcat-owned storage plans can be executed by ogcat core.")

    backend = backend_for_locator(plan.locator)
    if plan.overwrite == "error" and backend.exists(plan.locator):
        raise FileExistsError(f"target already exists: {plan.locator.value}")
    if plan.write_mode == "write":
        _register_remove_rollback(plan, backend, register_rollback)
        if plan.target_kind == "directory":
            backend.mkdir(plan.locator)
        return

    if source_path is None:
        raise ValueError(f"{plan.write_mode} storage plan requires a source path")

    if plan.write_mode == "move" and plan.locator.kind == "path":
        _register_local_move_rollback(plan, source_path, register_rollback)
    else:
        _register_remove_rollback(plan, backend, register_rollback)
    if plan.write_mode == "copy":
        backend.copy_from_path(source_path, plan.locator)
        return
    if plan.write_mode == "move":
        backend.move_from_path(source_path, plan.locator)
        return
    raise ValueError(f"Unsupported storage write mode: {plan.write_mode}")


def _register_local_move_rollback(
    plan: StoragePlan,
    source_path: Path,
    register_rollback: Callable[[Callable[[], None], str], None] | None,
) -> None:
    """Register source restoration for local move plans."""
    if register_rollback is None:
        return
    target_path = _path_locator(plan.locator)
    register_rollback(
        lambda source=source_path, target=target_path: _rollback_moved_file(source, target),
        f"restore moved file from {target_path} to {source_path}",
    )


def _register_remove_rollback(
    plan: StoragePlan,
    backend: StorageBackend,
    register_rollback: Callable[[Callable[[], None], str], None] | None,
) -> None:
    """Register cleanup for an owned target before it is created."""
    if register_rollback is None:
        return
    register_rollback(
        lambda locator=plan.locator, target_kind=plan.target_kind: backend.remove(
            locator,
            target_kind=target_kind,
        ),
        f"remove written {plan.target_kind} artifact {plan.locator.value}",
    )


def _path_locator(locator: ArtifactLocator) -> Path:
    """Return a local path for a path-backed locator."""
    path = locator.as_path()
    if path is None:
        raise ValueError(f"local backend requires locator kind 'path', got {locator.kind!r}")
    return path


def _rollback_moved_file(source_path: Path, target_path: Path) -> None:
    """Restore a moved local file when possible, otherwise remove the target."""
    if not target_path.exists():
        return
    if not source_path.exists():
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_path), str(source_path))
        return
    target_path.unlink(missing_ok=True)


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
    "FsspecStorageBackend",
    "LocalStorageBackend",
    "OverwritePolicy",
    "StorageBackend",
    "StoragePlan",
    "TargetKind",
    "WriteMode",
    "backend_for_locator",
    "execute_storage_plan",
    "plan_storage",
]
