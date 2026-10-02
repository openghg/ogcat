"""Record tombstone, restore, and purge behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import ogcat.operation_runner as operation_runner
from ogcat import (
    ArtifactDescriptor,
    ArtifactLocator,
    Catalog,
    CatalogRecord,
    CatalogSpec,
    PurgeIncompleteError,
)


def _source_file(tmp_path: Path, name: str = "source.nc") -> Path:
    """Create a small source file for lifecycle tests."""
    source = tmp_path / name
    source.write_text("dummy", encoding="utf-8")
    return source


def _record_id(record: CatalogRecord) -> str:
    """Return a persisted test record id."""
    assert record.id is not None
    return record.id


def _add_managed_secondary_artifact(catalog: Catalog, record_id: str, name: str) -> Path:
    """Attach an extra managed artifact descriptor to a stored record."""
    secondary_path = catalog.root / catalog.spec.objects_root / name
    secondary_path.parent.mkdir(parents=True, exist_ok=True)
    secondary_path.write_text("secondary", encoding="utf-8")
    record = catalog.get(record_id)
    assert record is not None
    record.artifacts.append(
        ArtifactDescriptor(
            id="secondary",
            role="manifest",
            locator=ArtifactLocator.path(secondary_path),
        )
    )
    catalog.repository.update(record)
    return secondary_path


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


def test_purge_incomplete_error_is_not_a_value_error() -> None:
    """Incomplete purge failures should not be mistaken for validation errors."""
    error = PurgeIncompleteError(
        record_id="1",
        operation_id="operation",
        failed_artifact_ids=["data"],
    )

    assert isinstance(error, RuntimeError)
    assert not isinstance(error, ValueError)


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


@pytest.mark.parametrize("operation", ["copy", "move"])
def test_purge_removes_managed_artifacts_and_hard_deletes_record(
    tmp_path: Path,
    operation: str,
) -> None:
    """Purging a tombstone should remove catalog-managed files and the record."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(
        _source_file(tmp_path, "managed.nc"),
        metadata={"species": "CO2"},
        operation=operation,
    )
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


def test_purge_continues_after_artifact_failure_and_retains_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial artifact purge should keep an accurate tombstone record."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "managed.nc"), metadata={"species": "CO2"})
    record_id = _record_id(record)
    failed_path = record.path()
    assert failed_path is not None
    removed_path = _add_managed_secondary_artifact(catalog, record_id, "secondary.txt")
    catalog.delete(record_id)
    original_remove_target = operation_runner.remove_target

    def flaky_remove_target(
        locator: ArtifactLocator,
        *,
        target_kind: operation_runner.TargetKind = "file",
    ) -> None:
        """Fail the primary artifact removal but allow later removals."""
        if locator.as_path() == failed_path:
            raise PermissionError("locked managed artifact")
        original_remove_target(locator, target_kind=target_kind)

    monkeypatch.setattr(operation_runner, "remove_target", flaky_remove_target)

    with pytest.raises(PurgeIncompleteError, match="Purge incomplete"):
        catalog.purge(record_id)

    retained = catalog.get(record_id)
    assert retained is not None
    assert retained.status == "deleted"
    assert failed_path.exists()
    assert not removed_path.exists()
    states = {artifact.id: artifact.state for artifact in retained.artifacts}
    assert states["data"] == "purge_failed"
    assert states["secondary"] == "purged"
    assert any(state == "purged" for artifact_id, state in states.items() if artifact_id != "secondary")
    assert retained.lifecycle_metadata["purge_status"] == "incomplete"
    assert retained.lifecycle_metadata["purge_failure_count"] == 1
    removed_count = retained.lifecycle_metadata["purge_removed_count"]
    assert isinstance(removed_count, int)
    assert removed_count >= 1
    artifact_events = catalog.audit_events(record_id=record_id, event_type="purge_artifact")
    assert any(event.details["purge_action"] == "failed" for event in artifact_events)
    assert any(event.details["purge_action"] == "removed" for event in artifact_events)
    purge_events = catalog.audit_events(record_id=record_id, event_type="purge")
    assert purge_events[-1].level == "error"
    assert purge_events[-1].details["purge_status"] == "incomplete"


