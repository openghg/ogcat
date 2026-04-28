"""Small artifact writer helpers for examples and lightweight workflows."""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from ogcat.hooks import OperationContext, OperationSource
from ogcat.models import ArtifactLocator, JsonValue, MetadataDict

TargetKind: TypeAlias = Literal["file", "directory"]
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


__all__ = [
    "FunctionArtifactWriter",
    "MemoryWriteFunction",
    "PathWriteFunction",
    "SourceWriteFunction",
    "TargetKind",
    "UnzipArtifactWriter",
    "memory_source",
    "memory_writer",
    "path_source",
    "path_writer",
    "source_writer",
]
