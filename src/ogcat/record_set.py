"""Helpers for working with catalog search results."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, overload

from rich.table import Table

from ogcat.models import CatalogRecord, JsonValue
from ogcat.search import flatten_lookup, resolve_field

DEFAULT_RECORDSET_FIELDS = ("id", "title", "product", "species", "path")
_METADATA_NAMESPACES = ("user_metadata", "derived_metadata", "naming_metadata")


def resolve_record_field(
    record: CatalogRecord,
    field: str,
    *,
    resolution_order: Sequence[str],
) -> JsonValue:
    """Resolve one field using CLI-compatible flattened and dotted lookup rules."""
    if field == "path":
        path = record.path()
        return None if path is None else str(path)
    if field == "locator.uri":
        return record.locator.value if record.locator.kind == "uri" else None

    value = flatten_lookup(record, field, resolution_order=resolution_order)
    if isinstance(value, Path):
        return str(value)
    return value


def format_display_value(value: JsonValue) -> str:
    """Format a resolved field value for tabular CLI output."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


class CatalogRecordSet(Sequence[CatalogRecord]):
    """A lightweight, sequence-like view of catalog records.

    Args:
        records: Records to expose.
        resolution_order: Namespace order for flattened field lookup.
    """

    def __init__(
        self,
        records: Sequence[CatalogRecord],
        *,
        resolution_order: Sequence[str] | None = None,
    ) -> None:
        self._records = tuple(records)
        self._resolution_order = tuple(resolution_order or ("top_level", "user_metadata", "derived_metadata"))

    def __len__(self) -> int:
        """Return the number of records in the set."""
        return len(self._records)

    @overload
    def __getitem__(self, index: int) -> CatalogRecord: ...

    @overload
    def __getitem__(self, index: slice) -> CatalogRecordSet: ...

    def __getitem__(self, index: int | slice) -> CatalogRecord | CatalogRecordSet:
        """Return one record or a sliced record set."""
        if isinstance(index, slice):
            return CatalogRecordSet(
                self._records[index],
                resolution_order=self._resolution_order,
            )
        return self._records[index]

    def __iter__(self) -> Iterator[CatalogRecord]:
        """Iterate over records in insertion order."""
        return iter(self._records)

    def select(self, *fields: str) -> list[dict[str, JsonValue]]:
        """Return JSON-friendly rows for the selected fields."""
        return self.rows(fields)

    def rows(self, fields: Sequence[str]) -> list[dict[str, JsonValue]]:
        """Return JSON-friendly rows for the selected fields.

        Args:
            fields: Field names or dotted paths to resolve.

        Returns:
            One dictionary per record.
        """
        selected_fields = list(fields)
        return [
            {
                field: resolve_record_field(
                    record,
                    field,
                    resolution_order=self._resolution_order,
                )
                for field in selected_fields
            }
            for record in self._records
        ]

    def display_rows(self, fields: Sequence[str], *, limit: int | None = None) -> list[list[str]]:
        """Return CLI-style display rows for the selected fields.

        Args:
            fields: Field names to resolve for each row.
            limit: Maximum number of records to format. Formats all records when omitted.
        """
        records = self._records if limit is None else self._records[:limit]
        return [
            [
                format_display_value(
                    resolve_record_field(
                        record,
                        field,
                        resolution_order=self._resolution_order,
                    )
                )
                for field in fields
            ]
            for record in records
        ]

    def field_paths(self) -> list[str]:
        """Return discoverable field paths present in this record set."""
        fields: set[str] = set()
        for record in self._records:
            if record.path() is not None:
                fields.add("path")
            if record.locator.kind == "uri":
                fields.add("locator.uri")
            record_dict = record.to_dict()
            for field, value in record_dict.items():
                if field in _METADATA_NAMESPACES:
                    if isinstance(value, dict):
                        fields.update(_nested_field_paths(value, prefix=field))
                    continue
                fields.add(field)
                if isinstance(value, dict):
                    fields.update(_nested_field_paths(value, prefix=field))
            for namespace in _METADATA_NAMESPACES:
                metadata = record_dict.get(namespace)
                if isinstance(metadata, dict):
                    fields.update(_nested_field_paths(metadata, prefix=namespace))
        return sorted(fields)

    def unique_values(self, field: str) -> list[JsonValue]:
        """Return unique scalar values present for a field."""
        values: dict[str, JsonValue] = {}
        for record in self._records:
            resolved = resolve_field(record, field, resolution_order=self._resolution_order)
            if not resolved.found or not _is_scalar_json_value(resolved.value):
                continue
            key = json.dumps(resolved.value, sort_keys=True)
            values[key] = resolved.value
        return [values[key] for key in sorted(values)]

    def preview(
        self,
        *,
        fields: Sequence[str] = DEFAULT_RECORDSET_FIELDS,
        limit: int = 10,
    ) -> Table:
        """Build a Rich table preview of the selected fields."""
        table = Table(title="ogcat search results")
        field_names = list(fields)
        for field in field_names:
            table.add_column(field, overflow="fold")
        for row in self.display_rows(field_names, limit=limit):
            table.add_row(*row)
        return table

    def to_dataframe(self, fields: Sequence[str] | None = None) -> Any:
        """Convert the records to a pandas DataFrame when pandas is available.

        Args:
            fields: Optional selected fields. When omitted, full record
                dictionaries are used.

        Returns:
            ``pandas.DataFrame`` containing the selected record data.

        Raises:
            ImportError: If pandas is not installed.
        """
        try:
            pd = import_module("pandas")
        except ImportError as exc:
            raise ImportError(
                "pandas is required for CatalogRecordSet.to_dataframe(). Install pandas to use this helper."
            ) from exc

        records = [record.to_dict() for record in self._records] if fields is None else self.rows(fields)
        return pd.DataFrame.from_records(records)

    def __rich__(self) -> Table:
        """Return a Rich preview for terminals and notebooks."""
        return self.preview()


def _nested_field_paths(mapping: dict[str, JsonValue], *, prefix: str) -> set[str]:
    """Return dotted field paths for a nested JSON mapping."""
    fields: set[str] = set()
    for key, value in mapping.items():
        path = f"{prefix}.{key}"
        fields.add(path)
        if isinstance(value, dict):
            fields.update(_nested_field_paths(value, prefix=path))
    return fields


def _is_scalar_json_value(value: object) -> bool:
    """Return whether a value is safe to include in unique-value summaries."""
    return value is None or isinstance(value, str | int | float | bool)
