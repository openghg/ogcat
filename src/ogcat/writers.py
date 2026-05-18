"""Artifact writer helpers for lightweight catalog workflows.

Writers bridge ``Catalog.add_artifact`` and concrete storage code. A writer is
any object that satisfies :class:`ogcat.hooks.ArtifactWriter`: it exposes a
``write(context, source, target)`` method, receives an
:class:`ogcat.hooks.OperationContext`, and materialises an
:class:`ogcat.models.ArtifactLocator` from an :class:`ogcat.hooks.OperationSource`.

This module provides small adapters for common examples. ``source_writer`` wraps
a function that accepts an ``OperationSource`` and target ``Path``.
``memory_writer`` and ``path_writer`` adapt narrower functions for in-memory
payloads and local files. ``UnzipArtifactWriter`` is a concrete directory writer
used by tutorials and tests. ``UnzipSingleFileArtifactWriter`` extracts one zip
member to a file target. These helpers register rollback actions through the
operation context before writing so partially-created targets can be cleaned up
if the catalog operation fails.
"""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, TypeAlias

from ogcat.hooks import OperationContext, OperationSource
from ogcat.models import ArtifactLocator, JsonValue, MetadataDict
from ogcat.storage import (
    TargetKind,
    WriteMode,
    adapter_for_locator,
    ensure_parent_directory,
    ensure_target_absent,
    remove_target,
    require_local_path,
)

SourceWriteFunction: TypeAlias = Callable[[OperationSource, Path], MetadataDict | None]
MemoryWriteFunction: TypeAlias = Callable[[object, Path], MetadataDict | None]
PathWriteFunction: TypeAlias = Callable[[Path, Path], MetadataDict | None]


def memory_source(
    data: object,
    *,
    kind: str = "memory",
    descriptor: str | None = None,
    metadata: MetadataDict | None = None,
) -> OperationSource:
    """Build an operation source carrying an in-memory Python object.

    Args:
        data: In-memory object to pass to a memory writer.
        kind: Source kind used for writer validation.
        descriptor: Optional human-readable source description.
        metadata: Optional JSON-compatible source metadata.

    Returns:
        An operation source with ``payload`` set to ``data``.
    """
    return OperationSource(
        kind=kind,
        descriptor=descriptor,
        metadata={} if metadata is None else dict(metadata),
        payload=data,
    )


def path_source(
    path: str | Path,
    *,
    kind: str = "path",
    descriptor: str | None = None,
    metadata: MetadataDict | None = None,
) -> OperationSource:
    """Build an operation source carrying a local path.

    Args:
        path: Local source path.
        kind: Source kind used for writer validation.
        descriptor: Optional human-readable source description. Defaults to the
            resolved source path string.
        metadata: Optional JSON-compatible source metadata.

    Returns:
        An operation source with ``path`` set to the resolved local path.
    """
    source_path = Path(path).expanduser().resolve()
    return OperationSource(
        kind=kind,
        path=source_path,
        descriptor=descriptor or str(source_path),
        metadata={} if metadata is None else dict(metadata),
    )


