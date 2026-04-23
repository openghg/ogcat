"""Search helpers for catalog records."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from ogcat.models import CatalogRecord


_RESERVED_FIELDS = {
    "id",
    "catalog",
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


def flatten_lookup(record: CatalogRecord, field: str) -> Any:
    """Resolve a field using dotted paths or precedence-based flattened lookup."""
    record_dict = record.to_dict()

    if "." in field:
        return get_dotted(record_dict, field)

    if field in _RESERVED_FIELDS:
        return record_dict.get(field)
    if field in record.user_metadata:
        return record.user_metadata.get(field)
    if field in record.derived_metadata:
        return record.derived_metadata.get(field)
    return record_dict.get(field)


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
) -> bool:
    """Return whether a record matches the provided filters."""
    where = where or {}
    contains = contains or {}
    regex = regex or {}

    for field, expected in where.items():
        actual = flatten_lookup(record, field)
        if not _eq(actual, expected, ignore_case):
            return False

    for field, expected_substring in contains.items():
        actual = _stringify(flatten_lookup(record, field))
        haystack = actual.casefold() if ignore_case else actual
        needle = expected_substring.casefold() if ignore_case else expected_substring
        if needle not in haystack:
            return False

    for field, pattern in regex.items():
        actual = _stringify(flatten_lookup(record, field))
        flags = re.IGNORECASE if ignore_case else 0
        if re.search(pattern, actual, flags=flags) is None:
            return False

    return True
