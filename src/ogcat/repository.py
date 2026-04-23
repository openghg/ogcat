"""Repository abstractions."""

from __future__ import annotations

from typing import Protocol

from ogcat.models import CatalogRecord


class CatalogRepository(Protocol):
    """Abstract storage for catalog records."""

    def insert(self, record: CatalogRecord) -> None:
        """Insert a new record."""

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
