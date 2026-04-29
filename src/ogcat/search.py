"""Search helpers for catalog records."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any

from ogcat.models import CatalogRecord

_RESERVED_FIELDS = {
    "id",
    "catalog",
    "record_type",
    "locator",
    "stored_abspath",
    "stored_relpath",
    "storage_mode",
    "time_added",
    "original_path",
    "original_filename",
    "suffixes",
    "user_metadata",
    "derived_metadata",
    "naming_metadata",
}


@dataclass(frozen=True, slots=True)
class FieldLookup:
    """Resolved field value plus whether the field existed."""

    found: bool
    value: Any = None


@dataclass(frozen=True, slots=True)
class FieldPath:
    """Backend-neutral field path used by search terms.

    Args:
        raw: User-facing field path. ``user.`` and ``derived.`` prefixes are
            normalised to stored metadata namespaces.
    """

    raw: str

    def __post_init__(self) -> None:
        """Validate field paths at construction time."""
        if not self.raw:
            raise ValueError("Search field path cannot be empty.")

    @property
    def stored(self) -> str:
        """Return this field path normalised to the stored record shape."""
        if self.raw == "user":
            return "user_metadata"
        if self.raw.startswith("user."):
            return f"user_metadata.{self.raw.removeprefix('user.')}"
        if self.raw == "derived":
            return "derived_metadata"
        if self.raw.startswith("derived."):
            return f"derived_metadata.{self.raw.removeprefix('derived.')}"
        return self.raw


class SearchOp(StrEnum):
    """Portable search operators."""

    EQ = "eq"
    CONTAINS = "contains"
    MATCH = "match"
    EXISTS = "exists"
    MISSING = "missing"
    REGEX = "regex"


@dataclass(frozen=True, slots=True)
class SearchTerm:
    """One backend-neutral search predicate.

    Args:
        field: Field path to resolve.
        op: Search operator.
        value: Optional comparison value.
    """

    field: FieldPath
    op: SearchOp
    value: Any = None

    @classmethod
    def build(cls, field: str | FieldPath, op: SearchOp | str, value: Any = None) -> SearchTerm:
        """Build a search term from user-facing field and operator inputs."""
        field_path = field if isinstance(field, FieldPath) else FieldPath(field)
        search_op = op if isinstance(op, SearchOp) else SearchOp(op)
        return cls(field=field_path, op=search_op, value=value)


class _SearchQueryTermBuilder:
    """Descriptor that works as both a constructor and chainable query method."""

    def __init__(self, op: SearchOp) -> None:
        self._op = op

    def __get__(self, query: SearchQuery | None, owner: type[SearchQuery]) -> Any:
        """Return a function that appends this descriptor's operator."""

        def build(field: str, value: Any = None) -> SearchQuery:
            base_query = owner.all() if query is None else query
            return base_query.and_(owner((SearchTerm.build(field=field, op=self._op, value=value),)))

        return build


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Backend-neutral catalog record search query.

    Terms are combined with AND semantics.

    Args:
        terms: Search terms to require.
        criteria: Backwards-compatible alias for ``terms``.
    """

    terms: tuple[SearchTerm, ...] = ()

    def __init__(
        self,
        terms: Sequence[SearchTerm] = (),
        *,
        criteria: Sequence[SearchTerm] | None = None,
    ) -> None:
        """Create a search query from backend-neutral search terms.

        Args:
            terms: Search terms combined with AND semantics.
            criteria: Backwards-compatible alias for terms.
        """
        if criteria is not None:
            terms = criteria
        object.__setattr__(self, "terms", tuple(terms))

    @property
    def criteria(self) -> tuple[SearchTerm, ...]:
        """Backwards-compatible alias for search terms."""
        return self.terms

    @classmethod
    def all(cls) -> SearchQuery:
        """Return a query that matches every record."""
        return cls()

    eq = _SearchQueryTermBuilder(SearchOp.EQ)
    equals = _SearchQueryTermBuilder(SearchOp.EQ)
    contains = _SearchQueryTermBuilder(SearchOp.CONTAINS)
    match = _SearchQueryTermBuilder(SearchOp.MATCH)
    matches = _SearchQueryTermBuilder(SearchOp.MATCH)
    regex = _SearchQueryTermBuilder(SearchOp.REGEX)
    exists = _SearchQueryTermBuilder(SearchOp.EXISTS)
    missing = _SearchQueryTermBuilder(SearchOp.MISSING)

    @classmethod
    def where(cls, filters: Mapping[str, Any] | None = None, **kwargs: Any) -> SearchQuery:
        """Build an equality query from keyword filters."""
        combined = dict(filters or {})
        combined.update(kwargs)
        return cls.from_filters(where=combined)

    @classmethod
    def from_filters(
        cls,
        *,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, Any] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
    ) -> SearchQuery:
        """Build a query from simple filter mappings."""
        terms: list[SearchTerm] = []
        terms.extend(
            SearchTerm.build(field=field, op=SearchOp.EQ, value=value)
            for field, value in (where or {}).items()
        )
        terms.extend(
            SearchTerm.build(field=field, op=SearchOp.CONTAINS, value=value)
            for field, value in (contains or {}).items()
        )
        terms.extend(
            SearchTerm.build(field=field, op=SearchOp.MATCH, value=value)
            for field, value in (match or {}).items()
        )
        terms.extend(
            SearchTerm.build(field=field, op=SearchOp.REGEX, value=value)
            for field, value in (regex or {}).items()
        )
        terms.extend(SearchTerm.build(field=field, op=SearchOp.EXISTS) for field in (exists or ()))
        terms.extend(SearchTerm.build(field=field, op=SearchOp.MISSING) for field in (missing or ()))
        return cls(tuple(terms))

    def and_(self, *queries: SearchQuery) -> SearchQuery:
        """Return a query requiring this query and all provided queries to match."""
        terms = list(self.terms)
        for query in queries:
            terms.extend(query.terms)
        return SearchQuery(tuple(terms))


def _normalise_field_path(dotted_path: str) -> str:
    """Translate user-friendly aliases to stored record paths."""
    return FieldPath(dotted_path).stored


def get_dotted(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    """Resolve a dotted path inside a nested mapping."""
    return resolve_dotted(mapping, dotted_path).value


def resolve_dotted(mapping: Mapping[str, Any], dotted_path: str) -> FieldLookup:
    """Resolve a dotted path inside a nested mapping."""
    current: Any = mapping
    for part in _normalise_field_path(dotted_path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            return FieldLookup(found=False)
        current = current[part]
    return FieldLookup(found=True, value=current)


def flatten_lookup(
    record: CatalogRecord,
    field: str,
    resolution_order: Sequence[str] | None = None,
) -> Any:
    """Resolve a field using dotted paths or precedence-based flattened lookup."""
    return resolve_field(record, field, resolution_order=resolution_order).value


def resolve_field(
    record: CatalogRecord,
    field: str,
    resolution_order: Sequence[str] | None = None,
) -> FieldLookup:
    """Resolve a field using dotted paths or precedence-based flattened lookup."""
    field = _normalise_field_path(field)
    record_dict = record.to_dict()

    if field == "path":
        resolved_path = record.path()
        return FieldLookup(
            found=resolved_path is not None, value=None if resolved_path is None else str(resolved_path)
        )
    if field == "locator.uri":
        if record.locator.kind == "uri":
            return FieldLookup(found=True, value=record.locator.value)
        return FieldLookup(found=False)

    if "." in field:
        return resolve_dotted(record_dict, field)

    order = resolution_order or ["top_level", "user_metadata", "derived_metadata"]
    namespaces: dict[str, Mapping[str, Any]] = {
        "top_level": record_dict,
        "user_metadata": record.user_metadata,
        "derived_metadata": record.derived_metadata,
    }

    for namespace_name in order:
        namespace = namespaces.get(namespace_name)
        if namespace is None:
            continue
        if namespace_name == "top_level" and field not in _RESERVED_FIELDS:
            continue
        if field in namespace:
            return FieldLookup(found=True, value=namespace[field])

    return FieldLookup(found=False)


def _stringify(value: Any) -> str:
    return "" if value is None else str(value)


def _eq(left: Any, right: Any, ignore_case: bool) -> bool:
    if ignore_case and isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _contains(actual: Any, expected: Any, ignore_case: bool) -> bool:
    """Return whether an actual value contains the expected value."""
    if isinstance(actual, str):
        needle = _stringify(expected)
        haystack = actual.casefold() if ignore_case else actual
        needle = needle.casefold() if ignore_case else needle
        return needle in haystack
    if isinstance(actual, Mapping):
        if isinstance(expected, Mapping):
            return all(
                key in actual and _eq(actual[key], value, ignore_case) for key, value in expected.items()
            )
        try:
            return expected in actual
        except TypeError:
            return False
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        expected_items = expected if isinstance(expected, list) else [expected]
        return all(
            any(_eq(item, expected_item, ignore_case) for item in actual) for expected_item in expected_items
        )
    return _eq(actual, expected, ignore_case)


def _match(actual: Any, pattern: str, ignore_case: bool) -> bool:
    """Return whether a string value matches a glob or substring pattern."""
    text = _stringify(actual)
    pattern_text = _stringify(pattern)
    if ignore_case:
        text = text.casefold()
        pattern_text = pattern_text.casefold()
    if any(char in pattern_text for char in "*?[]"):
        return fnmatchcase(text, pattern_text)
    return pattern_text in text


def _matches_criterion(
    record: CatalogRecord,
    term: SearchTerm,
    *,
    ignore_case: bool,
    resolution_order: Sequence[str] | None,
) -> bool:
    """Return whether a record matches one criterion."""
    resolved = resolve_field(record, term.field.stored, resolution_order=resolution_order)
    if term.op == SearchOp.EXISTS:
        return resolved.found
    if term.op == SearchOp.MISSING:
        return not resolved.found
    if not resolved.found:
        return False
    if term.op == SearchOp.EQ:
        return _eq(resolved.value, term.value, ignore_case)
    if term.op == SearchOp.CONTAINS:
        return _contains(resolved.value, term.value, ignore_case)
    if term.op == SearchOp.MATCH:
        return _match(resolved.value, str(term.value), ignore_case)
    if term.op == SearchOp.REGEX:
        flags = re.IGNORECASE if ignore_case else 0
        return re.search(str(term.value), _stringify(resolved.value), flags=flags) is not None
    raise ValueError(f"Unsupported search operator: {term.op}")


def matches_record(
    record: CatalogRecord,
    *,
    query: SearchQuery | None = None,
    where: dict[str, object] | None,
    contains: dict[str, Any] | None,
    regex: dict[str, str] | None,
    match: dict[str, str] | None = None,
    exists: Sequence[str] | None = None,
    missing: Sequence[str] | None = None,
    ignore_case: bool,
    resolution_order: Sequence[str] | None = None,
) -> bool:
    """Return whether a record matches the provided filters."""
    filter_query = SearchQuery.from_filters(
        where=where,
        contains=contains,
        regex=regex,
        match=match,
        exists=exists,
        missing=missing,
    )
    active_query = (query or SearchQuery.all()).and_(filter_query)
    for term in active_query.terms:
        if not _matches_criterion(
            record,
            term,
            ignore_case=ignore_case,
            resolution_order=resolution_order,
        ):
            return False

    return True
