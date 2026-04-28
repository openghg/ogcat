"""Helpers for working with catalog search results."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, overload

from rich.table import Table

from ogcat.models import CatalogRecord, JsonValue
from ogcat.search import flatten_lookup

DEFAULT_RECORDSET_FIELDS = ("id", "title", "product", "species", "path")


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
    """A lightweight, sequence-like view of catalog records."""

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
        """Return JSON-friendly rows for the selected fields."""
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

    def display_rows(self, fields: Sequence[str]) -> list[list[str]]:
        """Return CLI-style display rows for the selected fields."""
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
            for record in self._records
        ]

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
        for row in self.display_rows(field_names[:])[:limit]:
            table.add_row(*row)
        return table

    def to_dataframe(self, fields: Sequence[str] | None = None) -> Any:
        """Convert the records to a pandas DataFrame when pandas is available."""
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
