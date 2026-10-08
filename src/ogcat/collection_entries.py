"""Live collection entries and reusable local metadata extraction.

Entries describe the filesystem at read time and have no persisted record
identity. Directory datasets remain leaves until callers supply a member pattern.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ogcat.classification import _normalize_collection_pattern
from ogcat.hooks import OperationContext
from ogcat.models import ArtifactLocator, MetadataDict, normalize_metadata
from ogcat.search import SearchQuery, matches_metadata

MetadataExtractor = Callable[[Path], Mapping[str, object] | None]


def _local_member_paths(locator: ArtifactLocator, pattern: str) -> list[Path]:
    """Enumerate existing members while rejecting patterns and symlinks that escape."""
    if locator.kind != "path":
        raise NotImplementedError("Collection members support local path collections only.")
    root = Path(locator.value)
    if not root.is_dir():
        raise FileNotFoundError(f"Collection root is not an existing directory: {root}")
    pattern = _normalize_collection_pattern(pattern)
    resolved_root = root.resolve()
    members: list[Path] = []
    for candidate in root.glob(pattern):
        if not candidate.resolve().is_relative_to(resolved_root):
            raise ValueError(f"Collection member escapes its root: {candidate}")
        if candidate.exists():
            members.append(candidate)
    return sorted(members)


@dataclass(slots=True)
class CollectionEntry:
    """A live artifact locator with extracted metadata and optional traversal.

    Args:
        locator: Artifact location, independent of a catalog record.
        metadata: Plain metadata used for read-side filtering.
        member_pattern: Explicit relative glob declaring collection traversal.
            Omit for leaf artifacts, including directory datasets.
    """

    locator: ArtifactLocator
    metadata: MetadataDict
    member_pattern: str | None = None

    def path(self) -> Path | None:
        """Return the local path, or ``None`` for a non-local locator."""
        return Path(self.locator.value) if self.locator.kind == "path" else None

    def members(
        self,
        *,
        pattern: str | None = None,
        extractor: MetadataExtractor | None = None,
        query: SearchQuery | None = None,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
    ) -> list[CollectionEntry]:
        """Extract and filter current members of an explicitly declared collection.

        Args:
            pattern: Relative glob overriding ``member_pattern``. Required when
                this entry is a leaf. Traversal never recurses automatically.
            extractor: Callable receiving each matching path and returning plain
                metadata or ``None``. Exceptions propagate. Use a closure or
                partial to supply parent context; metadata is never inherited.
            query: Optional search query over extracted metadata.
            where: Equality filters.
            contains: Substring or list-membership filters.
            regex: Regular-expression filters.
            match: Glob or substring filters.
            exists: Metadata fields that must be present.
            missing: Metadata fields that must be absent.
            ignore_case: Whether string comparisons ignore case.

        Returns:
            Matching entries in path order, each initially a leaf.

        Raises:
            ValueError: If no pattern is declared, or traversal escapes the root.
            NotImplementedError: If the locator is non-local.
            FileNotFoundError: If the root is not an existing directory.
            TypeError: If extracted metadata is not JSON-compatible.
        """
        selected_pattern = self.member_pattern if pattern is None else pattern
        if selected_pattern is None:
            raise ValueError("Collection traversal requires an explicit member pattern.")
        filters = SearchQuery.from_filters(
            where=where, contains=contains, regex=regex, match=match, exists=exists, missing=missing
        )
        active_query = (query or SearchQuery.all()).and_(filters)
        entries: list[CollectionEntry] = []
        for path in _local_member_paths(self.locator, selected_pattern):
            extracted = extractor(path) if extractor is not None else None
            metadata = normalize_metadata({} if extracted is None else extracted)
            if matches_metadata(
                metadata,
                query=active_query,
                ignore_case=ignore_case,
            ):
                entries.append(CollectionEntry(ArtifactLocator.from_path(path), metadata))
        return entries


@dataclass(slots=True)
class MetadataExtractorHook:
    """Adapt a read-side extractor to inspect ingest's local source before writing.

    Reading before materialization preserves the source filename and allows
    content inspection before a move removes it. Operations without a local
    source are skipped; generated outputs need their own extraction hook.

    Args:
        extractor: Callable returning plain derived metadata or ``None``.
    """

    extractor: MetadataExtractor

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Merge normalized source metadata into the ingest context.

        Args:
            context: Ingest context before validation and artifact writing.
        """
        path = context.source.path
        if path is not None:
            extracted = self.extractor(path)
            if extracted is not None:
                context.derived_metadata.update(normalize_metadata(extracted))
