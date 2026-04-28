"""Repository abstractions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ogcat.models import CatalogRecord
from ogcat.search import SearchQuery


class CatalogRepository(Protocol):
    """Abstract storage for catalog records."""

    def insert(self, record: CatalogRecord) -> CatalogRecord:
        """Insert a new record and return it with its repository-assigned id."""
        ...

    def insert_many(self, records: list[CatalogRecord]) -> list[CatalogRecord]:
        """Insert multiple records and return them with repository-assigned ids."""
        ...

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        ...

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""
        ...

    def delete(self, record_id: str) -> None:
        """Delete an existing record."""
        ...

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
        ...

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
        ...
