"""Local writer exclusion, complete snapshots, and failed-write preservation."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from ogcat import Catalog, CatalogSpec
from ogcat.models import CatalogRecord
from ogcat.persistence import write_json
from ogcat.tinydb_repository import TinyDbCatalogRepository


def test_writer_lock_rejects_another_process_and_releases_on_close(tmp_path: Path) -> None:
    """A stable sidecar excludes another process until the writer closes."""
    path = tmp_path / "db.json"
    writer = TinyDbCatalogRepository(path)
    code = """
from pathlib import Path
import sys
from ogcat.tinydb_repository import TinyDbCatalogRepository
try:
    repository = TinyDbCatalogRepository(Path(sys.argv[1]))
except RuntimeError as exc:
    assert 'already has a writer' in str(exc)
    sys.exit(23)
repository.close()
"""
    assert subprocess.run([sys.executable, "-c", code, str(path)], check=False).returncode == 23
    lock_path = path.with_name("db.json.lock")
    lock_inode = lock_path.stat().st_ino
    writer.close()
    assert subprocess.run([sys.executable, "-c", code, str(path)], check=False).returncode == 0
    assert lock_path.stat().st_ino == lock_inode


def test_open_reader_observes_each_replaced_snapshot(tmp_path: Path) -> None:
    """An existing reader refreshes repeated native searches after each mutation."""
    writer = TinyDbCatalogRepository(tmp_path / "db.json")
    reader = TinyDbCatalogRepository(tmp_path / "db.json", read_only=True)
    try:
        assert reader.search(where={"user_metadata.species": "co2"}) == []
        record = writer.insert(
            CatalogRecord(catalog="test", time_added="2026-10-02", user_metadata={"species": "co2"})
        )
        assert reader.search(where={"user_metadata.species": "co2"}) == [record]
        changed = replace(record, user_metadata={"species": "ch4"})
        writer.update(changed)
        assert reader.search(where={"user_metadata.species": "co2"}) == []
        assert reader.get(str(record.id)) == changed
        writer.delete(str(record.id))
        assert reader.all() == []
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("failure", ["serialization", "fsync", "replace"])
def test_failed_json_write_preserves_original_and_removes_temporary(tmp_path: Path, failure: str) -> None:
    """Failures before publication preserve old durable bytes and remove scratch files."""
    path = tmp_path / "db.json"
    write_json(path, {"original": {}})
    original = path.read_bytes()
    if failure == "serialization":
        with pytest.raises(TypeError):
            write_json(path, {"invalid": object()})
    else:
        with (
            patch(f"ogcat.persistence.os.{failure}", side_effect=OSError("injected")),
            pytest.raises(OSError, match="injected"),
        ):
            write_json(path, {"changed": {}})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_failed_record_and_spec_writes_preserve_durable_state(tmp_path: Path) -> None:
    """Atomic publication failures propagate without changing records or the active spec."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test")) as catalog:
        record = catalog.add_reference(tmp_path / "external.nc")
        db_path = catalog.root / catalog.spec.db_path
        spec_path = catalog.root / "catalog.json"
        db_before, spec_before = db_path.read_bytes(), spec_path.read_bytes()
        with patch("ogcat.persistence.os.replace", side_effect=OSError("injected")):
            with pytest.raises(OSError, match="injected"):
                catalog.repository.update(replace(record, user_metadata={"changed": True}))
            with pytest.raises(OSError, match="injected"):
                catalog.update_spec(catalog_name="changed")
        assert db_path.read_bytes() == db_before
        assert spec_path.read_bytes() == spec_before
        assert catalog.spec.catalog_name == "test"
        assert catalog.get(record.id) == record


@pytest.mark.parametrize(
    "payload",
    [
        "null",
        "[]",
        "false",
        "123",
        '"text"',
        "{broken",
        '{"_default": []}',
        '{"_default": {"1": {}, "01": {}}}',
        '{"_default": {"+1": {}}}',
        '{"_default": {"word": {}}}',
        '{"_default": {"1": null}}',
        '{"_default": {"1": []}}',
    ],
)
def test_corrupt_database_rejected_and_constructor_releases_lock(tmp_path: Path, payload: str) -> None:
    """Malformed or non-object JSON cannot be opened or leave a writer lock held."""
    path = tmp_path / "db.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        TinyDbCatalogRepository(path)
    assert path.read_text() == payload
    path.write_text("{}")
    repository = TinyDbCatalogRepository(path)
    repository.close()


def test_legacy_zero_document_id_remains_usable(tmp_path: Path) -> None:
    """Canonical zero IDs accepted by TinyDB remain readable alongside new positive IDs."""
    path = tmp_path / "db.json"
    path.write_text('{"_default": {"0": {"catalog": "test", "time_added": "2026-10-02"}}}')
    repository = TinyDbCatalogRepository(path)
    try:
        legacy = repository.get("0")
        assert legacy is not None
        assert legacy.id == "0"
        inserted = repository.insert(CatalogRecord(catalog="test", time_added="2026-10-02"))
        assert inserted.id == "1"
        assert repository.get("0") == legacy
    finally:
        repository.close()


def test_storage_rejects_invalid_document_write_without_changing_bytes(tmp_path: Path) -> None:
    """Direct storage writes cannot introduce numeric aliases or malformed document values."""
    path = tmp_path / "db.json"
    repository = TinyDbCatalogRepository(path)
    original = path.read_bytes()
    try:
        storage = repository._db.storage
        assert storage is not None
        for data in ({"_default": {"01": {}}}, {"_default": {"1": None}}):
            with pytest.raises(ValueError):
                storage.write(data)
            assert path.read_bytes() == original
    finally:
        repository.close()


