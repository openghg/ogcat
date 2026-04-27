"""Best-effort transaction behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogRecord, CatalogSpec, OperationState, UnitOfWork


def _artifact_record() -> CatalogRecord:
    """Build a simple non-file artifact record."""
    return CatalogRecord(
        catalog="artifacts",
        time_added="2026-04-27T12:00:00Z",
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
    )


def test_catalog_transaction_rolls_back_staged_artifact_when_later_work_fails(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(RuntimeError, match="later operation failed"), catalog.transaction() as transaction:
        persisted = transaction.insert_staged_record(_artifact_record())
        assert persisted.id == "1"
        raise RuntimeError("later operation failed")

    assert catalog.repository.all() == []


def test_add_artifact_can_stage_record_in_catalog_transaction(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(RuntimeError, match="later operation failed"), catalog.transaction() as transaction:
        record = catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
            transaction=transaction,
        )
        assert record.id == "1"
        raise RuntimeError("later operation failed")

    assert catalog.repository.all() == []


def test_catalog_transaction_commit_keeps_staged_artifact(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with catalog.transaction() as transaction:
        persisted = transaction.insert_staged_record(_artifact_record())
        operation_id = transaction.operation_id
        transaction.commit()

    assert operation_id
    assert transaction.state is OperationState.COMMITTED
    assert catalog.repository.all() == [persisted]


def test_commit_prevents_registered_rollback_actions_from_running(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))
    calls: list[str] = []

    with catalog.transaction() as transaction:
        transaction.register_rollback(lambda: calls.append("rollback"), description="record call")
        transaction.commit()

    assert calls == []
    assert transaction.rollback_errors == []


def test_rollback_action_failure_is_recorded_and_original_exception_remains_visible(
    tmp_path: Path,
) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    def fail_rollback() -> None:
        raise OSError("cleanup failed")

    with (
        pytest.raises(RuntimeError, match="original failure") as exc_info,
        UnitOfWork(catalog.repository) as transaction,
    ):
        transaction.register_rollback(fail_rollback, description="failing cleanup")
        raise RuntimeError("original failure")

    assert transaction.state is OperationState.FAILED
    assert len(transaction.rollback_errors) == 1
    assert transaction.rollback_errors[0].description == "failing cleanup"
    assert "original failure" in str(exc_info.value)
    assert exc_info.value.__notes__ == [
        "rollback failed for failing cleanup: OSError: cleanup failed",
    ]


def test_direct_add_artifact_commits_normally(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2"},
    )

    assert record.id == "1"
    assert catalog.repository.all() == [record]
