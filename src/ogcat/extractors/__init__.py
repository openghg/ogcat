"""Lightweight derived-metadata extractors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ogcat.models import JsonValue, MetadataDict


class DerivedMetadataExtractor(Protocol):
    """Simple interface for optional file metadata extractors."""

    @property
    def name(self) -> str:
        """Extractor name used as the derived metadata key."""
        ...

    def can_extract(self, path: Path) -> bool:
        """Return whether this extractor should run for the given file."""
        ...

    def extract(self, path: Path) -> JsonValue | None:
        """Return extracted metadata for this file, or ``None`` if unavailable."""
        ...


@dataclass(frozen=True, slots=True)
class SuffixExtractor:
    """Convenience base for suffix-driven extractors."""

    name: str
    suffixes: tuple[str, ...]

    def can_extract(self, path: Path) -> bool:
        path_suffixes = {suffix.lower() for suffix in path.suffixes}
        return any(suffix in path_suffixes for suffix in self.suffixes)


def extract_derived_metadata(path: str | Path, *, include_errors: bool = False) -> MetadataDict:
    """Run all matching extractors and collect their derived metadata."""
    source = Path(path)
    derived: MetadataDict = {}
    errors: MetadataDict = {}

    for extractor in _EXTRACTORS:
        try:
            can_extract = extractor.can_extract(source)
        except Exception as exc:
            # Extractor selection is best-effort too; a broken optional
            # extractor must not block artifact ingest.
            if include_errors:
                errors[_extractor_name(extractor)] = f"{type(exc).__name__}: {exc}"
            continue
        if not can_extract:
            continue
        try:
            extracted = extractor.extract(source)
        except Exception as exc:
            # Derived metadata is best-effort and should not block file ingestion.
            if include_errors:
                errors[_extractor_name(extractor)] = f"{type(exc).__name__}: {exc}"
            continue
        if extracted is not None:
            derived[_extractor_name(extractor)] = extracted

    if errors:
        derived["extractor_errors"] = errors

    return derived


def _extractor_name(extractor: DerivedMetadataExtractor) -> str:
    """Return a stable extractor name for metadata and error reporting."""
    try:
        return str(extractor.name)
    except Exception:
        return type(extractor).__name__


def _default_extractors() -> tuple[DerivedMetadataExtractor, ...]:
    from ogcat.extractors.netcdf import NetcdfExtractor

    return (NetcdfExtractor(),)


_EXTRACTORS = _default_extractors()

__all__ = ["DerivedMetadataExtractor", "SuffixExtractor", "extract_derived_metadata"]
