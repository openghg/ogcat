"""Best-effort transaction behavior tests."""

from __future__ import annotations

from dataclasses import replace
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


def test_transaction_rejects_rollback_registration_after_commit(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with catalog.transaction() as transaction:
        transaction.commit()

        with pytest.raises(RuntimeError, match="Cannot register rollback action after commit"):
            transaction.register_rollback(lambda: None, description="too late")


def test_transaction_rejects_record_staging_after_commit(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with catalog.transaction() as transaction:
        transaction.commit()

        with pytest.raises(RuntimeError, match="Cannot stage record after commit"):
            transaction.insert_staged_record(_artifact_record())


def test_add_artifact_rejects_transaction_from_another_catalog(tmp_path: Path) -> None:
    first = Catalog.create(tmp_path / "first", CatalogSpec(catalog_name="first"))
    second = Catalog.create(tmp_path / "second", CatalogSpec(catalog_name="second"))

    with first.transaction() as transaction, pytest.raises(ValueError, match="different catalog repository"):
        second.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
            transaction=transaction,
        )

    assert first.repository.all() == []
    assert second.repository.all() == []


def test_rollback_action_failure_is_recorded_and_original_exception_remains_visible(
    tmp_path: Path,
) -> None:
    """Cleanup errors become notes without replacing the original exception."""
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    def fail_rollback() -> None:
        """Simulate cleanup that cannot complete."""
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


def test_uncommitted_context_reports_cleanup_failures_after_running_every_action(tmp_path: Path) -> None:
    """Normal context exit reports every failure after completing reverse cleanup."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    calls: list[str] = []

    def fail_rollback(message: str) -> None:
        """Record a cleanup attempt and simulate its failure."""
        calls.append(message)
        raise OSError(message)

    with (
        pytest.raises(ExceptionGroup, match="Rollback failed") as exc_info,
        UnitOfWork(catalog.repository) as transaction,
    ):
        transaction.insert_staged_record(_artifact_record())
        transaction.register_rollback(lambda: fail_rollback("first"))
        transaction.register_rollback(lambda: calls.append("successful cleanup"))
        transaction.register_rollback(lambda: fail_rollback("last"))

    assert calls == ["last", "successful cleanup", "first"]
    assert [str(error) for error in exc_info.value.exceptions] == ["last", "first"]
    assert transaction.state is OperationState.FAILED
    assert len(transaction.rollback_errors) == 2
    assert catalog.repository.all() == []


def test_repeated_failed_rollback_preserves_original_exception_without_duplicate_notes(
    tmp_path: Path,
) -> None:
    """Explicit rollback followed by context exit runs cleanup and adds notes once."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    calls: list[str] = []
    original = RuntimeError("original failure")

    def fail_rollback() -> None:
        """Record one failed cleanup attempt."""
        calls.append("cleanup")
        raise OSError("cleanup failed")

    with (
        pytest.raises(RuntimeError, match="original failure") as exc_info,
        UnitOfWork(catalog.repository) as transaction,
    ):
        transaction.register_rollback(fail_rollback, description="cleanup")
        transaction.rollback(original_exception=original)
        transaction.rollback(original_exception=original)
        raise original

    assert exc_info.value is original
    assert calls == ["cleanup"]
    assert original.__notes__ == ["rollback failed for cleanup: OSError: cleanup failed"]
    with pytest.raises(ExceptionGroup, match="Rollback failed"):
        transaction.rollback()
    assert calls == ["cleanup"]


@pytest.mark.parametrize(
    "state", [OperationState.COMMITTED, OperationState.ROLLED_BACK, OperationState.FAILED]
)
def test_terminal_transaction_rejects_changes_without_writing(tmp_path: Path, state: OperationState) -> None:
    """Closed transactions reject every mutation and retain their stored record."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    persisted = catalog.repository.insert(_artifact_record())
    transaction = UnitOfWork(catalog.repository)

    def fail_rollback() -> None:
        """Leave a failed transaction after cleanup."""
        raise OSError("cleanup failed")

    if state is OperationState.COMMITTED:
        transaction.commit()
    elif state is OperationState.FAILED:
        transaction.register_rollback(fail_rollback)
        with pytest.raises(ExceptionGroup, match="Rollback failed"):
            transaction.rollback()
    else:
        transaction.rollback()
        transaction.rollback()

    for mutate in (
        lambda: transaction.insert_staged_record(_artifact_record()),
        lambda: transaction.stage_record(_artifact_record()),
        lambda: transaction.update_staged_record(replace(persisted, user_metadata={"changed": True})),
        lambda: transaction.register_rollback(lambda: None),
        transaction.commit,
    ):
        with pytest.raises(RuntimeError, match="Cannot .* after"):
            mutate()

    assert transaction.state is state
    assert catalog.repository.all() == [persisted]


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
