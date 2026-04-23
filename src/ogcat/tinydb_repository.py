"""TinyDB-backed repository implementation.

This module includes a very small JSON-file fallback so the skeleton remains runnable
in environments where TinyDB is not yet installed. Codex can simplify this later if desired.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ogcat.models import CatalogRecord

try:
    from tinydb import Query, TinyDB  # type: ignore
except ImportError:  # pragma: no cover
    Query = None
    TinyDB = None


class TinyDbCatalogRepository:
    """TinyDB-backed catalog repository with a JSON fallback for the skeleton."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        if TinyDB is not None:
            self._db: TinyDB | None = TinyDB(db_path)
        else:
            self._db = None
            if not db_path.exists():
                db_path.write_text("[]\n", encoding="utf-8")

    def _load_json_records(self) -> list[dict[str, Any]]:
        if self._db is not None:
            raise RuntimeError("JSON fallback should not be used when TinyDB is available.")
        return json.loads(self._db_path.read_text(encoding="utf-8"))

    def _write_json_records(self, records: list[dict[str, Any]]) -> None:
        if self._db is not None:
            raise RuntimeError("JSON fallback should not be used when TinyDB is available.")
        self._db_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def insert(self, record: CatalogRecord) -> None:
        """Insert a new record."""
        if self._db is not None:
            self._db.insert(record.to_dict())
            return
        records = self._load_json_records()
        records.append(record.to_dict())
        self._write_json_records(records)

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        if self._db is not None:
            query = Query()
            result = self._db.get(query.id == record_id)
            if result is None:
                return None
            return CatalogRecord.from_dict(result)

        for item in self._load_json_records():
            if item.get("id") == record_id:
                return CatalogRecord.from_dict(item)
        return None

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""
        if self._db is not None:
            query = Query()
            self._db.update(record.to_dict(), query.id == record.id)
            return

        records = self._load_json_records()
        for idx, item in enumerate(records):
            if item.get("id") == record.id:
                records[idx] = record.to_dict()
                self._write_json_records(records)
                return
        raise KeyError(f"Record not found: {record.id}")

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
        if self._db is not None:
            return [CatalogRecord.from_dict(item) for item in self._db.all()]
        return [CatalogRecord.from_dict(item) for item in self._load_json_records()]
