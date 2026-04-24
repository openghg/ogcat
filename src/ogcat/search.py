"""Search helpers for catalog records."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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


def get_dotted(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    """Resolve a dotted path inside a nested mapping."""
    current: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def flatten_lookup(
    record: CatalogRecord,
    field: str,
    resolution_order: Sequence[str] | None = None,
) -> Any:
    """Resolve a field using dotted paths or precedence-based flattened lookup."""
    record_dict = record.to_dict()

    if "." in field:
        return get_dotted(record_dict, field)

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
            return namespace[field]

    return None


def _stringify(value: Any) -> str:
    return "" if value is None else str(value)


def _eq(left: Any, right: Any, ignore_case: bool) -> bool:
    if ignore_case and isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def matches_record(
    record: CatalogRecord,
    *,
    where: dict[str, object] | None,
    contains: dict[str, str] | None,
    regex: dict[str, str] | None,
    ignore_case: bool,
    resolution_order: Sequence[str] | None = None,
) -> bool:
    """Return whether a record matches the provided filters."""
    where = where or {}
    contains = contains or {}
    regex = regex or {}

    for field, expected in where.items():
        actual = flatten_lookup(record, field, resolution_order=resolution_order)
        if not _eq(actual, expected, ignore_case):
            return False

    for field, expected_substring in contains.items():
        actual = _stringify(flatten_lookup(record, field, resolution_order=resolution_order))
        haystack = actual.casefold() if ignore_case else actual
        needle = expected_substring.casefold() if ignore_case else expected_substring
        if needle not in haystack:
            return False

    for field, pattern in regex.items():
        actual = _stringify(flatten_lookup(record, field, resolution_order=resolution_order))
        flags = re.IGNORECASE if ignore_case else 0
        if re.search(pattern, actual, flags=flags) is None:
            return False

    return True
