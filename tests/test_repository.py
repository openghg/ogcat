"""Repository behavior tests."""

from __future__ import annotations

from ogcat.models import CatalogRecord
from ogcat.tinydb_repository import TinyDbCatalogRepository


def test_repository_insert_get_update_and_all(tmp_path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    record = CatalogRecord(
        id="rec_000001",
        catalog="fluxes",
        stored_abspath="/tmp/catalog/files/example.nc",
        stored_relpath="files/example.nc",
        storage_mode="copy",
        time_added="2026-04-23T12:00:00Z",
        original_path="/tmp/source/example.nc",
        original_filename="example.nc",
        suffixes=[".nc"],
        user_metadata={"species": "CO2"},
        derived_metadata={"checksum": "abc123"},
        naming_metadata={"resolved_filename": "example.nc"},
    )

    repository.insert(record)

    stored = repository.get(record.id)
    assert stored == record
    assert repository.all() == [record]

    updated = CatalogRecord(
        id=record.id,
        catalog=record.catalog,
        stored_abspath=record.stored_abspath,
        stored_relpath=record.stored_relpath,
        storage_mode=record.storage_mode,
        time_added=record.time_added,
        original_path=record.original_path,
        original_filename=record.original_filename,
        suffixes=record.suffixes,
        user_metadata={"species": "CH4"},
        derived_metadata=record.derived_metadata,
        naming_metadata=record.naming_metadata,
    )
    repository.update(updated)

    assert repository.get(record.id) == updated
