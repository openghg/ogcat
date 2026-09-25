"""Read-only catalog behavior tests."""

from pathlib import Path

import pytest

from ogcat import Catalog, CatalogSpec


def test_read_only_catalog_queries_without_changing_files(tmp_path: Path) -> None:
    """Opening for reading preserves the catalog and its audit files."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example"))
    record = catalog.add_reference(tmp_path / "external.nc", metadata={"species": "co2"})
    db_path = catalog.root / catalog.spec.db_path
    before_db = db_path.read_bytes()
    audit_path = catalog.root / ".ogcat" / "logs" / "events.jsonl"
    before_audit = audit_path.read_bytes() if audit_path.exists() else None

    reader = Catalog.open(catalog.root, read_only=True)

    assert reader.read_only
    assert reader.get(record.id) == record
    assert reader.get_one(where={"species": "co2"}) == record
    assert db_path.read_bytes() == before_db
    assert (audit_path.read_bytes() if audit_path.exists() else None) == before_audit


def test_read_only_catalog_rejects_mutations_before_writing_files(tmp_path: Path) -> None:
    """Managed ingest and record/spec changes fail before side effects."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example"))
    source = tmp_path / "source.nc"
    source.write_text("data")
    existing = catalog.add_reference(source)
    db_path = catalog.root / catalog.spec.db_path
    before_db = db_path.read_bytes()
    reader = Catalog.open(catalog.root, read_only=True)

    with pytest.raises(PermissionError, match="read-only"):
        reader.add_file(source, operation="move")
    with pytest.raises(PermissionError, match="read-only"):
        reader.add_reference(source)
    with pytest.raises(PermissionError, match="read-only"):
        reader.delete(existing.id)
    with pytest.raises(PermissionError, match="read-only"):
        reader.update_spec(catalog_name="changed")

    assert source.read_text() == "data"
    assert db_path.read_bytes() == before_db
    assert reader.get(existing.id) == existing