@pytest.mark.parametrize("read_only", [False, True])
def test_missing_database_is_not_recreated_by_catalog_open(tmp_path: Path, read_only: bool) -> None:
    """Existing catalog specs require their database even for writable opens."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test")) as catalog:
        root = catalog.root
        db_path = root / catalog.spec.db_path
    db_path.unlink()
    with pytest.raises(FileNotFoundError):
        Catalog.open(root, read_only=read_only)
    assert not db_path.exists()


def test_opened_database_disappearance_raises_instead_of_recreating(tmp_path: Path) -> None:
    """Reads and mutations refuse to recreate a database removed during its lifetime."""
    path = tmp_path / "db.json"
    repository = TinyDbCatalogRepository(path)
    try:
        path.unlink()
        with pytest.raises(FileNotFoundError):
            repository.all()
        with pytest.raises(FileNotFoundError):
            repository.insert(CatalogRecord(catalog="test", time_added="2026-10-02"))
        assert not path.exists()
    finally:
        repository.close()


def test_legacy_empty_database_remains_readable_without_writes(tmp_path: Path) -> None:
    """An empty legacy database is accepted and read-only opens create no sidecar."""
    path = tmp_path / "db.json"
    path.write_bytes(b"")
    reader = TinyDbCatalogRepository(path, read_only=True)
    try:
        assert reader.all() == []
        assert path.read_bytes() == b""
        assert list(tmp_path.iterdir()) == [path]
    finally:
        reader.close()


def test_failed_create_preserves_existing_files_and_releases_writer(tmp_path: Path) -> None:
    """A failed creation removes its new DB, preserves user files, and can be retried."""
    root = tmp_path / "catalog"
    root.mkdir()
    path = root / "db.json"
    user_file = root / "notes.txt"
    user_file.write_text("keep")
    with (
        patch("ogcat.catalog.write_json", side_effect=OSError("injected")),
        pytest.raises(OSError, match="injected"),
    ):
        Catalog.create(root, CatalogSpec(catalog_name="test"))
    assert not (root / "catalog.json").exists()
    assert not path.exists()
    assert user_file.read_text() == "keep"
    with Catalog.create(root, CatalogSpec(catalog_name="retry")) as catalog:
        assert catalog.repository.all() == []


def test_create_rejects_existing_database_without_a_catalog(tmp_path: Path) -> None:
    """A stray existing DB cannot silently become part of a new catalog."""
    path = tmp_path / "db.json"
    path.write_text('{"unrelated": {}}')
    with pytest.raises(FileExistsError):
        Catalog.create(tmp_path, CatalogSpec(catalog_name="new"))
    assert path.read_text() == '{"unrelated": {}}'
    assert not (tmp_path / "catalog.json").exists()


def test_atomic_replacement_preserves_existing_file_permissions(tmp_path: Path) -> None:
    """Replacing JSON retains group-readable database and spec permission bits."""
    path = tmp_path / "db.json"
    path.write_text("{}")
    path.chmod(0o640)
    write_json(path, {"_default": {}})
    assert path.stat().st_mode & 0o777 == 0o640


def test_exclusive_publication_succeeds_when_temporary_cleanup_fails(tmp_path: Path) -> None:
    """A published complete file must not be reported as failed because cleanup fails."""
    path = tmp_path / "catalog.json"
    with patch("ogcat.persistence.Path.unlink", side_effect=OSError("injected cleanup")):
        write_json(path, {"catalog_name": "new"}, exclusive=True)
    assert CatalogSpec.read(path).catalog_name == "new"


def test_open_refreshes_spec_after_acquiring_repository(tmp_path: Path) -> None:
    """The spec used by an opened writer is the one read after acquiring its lock."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="old")) as catalog:
        root = catalog.root
    from ogcat.catalog import _open_repository

    def open_and_change(*args: object, **kwargs: object) -> TinyDbCatalogRepository:
        """Simulate a completed spec update between the initial read and lock acquisition."""
        repository = _open_repository(*args, **kwargs)  # type: ignore[arg-type]
        CatalogSpec(catalog_name="new").write(root / "catalog.json")
        return repository

    with (
        patch("ogcat.catalog._open_repository", side_effect=open_and_change),
        Catalog.open(root) as reopened,
    ):
        assert reopened.spec.catalog_name == "new"


def test_open_rejects_changed_database_path_and_releases_writer(tmp_path: Path) -> None:
    """A spec changed during opening cannot attach metadata to the wrong database."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test")) as catalog:
        root = catalog.root
    from ogcat.catalog import _open_repository

    def open_and_change(*args: object, **kwargs: object) -> TinyDbCatalogRepository:
        """Simulate replacement of the spec with a different database path."""
        repository = _open_repository(*args, **kwargs)  # type: ignore[arg-type]
        CatalogSpec(catalog_name="test", db_path="other.json").write(root / "catalog.json")
        return repository

    with (
        patch("ogcat.catalog._open_repository", side_effect=open_and_change),
        pytest.raises(ValueError, match="database path changed"),
    ):
        Catalog.open(root)
    repository = TinyDbCatalogRepository(root / "db.json")
    repository.close()
    assert not (root / "other.json").exists()


def test_open_construction_failure_releases_writer_without_removing_database(tmp_path: Path) -> None:
    """Failure after repository creation releases its lock and preserves the existing DB."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test")) as catalog:
        root = catalog.root
    original = (root / "db.json").read_bytes()
    with (
        patch("ogcat.catalog._coerce_audit_sink", side_effect=ValueError("injected")),
        pytest.raises(ValueError, match="injected"),
    ):
        Catalog.open(root)
    with Catalog.open(root) as reopened:
        assert reopened.repository.all() == []
    assert (root / "db.json").read_bytes() == original
