"""Cheap artifact classification helpers."""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import urlsplit

from ogcat.models import ArtifactLocator, JsonValue, MetadataDict

CLASSIFICATION_METADATA_KEY = "classification"
CLASSIFICATION_SEARCH_FIELDS = frozenset(
    {
        "artifact_kind",
        "format",
        "archive_format",
        "inner_format",
    }
)

_ARCHIVE_SUFFIX_FORMATS = {
    ".zip": "zip",
    ".gz": "gzip",
    ".gzip": "gzip",
    ".tar": "tar",
    ".tgz": "tar",
    ".tbz": "tar",
    ".tbz2": "tar",
    ".txz": "tar",
}
_NETCDF_SUFFIXES = {".nc", ".nc3", ".nc4", ".cdf"}
_TEXT_SUFFIXES = {".asc", ".ascii", ".csv", ".dat", ".json", ".md", ".text", ".tsv", ".txt"}
_ZARR_SUFFIXES = {".zarr"}


def classify_artifact(
    locator: ArtifactLocator,
    *,
    original_path: str | Path | None = None,
    original_filename: str | None = None,
    suffixes: Sequence[str] | None = None,
) -> MetadataDict:
    """Infer normalized, cheap artifact classification metadata.

    The classifier uses locator text, path suffixes, and local path shape. It
    does not open NetCDF, HDF5, Zarr, or other scientific data payloads. For
    local zip files it may read the central directory to identify a single
    member's suffix without extracting file content.

    Args:
        locator: Stored artifact locator.
        original_path: Optional original source path or URI.
        original_filename: Optional original source filename.
        suffixes: Optional suffix list to preserve in the classification.

    Returns:
        JSON-compatible metadata for ``derived_metadata["classification"]``.
    """
    resolved_suffixes = _resolve_suffixes(
        suffixes=suffixes,
        locator=locator,
        original_path=original_path,
        original_filename=original_filename,
    )
    local_path = locator.as_path()
    local_is_dir = _is_local_directory(local_path)
    archive_format = _archive_format(resolved_suffixes)
    format_name = _format_from_suffixes(resolved_suffixes)
    artifact_kind = _artifact_kind(
        locator=locator,
        suffixes=resolved_suffixes,
        local_is_dir=local_is_dir,
        archive_format=archive_format,
    )

    metadata: MetadataDict = {
        "artifact_kind": artifact_kind,
        "format": format_name,
        "suffixes": cast(JsonValue, list(resolved_suffixes)),
    }
    if archive_format is not None:
        metadata["archive_format"] = archive_format
        inner_format = _inner_format_from_suffix_chain(resolved_suffixes)
        if inner_format is not None:
            metadata["inner_format"] = inner_format
    if archive_format == "zip" and local_path is not None:
        inner_format = _zip_single_member_inner_format(local_path)
        if inner_format is not None:
            metadata["inner_format"] = inner_format

    return metadata


def _resolve_suffixes(
    *,
    suffixes: Sequence[str] | None,
    locator: ArtifactLocator,
    original_path: str | Path | None,
    original_filename: str | None,
) -> list[str]:
    """Return suffixes from explicit metadata or cheap locator/path parsing."""
    if suffixes:
        return [str(suffix) for suffix in suffixes]

    candidates: list[str | Path] = []
    if original_filename:
        candidates.append(original_filename)
    if original_path is not None:
        candidates.append(original_path)
    if locator.value:
        candidates.append(locator.value)

    for candidate in candidates:
        inferred = _suffixes_from_candidate(candidate, locator_kind=locator.kind)
        if inferred:
            return inferred
    return []


def _suffixes_from_candidate(candidate: str | Path, *, locator_kind: str) -> list[str]:
    """Infer suffixes from one local or URI-like locator candidate."""
    if isinstance(candidate, Path):
        return list(candidate.suffixes)
    value = str(candidate)
    if locator_kind in {"uri", "urlpath"} or "://" in value:
        parsed_path = urlsplit(value).path
        if "::" in parsed_path:
            parsed_path = parsed_path.rsplit("::", 1)[-1]
        return list(PurePosixPath(parsed_path).suffixes)
    return list(Path(value).suffixes)


def _is_local_directory(path: Path | None) -> bool:
    """Return whether a path-backed locator currently points at a directory."""
    if path is None:
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _artifact_kind(
    *,
    locator: ArtifactLocator,
    suffixes: Sequence[str],
    local_is_dir: bool,
    archive_format: str | None,
) -> str:
    """Return the normalized artifact kind."""
    if archive_format is not None:
        return "archive"
    if _has_suffix(suffixes, _ZARR_SUFFIXES):
        return "zarr_store"
    if locator.kind == "path":
        return "directory" if local_is_dir else "file"
    if locator.kind in {"uri", "urlpath"}:
        return "remote_resource"
    return "opaque"


def _archive_format(suffixes: Sequence[str]) -> str | None:
    """Return the outer archive or compression format from suffixes."""
    if not suffixes:
        return None
    return _ARCHIVE_SUFFIX_FORMATS.get(suffixes[-1].casefold())


def _format_from_suffixes(suffixes: Sequence[str]) -> str:
    """Return the normalized safe format name for suffixes."""
    if not suffixes:
        return "unknown"
    archive_format = _archive_format(suffixes)
    if archive_format is not None:
        return archive_format
    if _has_suffix(suffixes, _ZARR_SUFFIXES):
        return "zarr"
    if _has_suffix(suffixes, _NETCDF_SUFFIXES):
        return "netcdf"
    if _has_suffix(suffixes, _TEXT_SUFFIXES):
        return "text"
    return "unknown"


def _inner_format_from_suffix_chain(suffixes: Sequence[str]) -> str | None:
    """Return an inner format implied by a multi-part suffix chain."""
    if len(suffixes) < 2:
        return None
    inner_format = _format_from_suffixes(suffixes[:-1])
    return None if inner_format == "unknown" else inner_format


def _zip_single_member_inner_format(path: Path) -> str | None:
    """Return the single file member's format for a local zip archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [member.filename for member in archive.infolist() if not member.is_dir()]
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return None
    if len(names) != 1:
        return None
    inner_format = _format_from_suffixes(PurePosixPath(names[0]).suffixes)
    return None if inner_format == "unknown" else inner_format


def _has_suffix(suffixes: Sequence[str], expected: set[str]) -> bool:
    """Return whether suffixes contain one of the expected suffixes."""
    return any(suffix.casefold() in expected for suffix in suffixes)