def test_purge_retains_tombstone_when_repository_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repository hard-delete failure should retain purge outcome metadata."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "managed.nc"), metadata={"species": "CO2"})
    record_id = _record_id(record)
    artifact_path = record.path()
    assert artifact_path is not None
    catalog.delete(record_id)

    def fail_delete(record_id: str) -> None:
        """Simulate a repository failure after artifact cleanup."""
        raise RuntimeError(f"repository unavailable for {record_id}")

    monkeypatch.setattr(catalog.repository, "delete", fail_delete)

    with pytest.raises(PurgeIncompleteError, match="repository delete failed"):
        catalog.purge(record_id)

    retained = catalog.get(record_id)
    assert retained is not None
    assert retained.status == "deleted"
    assert not artifact_path.exists()
    assert retained.artifacts[0].state == "purged"
    assert retained.lifecycle_metadata["purge_status"] == "incomplete"
    assert retained.lifecycle_metadata["purge_repository_delete_failed"] is True
    purge_events = catalog.audit_events(record_id=record_id, event_type="purge")
    assert purge_events[-1].exception_type == "RuntimeError"

    with pytest.raises(ValueError, match="Cannot restore record after purge removed artifacts"):
        catalog.restore(record_id)
    assert catalog.get(record_id) == retained


@pytest.mark.parametrize("deleted_reference", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_purge_rejects_retained_references_before_any_removal(
    tmp_path: Path,
    deleted_reference: bool,
    force: bool,
) -> None:
    """Active and deleted references protect managed bytes even with force."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    owner = catalog.add_file(_source_file(tmp_path))
    owner_id = _record_id(owner)
    secondary = _add_managed_secondary_artifact(catalog, owner_id, "secondary.txt")
    # The last artifact is protected, so preflight must precede primary removal.
    reference = catalog.add_reference(secondary)
    reference_id = _record_id(reference)
    if deleted_reference:
        catalog.delete(reference_id)
    if not force:
        catalog.delete(owner_id)
    before = catalog.get(owner_id)

    with pytest.raises(ValueError, match=reference_id):
        catalog.purge(owner_id, force=force)

    assert catalog.get(owner_id) == before
    primary = owner.path()
    assert primary is not None and primary.exists()
    assert secondary.exists()
    assert catalog.audit_events(record_id=owner_id, event_type="purge_artifact") == []


@pytest.mark.parametrize("alias", ["direct", "normalized", "symlink", "chain"])
def test_purge_rejects_shared_primary_path(tmp_path: Path, alias: str) -> None:
    """Equivalent local paths and readable symlink aliases protect primary data."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    owner = catalog.add_file(_source_file(tmp_path))
    path = owner.path()
    assert path is not None
    if alias == "normalized":
        path = path.parent / ".." / path.parent.name / path.name
    elif alias in {"symlink", "chain"}:
        link = tmp_path / "alias.nc"
        link.symlink_to(path)
        path = link
        if alias == "chain":
            second_link = tmp_path / "second-alias.nc"
            second_link.symlink_to(path)
            path = second_link
    reference = (
        catalog.add_reference(path)
        if alias == "direct"
        else catalog.add_artifact(record_type="external_file", locator=ArtifactLocator.path(path))
    )
    catalog.delete(_record_id(owner))

    with pytest.raises(ValueError, match=_record_id(reference)):
        catalog.purge(_record_id(owner))
    assert path.exists()


@pytest.mark.parametrize("reference_kind", ["descendant", "root", "symlink_descendant"])
def test_purge_rejects_directory_dependencies(tmp_path: Path, reference_kind: str) -> None:
    """Directory removals protect descendants and overlapping collection roots."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    owner = catalog.add_file(_source_file(tmp_path))
    owner_id = _record_id(owner)
    directory = catalog.root / catalog.spec.objects_root / "managed-directory"
    directory.mkdir()
    member = directory / "member.nc"
    member.write_text("data", encoding="utf-8")
    owner.artifacts.append(
        ArtifactDescriptor(id="directory", role="manifest", locator=ArtifactLocator.path(directory))
    )
    catalog.repository.update(owner)
    if reference_kind == "root":
        reference = catalog.add_collection(directory.parent)
    else:
        path = member
        if reference_kind == "symlink_descendant":
            path = tmp_path / "alias.nc"
            path.symlink_to(member)
        reference = catalog.add_artifact(record_type="external_file", locator=ArtifactLocator.path(path))
    catalog.delete(owner_id)

    with pytest.raises(ValueError, match=_record_id(reference)):
        catalog.purge(owner_id)
    assert member.exists()
    primary = owner.path()
    assert primary is not None and primary.exists()


@pytest.mark.parametrize("reference_link", [False, True])
def test_purge_symlink_distinguishes_link_from_target(tmp_path: Path, reference_link: bool) -> None:
    """Unlinking an owned symlink protects its aliases but permits target references."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    target = catalog.root / catalog.spec.files_root / "user-owned.nc"
    target.write_text("data", encoding="utf-8")
    link = catalog.root / catalog.spec.objects_root / "managed-link.nc"
    link.symlink_to(target)
    owner = catalog.add_artifact(record_type="managed_file", locator=ArtifactLocator.path(link))
    owner.storage_mode = "write"
    catalog.repository.update(owner)
    path = target
    if reference_link:
        path = tmp_path / "alias.nc"
        path.symlink_to(link)
    reference = catalog.add_artifact(record_type="external_file", locator=ArtifactLocator.path(path))
    catalog.delete(_record_id(owner))

    if reference_link:
        with pytest.raises(ValueError, match=_record_id(reference)):
            catalog.purge(_record_id(owner))
        assert link.is_symlink()
    else:
        catalog.purge(_record_id(owner))
        assert not link.is_symlink()
    assert target.read_text(encoding="utf-8") == "data"


@pytest.mark.parametrize("evidence", ["artifact_state", "removed_count"])
def test_restore_rejects_removed_purge_artifacts(tmp_path: Path, evidence: str) -> None:
    """Either persisted removal evidence independently prevents unsafe restore."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path))
    record = catalog.delete(_record_id(record))
    if evidence == "artifact_state":
        record.artifacts[0].state = "purged"
    else:
        record.lifecycle_metadata["purge_removed_count"] = 1
    catalog.repository.update(record)

    with pytest.raises(ValueError, match="Cannot restore record after purge removed artifacts"):
        catalog.restore(_record_id(record))
    assert catalog.get(_record_id(record)) == record


def test_purge_rejects_alias_through_managed_directory_symlink(tmp_path: Path) -> None:
    """A symlink target's directory components must remain available to aliases."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    actual = catalog.root / catalog.spec.files_root / "actual"
    actual.mkdir()
    member = actual / "member.nc"
    member.write_text("data", encoding="utf-8")
    managed_link = catalog.root / catalog.spec.objects_root / "managed-linkdir"
    managed_link.symlink_to(actual, target_is_directory=True)
    owner = catalog.add_artifact(record_type="managed_file", locator=ArtifactLocator.path(managed_link))
    owner.storage_mode = "write"
    catalog.repository.update(owner)
    alias = tmp_path / "alias.nc"
    alias.symlink_to(managed_link / member.name)
    reference = catalog.add_artifact(record_type="external_file", locator=ArtifactLocator.path(alias))
    catalog.delete(_record_id(owner))

    with pytest.raises(ValueError, match=_record_id(reference)):
        catalog.purge(_record_id(owner))
    assert managed_link.is_symlink()
    assert alias.read_text(encoding="utf-8") == "data"


def test_incomplete_forced_purge_retains_hidden_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced cleanup failures hide the damaged owner and retain deletion metadata."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    owner = catalog.add_file(_source_file(tmp_path))
    owner_id = _record_id(owner)

    def fail_delete(record_id: str) -> None:
        """Fail after managed bytes have been removed."""
        raise RuntimeError(f"repository unavailable for {record_id}")

    monkeypatch.setattr(catalog.repository, "delete", fail_delete)

    with pytest.raises(PurgeIncompleteError):
        catalog.purge(owner_id, force=True)
    retained = catalog.get(owner_id)
    assert retained is not None and retained.status == "deleted"
    assert "deleted_at" in retained.lifecycle_metadata
    assert (
        retained.lifecycle_metadata["delete_operation_id"]
        == retained.lifecycle_metadata["purge_operation_id"]
    )
    assert catalog.search().ids == []
    with pytest.raises(ValueError, match="Cannot restore record after purge removed artifacts"):
        catalog.restore(owner_id)


def test_purge_retry_preserves_removal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed cleanup retry cannot erase earlier removals and permit restore."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    owner = catalog.add_file(_source_file(tmp_path))
    owner_id = _record_id(owner)
    catalog.delete(owner_id)

    def fail_delete(record_id: str) -> None:
        """Retain the record after successful artifact removal."""
        raise RuntimeError(f"repository unavailable for {record_id}")

    monkeypatch.setattr(catalog.repository, "delete", fail_delete)
    with pytest.raises(PurgeIncompleteError):
        catalog.purge(owner_id)
    first = catalog.get(owner_id)
    assert first is not None

    def fail_remove(
        locator: ArtifactLocator,
        *,
        target_kind: operation_runner.TargetKind = "file",
    ) -> None:
        """Fail every retry removal, including already absent paths."""
        raise PermissionError(f"cannot remove {locator.value} as {target_kind}")

    monkeypatch.setattr(operation_runner, "remove_target", fail_remove)
    with pytest.raises(PurgeIncompleteError):
        catalog.purge(owner_id)
    retried = catalog.get(owner_id)
    assert retried is not None
    assert (
        retried.lifecycle_metadata["purge_removed_count"] == first.lifecycle_metadata["purge_removed_count"]
    )
    assert all(artifact.state == "purged" for artifact in retried.artifacts)
    with pytest.raises(ValueError, match="Cannot restore record after purge removed artifacts"):
        catalog.restore(owner_id)


def test_restore_rejects_partial_directory_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed directory cleanup can lose members without reporting removal success."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    directory = catalog.root / catalog.spec.objects_root / "managed-directory"
    directory.mkdir()
    member = directory / "member.nc"
    member.write_text("data", encoding="utf-8")
    owner = catalog.add_artifact(record_type="managed_directory", locator=ArtifactLocator.path(directory))
    owner.storage_mode = "write"
    catalog.repository.update(owner)
    owner_id = _record_id(owner)
    catalog.delete(owner_id)

    def partial_remove(
        locator: ArtifactLocator,
        *,
        target_kind: operation_runner.TargetKind = "file",
    ) -> None:
        """Remove a directory member before failing the overall cleanup."""
        assert locator.as_path() == directory and target_kind == "directory"
        member.unlink()
        raise PermissionError("remaining directory entries are locked")

    monkeypatch.setattr(operation_runner, "remove_target", partial_remove)
    with pytest.raises(PurgeIncompleteError):
        catalog.purge(owner_id)
    retained = catalog.get(owner_id)
    assert retained is not None
    assert retained.artifacts[0].state == "purge_failed"
    assert retained.lifecycle_metadata["purge_removed_count"] == 0
    assert not member.exists()
    with pytest.raises(ValueError, match="Cannot restore record"):
        catalog.restore(owner_id)


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


@pytest.mark.parametrize("storage_mode", [None, "copy", "external"])
def test_purge_skips_record_only_artifact_under_managed_root(
    tmp_path: Path,
    storage_mode: str | None,
) -> None:
    """Record-only artifacts stay user-owned even within a managed path."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    target = catalog.root / catalog.spec.files_root / "user-owned.nc"
    target.write_text("user data", encoding="utf-8")
    record = catalog.add_artifact(
        record_type="external_file",
        locator=ArtifactLocator.path(target),
        storage_mode=storage_mode,
    )
    assert record.storage_mode == ("external" if storage_mode == "external" else "reference")
    record_id = _record_id(record)
    catalog.delete(record_id)

    catalog.purge(record_id)

    assert target.read_text(encoding="utf-8") == "user data"
    assert catalog.get(record_id) is None


def test_purge_skips_legacy_artifact_without_storage_mode(tmp_path: Path) -> None:
    """Legacy null storage modes do not prove ownership of managed paths."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    target = catalog.root / catalog.spec.files_root / "user-owned.nc"
    target.write_text("user data", encoding="utf-8")
    record = catalog.add_artifact(
        record_type="external_file",
        locator=ArtifactLocator.path(target),
    )
    record.storage_mode = None
    catalog.repository.update(record)
    record_id = _record_id(record)
    catalog.delete(record_id)

    catalog.purge(record_id)

    assert target.read_text(encoding="utf-8") == "user data"
    assert catalog.get(record_id) is None


def test_purge_requires_deleted_record_unless_forced(tmp_path: Path) -> None:
    """Active records should not be purged unless force is explicit."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path), metadata={"species": "CO2"})
    record_id = _record_id(record)

    with pytest.raises(ValueError, match="must be deleted before purge"):
        catalog.purge(record_id)

    assert catalog.get(record_id) is not None


def test_search_rejects_conflicting_deleted_visibility_flags(tmp_path: Path) -> None:
    """Deleted-record search flags should be mutually exclusive."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    with pytest.raises(ValueError, match="include_deleted and only_deleted"):
        catalog.search(include_deleted=True, only_deleted=True)


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
