"""Record tombstone, restore, and purge behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogRecord, CatalogSpec


def _source_file(tmp_path: Path, name: str = "source.nc") -> Path:
    """Create a small source file for lifecycle tests."""
    source = tmp_path / name
    source.write_text("dummy", encoding="utf-8")
    return source


def _record_id(record: CatalogRecord) -> str:
    """Return a persisted test record id."""
    assert record.id is not None
    return record.id


def test_delete_tombstones_record_and_hides_default_search(tmp_path: Path) -> None:
    """Deleting a record should tombstone it without dropping locator metadata."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)

    deleted = catalog.delete(record_id, reason="superseded")

    assert deleted.id == record_id
    assert deleted.status == "deleted"
    assert deleted.locator == record.locator
    assert deleted.artifacts == record.artifacts
    assert deleted.user_metadata == record.user_metadata
    assert deleted.lifecycle_metadata["delete_reason"] == "superseded"
    assert "delete_operation_id" in deleted.lifecycle_metadata
    assert "deleted_at" in deleted.lifecycle_metadata
    assert catalog.get(record_id) == deleted
    assert catalog.path(record_id) == record.path()
    assert catalog.search(where={"species": "CO2"}) == []
    assert catalog.search(where={"species": "CO2"}, include_deleted=True).ids == [record_id]
    assert catalog.search(only_deleted=True).ids == [record_id]
    with pytest.raises(ValueError, match="found no records"):
        catalog.get_one(where={"species": "CO2"})


def test_restore_returns_record_to_default_search(tmp_path: Path) -> None:
    """Restoring a deleted record should preserve its id and make it searchable."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)
    catalog.delete(record_id)

    restored = catalog.restore(record_id, reason="needed again")

    assert restored.id == record_id
    assert restored.status == "active"
    assert restored.lifecycle_metadata["restore_reason"] == "needed again"
    assert "restore_operation_id" in restored.lifecycle_metadata
    assert "restored_at" in restored.lifecycle_metadata
    assert catalog.search(where={"species": "CO2"}).ids == [record_id]
    assert catalog.search(only_deleted=True) == []


def test_old_records_load_as_active() -> None:
    """Legacy serialized records without lifecycle fields should load as active."""
    record = CatalogRecord.from_dict(
        {
            "id": "1",
            "catalog": "files",
            "time_added": "2026-04-23T12:00:00Z",
            "locator": {"kind": "uri", "value": "s3://bucket/example.zarr", "relative_path": None},
        }
    )

    assert record.status == "active"
    assert record.lifecycle_metadata == {}
    assert record.to_dict()["status"] == "active"


def test_active_summaries_exclude_deleted_records_by_default(tmp_path: Path) -> None:
    """Catalog summaries and field helpers should use active records by default."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    active = catalog.add_reference(
        ArtifactLocator(kind="uri", value="s3://bucket/active.zarr"),
        metadata={"species": "CO2", "active_only": "yes"},
    )
    deleted = catalog.add_reference(
        ArtifactLocator(kind="uri", value="s3://bucket/deleted.zarr"),
        metadata={"species": "CH4", "deleted_only": "yes"},
    )
    catalog.delete(_record_id(deleted))

    description = catalog.describe()

    assert description["record_count"] == 1
    assert description["deleted_record_count"] == 1
    assert catalog.describe(include_deleted=True)["record_count"] == 2
    assert "user_metadata.active_only" in catalog.list_record_fields()
    assert "user_metadata.deleted_only" not in catalog.list_record_fields()
    assert "user_metadata.deleted_only" in catalog.list_record_fields(include_deleted=True)
    assert catalog.unique_values("species") == ["CO2"]
    assert sorted(str(value) for value in catalog.unique_values("species", include_deleted=True)) == [
        "CH4",
        "CO2",
    ]
    assert catalog.get_one(where={"species": "CO2"}).id == active.id