@dataclass(frozen=True, slots=True)
class FunctionArtifactWriter:
    """Artifact writer adapter around a small Python function.

    The wrapped function receives an ``OperationSource`` and a local target
    path. It should write either a single file or a directory, matching
    ``target_kind``. If it returns metadata, the metadata is merged into
    ``context.derived_metadata``.

    Args:
        write_function: Function that writes artifact data.
        target_kind: Whether the target locator should be treated as a file or
            directory.
        source_kind: Optional required source kind.
    """

    write_function: SourceWriteFunction
    target_kind: TargetKind
    source_kind: str | None = None
    write_mode: WriteMode = "write"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Write artifact data and register rollback for the created target."""
        if self.source_kind is not None and source.kind != self.source_kind:
            raise ValueError(f"writer requires source kind {self.source_kind!r}, got {source.kind!r}")

        target_path = _target_path(target)
        _prepare_empty_target(target_path, self.target_kind)
        context.rollback(
            lambda path=target_path, target_kind=self.target_kind: _remove_target(path, target_kind),
            description=f"remove written {self.target_kind} artifact {target_path}",
        )
        metadata = self.write_function(source, target_path)
        if metadata is not None:
            context.derived_metadata.update(metadata)


@dataclass(frozen=True, slots=True)
class CopyArtifactWriter:
    """Artifact writer that copies a local source path to a storage target."""

    source_kind: str = "local_file"
    target_kind: ClassVar[TargetKind] = "file"
    write_mode: ClassVar[WriteMode] = "copy"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Copy a local source path to the target and register rollback."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(f"copy writer requires OperationSource(kind={self.source_kind!r}, path=...)")
        if self.target_kind != "file":
            raise ValueError("copy writer currently supports file targets only")
        adapter = adapter_for_locator(target)
        ensure_target_absent(target, adapter=adapter)
        ensure_parent_directory(target, adapter=adapter)
        context.rollback(
            lambda locator=target, target_kind=self.target_kind, storage=adapter: remove_target(
                locator,
                target_kind=target_kind,
                adapter=storage,
            ),
            description=f"remove copied {self.target_kind} artifact {target.value}",
        )
        adapter.copy_from_path(source.path, target)


@dataclass(frozen=True, slots=True)
class CopyDirectoryArtifactWriter:
    """Artifact writer that copies a local source directory to a directory target."""

    source_kind: str = "local_file"
    target_kind: ClassVar[TargetKind] = "directory"
    write_mode: ClassVar[WriteMode] = "copy"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Copy a local source directory to the target and register rollback."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(
                f"copy directory writer requires OperationSource(kind={self.source_kind!r}, path=...)"
            )
        if not source.path.is_dir():
            raise ValueError(f"copy directory writer requires an existing directory: {source.path}")
        if target.kind != "path":
            raise ValueError("copy directory writer currently requires a path-backed target")
        adapter = adapter_for_locator(target)
        ensure_target_absent(target, adapter=adapter)
        target_path = require_local_path(target)
        ensure_parent_directory(target, adapter=adapter)
        context.rollback(
            lambda locator=target, storage=adapter: remove_target(
                locator,
                target_kind=self.target_kind,
                adapter=storage,
            ),
            description=f"remove copied {self.target_kind} artifact {target.value}",
        )
        shutil.copytree(source.path, target_path)


@dataclass(frozen=True, slots=True)
class MoveArtifactWriter:
    """Artifact writer that moves a local source path to a storage target."""

    source_kind: str = "local_file"
    target_kind: ClassVar[TargetKind] = "file"
    write_mode: ClassVar[WriteMode] = "move"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Move a local source path to the target and register rollback."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(f"move writer requires OperationSource(kind={self.source_kind!r}, path=...)")
        if target.kind != "path":
            raise ValueError("move writer currently requires a path-backed target for rollback-safe moves")
        if self.target_kind != "file":
            raise ValueError("move writer currently supports file targets only")
        adapter = adapter_for_locator(target)
        ensure_target_absent(target, adapter=adapter)
        target_path = require_local_path(target)
        ensure_parent_directory(target, adapter=adapter)
        context.rollback(
            lambda source_path=source.path, stored_path=target_path: _rollback_moved_file(
                source_path=source_path,
                target_path=stored_path,
            ),
            description=f"restore moved file from {target_path} to {source.path}",
        )
        adapter.move_from_path(source.path, target)


@dataclass(frozen=True, slots=True)
class MoveDirectoryArtifactWriter:
    """Artifact writer that moves a local source directory to a directory target."""

    source_kind: str = "local_file"
    target_kind: ClassVar[TargetKind] = "directory"
    write_mode: ClassVar[WriteMode] = "move"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Move a local source directory to the target and register rollback."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(
                f"move directory writer requires OperationSource(kind={self.source_kind!r}, path=...)"
            )
        if not source.path.is_dir():
            raise ValueError(f"move directory writer requires an existing directory: {source.path}")
        if target.kind != "path":
            raise ValueError("move directory writer currently requires a path-backed target")
        adapter = adapter_for_locator(target)
        ensure_target_absent(target, adapter=adapter)
        target_path = require_local_path(target)
        ensure_parent_directory(target, adapter=adapter)
        context.rollback(
            lambda source_path=source.path, stored_path=target_path: _rollback_moved_target(
                source_path=source_path,
                target_path=stored_path,
                target_kind=self.target_kind,
            ),
            description=f"restore moved {self.target_kind} from {target_path} to {source.path}",
        )
        shutil.move(str(source.path), str(target_path))


def source_writer(
    write_function: SourceWriteFunction,
    *,
    target_kind: TargetKind,
    source_kind: str | None = None,
) -> FunctionArtifactWriter:
    """Wrap a function that writes from an ``OperationSource`` to a target path."""
    return FunctionArtifactWriter(
        write_function=write_function,
        target_kind=target_kind,
        source_kind=source_kind,
    )


def memory_writer(
    write_function: MemoryWriteFunction,
    *,
    target_kind: TargetKind,
    source_kind: str = "memory",
) -> FunctionArtifactWriter:
    """Wrap a function that writes an in-memory payload to a target path."""

    def write_from_memory(source: OperationSource, target: Path) -> MetadataDict | None:
        """Write the source payload to the target path."""
        return write_function(source.payload, target)

    return source_writer(write_from_memory, target_kind=target_kind, source_kind=source_kind)


def path_writer(
    write_function: PathWriteFunction,
    *,
    target_kind: TargetKind,
    source_kind: str = "path",
) -> FunctionArtifactWriter:
    """Wrap a function that writes from a source path to a target path."""

    def write_from_path(source: OperationSource, target: Path) -> MetadataDict | None:
        """Write the source path to the target path."""
        if source.path is None:
            raise ValueError("path writer requires source.path")
        return write_function(source.path, target)

    return source_writer(write_from_path, target_kind=target_kind, source_kind=source_kind)


@dataclass(frozen=True, slots=True)
class UnzipArtifactWriter:
    """Example writer that safely extracts a zip file into a directory target."""

    source_kind: str = "zip_file"
    target_kind: ClassVar[TargetKind] = "directory"
    write_mode: ClassVar[WriteMode] = "write"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Extract a zip source into the target directory."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(f"unzip writer requires OperationSource(kind={self.source_kind!r}, path=...)")

        target_dir = _target_path(target)
        _prepare_empty_target(target_dir, "directory")
        context.rollback(
            lambda path=target_dir: shutil.rmtree(path, ignore_errors=True),
            description=f"remove extracted directory {target_dir}",
        )
        target_root = target_dir.resolve()
        with zipfile.ZipFile(source.path) as archive:
            members = archive.infolist()
            names = sorted(member.filename for member in members if not member.is_dir())
            for member in members:
                destination = (target_dir / member.filename).resolve()
                if destination != target_root and target_root not in destination.parents:
                    raise ValueError(f"zip member escapes target directory: {member.filename}")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, destination.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)

        extracted_names: list[JsonValue] = list(names)
        context.derived_metadata["extracted_file_count"] = len(names)
        context.derived_metadata["extracted_names"] = extracted_names


@dataclass(frozen=True, slots=True)
class UnzipSingleFileArtifactWriter:
    """Extract a single zip member to a file target.

    Args:
        member_name: Optional archive member to extract. When omitted, the
            archive must contain exactly one non-directory member.
        source_kind: Required operation source kind.
    """

    member_name: str | None = None
    source_kind: str = "zip_file"
    target_kind: ClassVar[TargetKind] = "file"
    write_mode: ClassVar[WriteMode] = "write"

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Extract one zip source member to the target file."""
        if source.kind != self.source_kind or source.path is None:
            raise ValueError(
                f"single-file unzip writer requires OperationSource(kind={self.source_kind!r}, path=...)"
            )

        target_path = _target_path(target)
        _prepare_empty_target(target_path, "file")
        context.rollback(
            lambda path=target_path: path.unlink(missing_ok=True),
            description=f"remove extracted file {target_path}",
        )
        with zipfile.ZipFile(source.path) as archive:
            member = _select_zip_member(archive, member_name=self.member_name)
            with archive.open(member) as source_file, target_path.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)

        context.derived_metadata["extracted_file_count"] = 1
        context.derived_metadata["extracted_name"] = member.filename
        context.derived_metadata["extracted_size"] = member.file_size


