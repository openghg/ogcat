"""Search helpers for catalog records."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
class SearchCriterion:
    """One backend-neutral search predicate."""

    field: str
    operator: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Backend-neutral catalog record search query."""

    criteria: tuple[SearchCriterion, ...] = ()

    @classmethod
    def all(cls) -> SearchQuery:
        """Return a query that matches every record."""
        return cls()

    @classmethod
    def equals(cls, field: str, value: Any) -> SearchQuery:
        """Return a query matching records where a field equals a value."""
        return cls((SearchCriterion(field=field, operator="eq", value=value),))

    @classmethod
    def contains(cls, field: str, value: Any) -> SearchQuery:
        """Return a query matching records where a field contains a value."""
        return cls((SearchCriterion(field=field, operator="contains", value=value),))

    @classmethod
    def matches(cls, field: str, pattern: str) -> SearchQuery:
        """Return a query matching records where a string field matches a pattern."""
        return cls((SearchCriterion(field=field, operator="match", value=pattern),))

    @classmethod
    def regex(cls, field: str, pattern: str) -> SearchQuery:
        """Return a query matching records where a string field matches a regex."""
        return cls((SearchCriterion(field=field, operator="regex", value=pattern),))

    @classmethod
    def exists(cls, field: str) -> SearchQuery:
        """Return a query matching records where a field exists."""
        return cls((SearchCriterion(field=field, operator="exists"),))

    @classmethod
    def missing(cls, field: str) -> SearchQuery:
        """Return a query matching records where a field is missing."""
        return cls((SearchCriterion(field=field, operator="missing"),))

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
        criteria: list[SearchCriterion] = []
        criteria.extend(
            SearchCriterion(field=field, operator="eq", value=value) for field, value in (where or {}).items()
        )
        criteria.extend(
            SearchCriterion(field=field, operator="contains", value=value)
            for field, value in (contains or {}).items()
        )
        criteria.extend(
            SearchCriterion(field=field, operator="match", value=value)
            for field, value in (match or {}).items()
        )
        criteria.extend(
            SearchCriterion(field=field, operator="regex", value=value)
            for field, value in (regex or {}).items()
        )
        criteria.extend(SearchCriterion(field=field, operator="exists") for field in (exists or ()))
        criteria.extend(SearchCriterion(field=field, operator="missing") for field in (missing or ()))
        return cls(tuple(criteria))

    def and_(self, *queries: SearchQuery) -> SearchQuery:
        """Return a query requiring this query and all provided queries to match."""
        criteria = list(self.criteria)
        for query in queries:
            criteria.extend(query.criteria)
        return SearchQuery(tuple(criteria))


def _normalise_field_path(dotted_path: str) -> str:
    """Translate user-friendly aliases to stored record paths."""
    if dotted_path == "metadata":
        return "user_metadata"
    if dotted_path.startswith("metadata."):
        return f"user_metadata.{dotted_path.removeprefix('metadata.')}"
    return dotted_path


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
        return expected in actual
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
    criterion: SearchCriterion,
    *,
    ignore_case: bool,
    resolution_order: Sequence[str] | None,
) -> bool:
    """Return whether a record matches one criterion."""
    resolved = resolve_field(record, criterion.field, resolution_order=resolution_order)
    if criterion.operator == "exists":
        return resolved.found
    if criterion.operator == "missing":
        return not resolved.found
    if not resolved.found:
        return False
    if criterion.operator == "eq":
        return _eq(resolved.value, criterion.value, ignore_case)
    if criterion.operator == "contains":
        return _contains(resolved.value, criterion.value, ignore_case)
    if criterion.operator == "match":
        return _match(resolved.value, str(criterion.value), ignore_case)
    if criterion.operator == "regex":
        flags = re.IGNORECASE if ignore_case else 0
        return re.search(str(criterion.value), _stringify(resolved.value), flags=flags) is not None
    raise ValueError(f"Unsupported search operator: {criterion.operator}")


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
    for criterion in active_query.criteria:
        if not _matches_criterion(
            record,
            criterion,
            ignore_case=ignore_case,
            resolution_order=resolution_order,
        ):
            return False

    return True
