"""Catalog resource contexts close handles without changing transaction semantics."""

import os
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from tinydb.storages import JSONStorage

from ogcat import Catalog, CatalogSpec
from ogcat.repository import CatalogRepository
from ogcat.tinydb_repository import TinyDbCatalogRepository


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_catalog_context_releases_handle_and_preserves_records(
    tmp_path: Path, read_only: bool, fail: bool
) -> None:
    """Both context exit paths close real file descriptors and keep committed data."""
    root = tmp_path / "catalog"
    with Catalog.create(root, CatalogSpec(catalog_name="example")) as catalog:
        record = catalog.add_reference(tmp_path / "data.nc", metadata={"species": "co2"})

    opened = Catalog.open(root, read_only=read_only)
    repository = cast(TinyDbCatalogRepository, opened.repository)
    descriptor = cast(JSONStorage, repository._db.storage)._handle.fileno()
    try:
        with opened as active:
            assert active is opened
            assert active.get(record.id) == record
            # Warm the native TinyDB query cache before closing it.
            assert list(active.search(where={"user_metadata.species": "co2"})) == [record]
            if fail:
                raise ValueError("caller failed")
    except ValueError as exc:
        assert fail
        assert str(exc) == "caller failed"
    else:
        assert not fail

    with pytest.raises(OSError):
        os.fstat(descriptor)
    opened.close()
    with pytest.raises(RuntimeError, match="closed"):
        opened.search(where={"user_metadata.species": "co2"})
    with pytest.raises(RuntimeError, match="closed"), opened:
        pass
    with Catalog.open(root, read_only=True) as reopened:
        assert reopened.get(record.id) == record


def test_inner_transaction_rolls_back_before_catalog_closes(tmp_path: Path) -> None:
    """Explicit rollback removes staged records while earlier completed adds survive."""
    root = tmp_path / "catalog"
    with (
        pytest.raises(ValueError, match="caller failed"),
        Catalog.create(root, CatalogSpec(catalog_name="example")) as catalog,
    ):
        committed = catalog.add_reference(tmp_path / "keep.nc")
        with catalog.transaction() as transaction:
            catalog.add_reference(tmp_path / "discard.nc", transaction=transaction)
            raise ValueError("caller failed")

    with Catalog.open(root, read_only=True) as reopened:
        assert list(reopened.search()) == [committed]


def test_closed_catalog_rejects_writes_before_side_effects(tmp_path: Path) -> None:
    """Closing prevents managed moves, record mutations, and specification writes."""
    source = tmp_path / "source.nc"
    source.write_text("data")
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example")) as catalog:
        record = catalog.add_reference(source)
    db_path = catalog.root / catalog.spec.db_path
    spec_path = catalog.root / "catalog.json"
    before_db, before_spec = db_path.read_bytes(), spec_path.read_bytes()

    with pytest.raises(RuntimeError, match="closed"):
        catalog.add_file(source, operation="move")
    with pytest.raises(RuntimeError, match="closed"):
        catalog.delete(record.id)
    with pytest.raises(RuntimeError, match="closed"):
        catalog.update_spec(catalog_name="changed")

    assert source.read_text() == "data"
    assert db_path.read_bytes() == before_db
    assert spec_path.read_bytes() == before_spec


@pytest.mark.parametrize("method", ["get", "search", "describe", "list_record_fields", "unique_values"])
def test_closed_catalog_guards_custom_repository_reads(tmp_path: Path, method: str) -> None:
    """Resource-free repositories need no close method and receive no calls after close."""
    repository = Mock(spec_set=CatalogRepository)
    catalog = Catalog(tmp_path, CatalogSpec(catalog_name="example"), cast(CatalogRepository, repository))
    catalog.close()
    catalog.close()

    args = ("species",) if method in {"get", "unique_values"} else ()
    with pytest.raises(RuntimeError, match="closed"):
        getattr(catalog, method)(*args)
    assert not repository.mock_calls
