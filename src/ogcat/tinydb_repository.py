"""TinyDB-backed repository implementation."""

from __future__ import annotations

from pathlib import Path

from tinydb import Query, TinyDB

from ogcat.models import CatalogRecord


class TinyDbCatalogRepository:
    """TinyDB-backed catalog repository."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(db_path)

    def allocate_record_ids(self, count: int = 1) -> list[str]:
        """Allocate one or more new ids aligned with TinyDB doc_ids."""
        if count < 1:
            return []
        start = 1
        for document in self._db.all():
            start = max(start, document.doc_id + 1)
        return [str(number) for number in range(start, start + count)]

    def insert(self, record: CatalogRecord) -> None:
        """Insert a new record."""
        doc_id = self._db.insert(record.to_dict())
        expected_id = str(doc_id)
        if record.id != expected_id:
            raise ValueError(
                f"Record id {record.id!r} does not match allocated TinyDB doc_id {expected_id!r}"
            )

    def insert_many(self, records: list[CatalogRecord]) -> None:
        """Insert multiple records."""
        if not records:
            return
        doc_ids = self._db.insert_multiple([record.to_dict() for record in records])
        expected_ids = [str(doc_id) for doc_id in doc_ids]
        actual_ids = [record.id for record in records]
        if actual_ids != expected_ids:
            raise ValueError(
                f"Record ids {actual_ids!r} do not match allocated TinyDB doc_ids {expected_ids!r}"
            )

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        if record_id.isdigit():
            result = self._db.get(doc_id=int(record_id))
        else:
            query = Query()
            result = self._db.get(query.id == record_id)
        if result is None:
            return None
        return CatalogRecord.from_dict(result)

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""
        if record.id.isdigit():
            updated_doc_ids = self._db.update(record.to_dict(), doc_ids=[int(record.id)])
        else:
            query = Query()
            updated_doc_ids = self._db.update(record.to_dict(), query.id == record.id)
        if not updated_doc_ids:
            raise KeyError(f"Record not found: {record.id}")

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
        return [CatalogRecord.from_dict(item) for item in self._db.all()]
