"""TinyDB-backed repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from tinydb import Query, TinyDB

from ogcat.models import CatalogRecord, JsonValue
from ogcat.search import SearchQuery, matches_record


class TinyDbCatalogRepository:
    """TinyDB-backed catalog repository."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(db_path)

    def insert(self, record: CatalogRecord) -> CatalogRecord:
        """Insert a new record and return it with its TinyDB doc_id."""
        payload = record.to_dict()
        payload.pop("id", None)
        doc_id = self._db.insert(payload)
        return replace(record, id=str(doc_id))

    def insert_many(self, records: list[CatalogRecord]) -> list[CatalogRecord]:
        """Insert multiple records and return them with their TinyDB doc_ids."""
        if not records:
            return []
        payloads = []
        for record in records:
            payload = record.to_dict()
            payload.pop("id", None)
            payloads.append(payload)
        doc_ids = self._db.insert_multiple(payloads)
        return [replace(record, id=str(doc_id)) for record, doc_id in zip(records, doc_ids, strict=True)]

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        if record_id.isdigit():
            result = self._db.get(doc_id=int(record_id))
        else:
            query = Query()
            result = self._db.get(query.id == record_id)
        if result is None:
            return None
        return self._record_from_document(result)

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""
        if record.id is None:
            raise ValueError("Cannot update a record without an id.")
        if record.id.isdigit():
            updated_doc_ids = self._db.update(record.to_dict(), doc_ids=[int(record.id)])
        else:
            query = Query()
            updated_doc_ids = self._db.update(record.to_dict(), query.id == record.id)
        if not updated_doc_ids:
            raise KeyError(f"Record not found: {record.id}")

    def delete(self, record_id: str) -> None:
        """Delete an existing record."""
        if record_id.isdigit():
            removed_doc_ids = self._db.remove(doc_ids=[int(record_id)])
        else:
            query = Query()
            removed_doc_ids = self._db.remove(query.id == record_id)
        if not removed_doc_ids:
            raise KeyError(f"Record not found: {record_id}")

    def search(
        self,
        *,
        query: SearchQuery | None = None,
        where: dict[str, object] | None = None,
        contains: dict[str, object] | None = None,
        regex: dict[str, str] | None = None,
        match: dict[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        resolution_order: Sequence[str] | None = None,
    ) -> list[CatalogRecord]:
        """Search records."""
        return [
            record
            for record in self.all()
            if matches_record(
                record,
                query=query,
                where=where,
                contains=contains,
                regex=regex,
                match=match,
                exists=exists,
                missing=missing,
                ignore_case=ignore_case,
                resolution_order=resolution_order,
            )
        ]

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
        return [self._record_from_document(item) for item in self._db.all()]

    def _record_from_document(self, document: Any) -> CatalogRecord:
        """Build a record, recovering the id from TinyDB doc_id when needed."""
        data = dict(cast(dict[str, JsonValue], document))
        if data.get("id") is None:
            data["id"] = str(document.doc_id)
        return CatalogRecord.from_dict(data)