def test_plan_view_excludes_deleted_records(tmp_path: Path) -> None:
    """Replica view planning should not include tombstoned records."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    catalog.delete(_record_id(record))

    plan = catalog.plan_view(tmp_path / "view", "{product}/{id}_{original_filename}")

    assert plan.items == ()


def test_delete_rolls_back_with_caller_owned_transaction(tmp_path: Path) -> None:
    """Caller-owned transactions should restore tombstones when not committed."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)

    with catalog.transaction() as transaction:
        deleted = catalog.delete(record_id, transaction=transaction)
        assert deleted.status == "deleted"
        stored = catalog.get(record_id)
        assert stored is not None
        assert stored.status == "deleted"

    rolled_back = catalog.get(record_id)
    assert rolled_back is not None
    assert rolled_back.status == "active"
    assert catalog.search(where={"species": "CO2"}).ids == [record_id]


def test_purge_removes_managed_artifacts_and_hard_deletes_record(tmp_path: Path) -> None:
    """Purging a tombstone should remove catalog-managed files and the record."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "managed.nc"), metadata={"species": "CO2"})
    record_id = _record_id(record)
    artifact_paths = [
        artifact.locator.as_path()
        for artifact in record.artifacts
        if artifact.locator is not None and artifact.locator.as_path() is not None
    ]
    assert artifact_paths
    assert all(path.exists() or path.is_symlink() for path in artifact_paths if path is not None)
    catalog.delete(record_id)

    catalog.purge(record_id)

    assert catalog.get(record_id) is None
    for path in artifact_paths:
        assert path is not None
        assert not path.exists()
        assert not path.is_symlink()


def test_purge_skips_external_path_artifacts(tmp_path: Path) -> None:
    """Purge should skip user-owned paths outside managed catalog roots."""
    external = _source_file(tmp_path, "external.nc")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_reference(external, metadata={"species": "CO2"})
    record_id = _record_id(record)
    catalog.delete(record_id)

    catalog.purge(record_id)

    assert external.exists()
    assert catalog.get(record_id) is None
    skip_events = catalog.audit_events(event_type="purge_artifact")
    assert any(event.details["purge_action"] == "skipped" for event in skip_events)


def test_purge_skips_reference_paths_under_managed_roots(tmp_path: Path) -> None:
    """Reference-only records must not delete user-owned paths under catalog roots."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    referenced = catalog.root / catalog.spec.files_root / "user-owned.nc"
    referenced.write_text("user data", encoding="utf-8")
    record = catalog.add_reference(referenced, metadata={"species": "CO2"})
    record_id = _record_id(record)
    catalog.delete(record_id)

    catalog.purge(record_id)

    assert referenced.exists()
    assert referenced.read_text(encoding="utf-8") == "user data"
    assert catalog.get(record_id) is None
    skip_events = catalog.audit_events(event_type="purge_artifact")
    assert any(
        event.details["reason"] == "record storage mode is reference"
        and event.details["purge_action"] == "skipped"
        for event in skip_events
    )


def test_purge_requires_deleted_record_unless_forced(tmp_path: Path) -> None:
    """Active records should not be purged unless force is explicit."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)

    with pytest.raises(ValueError, match="must be deleted before purge"):
        catalog.purge(record_id)

    assert catalog.get(record_id) is not None


def test_delete_restore_and_purge_emit_audit_events(tmp_path: Path) -> None:
    """Lifecycle operations should write structured audit events."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        audit_user_id="alice",
    )
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)

    catalog.delete(record_id, reason="bad input")
    catalog.restore(record_id, reason="false alarm")
    catalog.delete(record_id)
    catalog.purge(record_id)

    delete_lifecycle = [
        event
        for event in catalog.audit_events(record_id=record_id, event_type="lifecycle")
        if event.details["status_after"] == "deleted"
    ]
    restore_lifecycle = [
        event
        for event in catalog.audit_events(record_id=record_id, event_type="lifecycle")
        if event.details["status_after"] == "active"
    ]
    purge_events = catalog.audit_events(record_id=record_id, event_type="purge")

    assert delete_lifecycle
    assert restore_lifecycle
    assert purge_events
    assert all(event.user_id == "alice" for event in [*delete_lifecycle, *restore_lifecycle, *purge_events])
    assert delete_lifecycle[0].details["reason_present"] is True
    assert "artifact_summaries" in delete_lifecycle[0].details