def _select_zip_member(archive: zipfile.ZipFile, *, member_name: str | None) -> zipfile.ZipInfo:
    """Return the selected safe file member from an archive."""
    members = [member for member in archive.infolist() if not member.is_dir()]
    if member_name is None:
        if len(members) != 1:
            raise ValueError(f"expected exactly one file in zip archive, found {len(members)}")
        member = members[0]
    else:
        try:
            member = archive.getinfo(member_name)
        except KeyError as exc:
            raise ValueError(f"zip member not found: {member_name}") from exc
        if member.is_dir():
            raise ValueError(f"zip member is a directory: {member_name}")

    member_path = Path(member.filename)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"zip member escapes target file: {member.filename}")
    return member


def _target_path(locator: ArtifactLocator) -> Path:
    """Return a path-backed locator target."""
    target_path = locator.as_path()
    if target_path is None:
        raise ValueError("artifact writer requires a path-backed target locator")
    return target_path


def _prepare_empty_target(target_path: Path, target_kind: TargetKind) -> None:
    """Prepare a target that this writer can safely remove on rollback."""
    if target_path.exists():
        raise FileExistsError(f"target {target_path} already exists")
    if target_kind == "file":
        target_path.parent.mkdir(parents=True, exist_ok=True)
        return
    if target_kind == "directory":
        target_path.mkdir(parents=True, exist_ok=False)
        return
    raise ValueError(f"Unsupported target kind: {target_kind}")


def _remove_target(target_path: Path, target_kind: TargetKind) -> None:
    """Remove a file or directory target created by a helper writer."""
    if target_kind == "file":
        target_path.unlink(missing_ok=True)
        return
    shutil.rmtree(target_path, ignore_errors=True)


def _rollback_moved_file(*, source_path: Path, target_path: Path) -> None:
    """Restore a moved file when possible, otherwise remove the moved target."""
    _rollback_moved_target(source_path=source_path, target_path=target_path, target_kind="file")


def _rollback_moved_target(*, source_path: Path, target_path: Path, target_kind: TargetKind) -> None:
    """Restore a moved target when possible, otherwise remove the moved target."""
    if not target_path.exists():
        return
    if not source_path.exists():
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_path), str(source_path))
        return
    _remove_target(target_path, target_kind)


__all__ = [
    "CopyArtifactWriter",
    "CopyDirectoryArtifactWriter",
    "FunctionArtifactWriter",
    "MemoryWriteFunction",
    "MoveArtifactWriter",
    "MoveDirectoryArtifactWriter",
    "PathWriteFunction",
    "SourceWriteFunction",
    "UnzipArtifactWriter",
    "UnzipSingleFileArtifactWriter",
    "memory_source",
    "memory_writer",
    "path_source",
    "path_writer",
    "source_writer",
]
