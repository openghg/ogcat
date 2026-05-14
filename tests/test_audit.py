"""Structured audit logging tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec
from ogcat.audit import (
    AUDIT_LOG_RELATIVE_PATH,
    AuditEvent,
    JsonlAuditSink,
    exception_operation_id,
    redact_sensitive_values,
)
from ogcat.hooks import OperationContext, OperationSource


def _source_file(tmp_path: Path, name: str = "source.nc") -> Path:
    """Create a small source file for add tests."""
    source = tmp_path / name
    source.write_text("dummy", encoding="utf-8")
    return source


def test_successful_add_writes_structured_audit_events(tmp_path: Path) -> None:
    """Successful add operations should write the expected lifecycle events."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        audit_user_id="alice",
    )

    record = catalog.add_file(_source_file(tmp_path))

    events = catalog.audit_events()
    event_types = [event.event_type for event in events]
    assert event_types[:2] == ["operation-started", "validation"]
    assert "write" in event_types
    assert event_types[-1] == "commit"
    assert "failure" not in event_types
    assert "rollback" not in event_types
    assert {event.operation_id for event in events} == {events[0].operation_id}
    assert {event.user_id for event in events} == {"alice"}
    assert {event.catalog_path for event in events} == {str(catalog.root)}
    assert any(event.record_id == record.id and event.event_type == "commit" for event in events)
    assert (catalog.root / AUDIT_LOG_RELATIVE_PATH).exists()


def test_registered_hook_phases_emit_formal_audit_events(tmp_path: Path) -> None:
    """Hook audit events should describe the method and phase consistently."""

    class AuditedHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            context.user_metadata["hooked"] = True

        def before_commit(self, context: OperationContext) -> None:
            context.add_warning("commit hook warning", hook_name="AuditedHook")

    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        hooks=[AuditedHook()],
    )

    catalog.add_file(_source_file(tmp_path))

    hook_events = [event for event in catalog.audit_events(event_type="hook")]
    hook_methods_and_phases = [
        (event.details["hook_method"], event.details["hook_phase"], event.level) for event in hook_events
    ]
    assert hook_methods_and_phases == [
        ("before_validate_metadata", "started", "info"),
        ("before_validate_metadata", "completed", "info"),
        ("before_commit", "started", "info"),
        ("before_commit", "completed", "warning"),
    ]
    assert all(event.details["hook_count"] == 1 for event in hook_events)
    assert [event.details["phase"] for event in hook_events] == [
        "before_validate_metadata",
        "before_validate_metadata",
        "before_commit",
        "before_commit",
    ]
    assert hook_events[-1].details["warnings_added"] == 1


def test_failed_add_writes_failure_and_rollback_events(tmp_path: Path) -> None:
    """A failed add should log the failure, rollback, and operation id."""

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("simulated audit failure path")

    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        hooks=[FailingHook()],
        audit_user_id="alice",
    )
    source = _source_file(tmp_path, "failure.nc")

    with pytest.raises(RuntimeError, match="simulated audit failure path") as exc_info:
        catalog.add_file(source)

    operation_id = exception_operation_id(exc_info.value)
    assert operation_id is not None
    events = catalog.audit_events(operation_id=operation_id)
    event_types = [event.event_type for event in events]
    assert "failure" in event_types
    assert event_types.count("rollback") == 2
    failure = next(event for event in events if event.event_type == "failure")
    assert failure.exception_type == "RuntimeError"
    assert failure.details["phase"] == "before_record_write"
    hook_failure = next(
        event for event in events if event.event_type == "hook" and event.details["hook_phase"] == "failed"
    )
    assert hook_failure.details["hook_method"] == "before_record_write"
    assert hook_failure.exception_type == "RuntimeError"
    rollback_events = [event for event in events if event.event_type == "rollback"]
    assert rollback_events[-1].message == "Rollback completed."
    assert catalog.repository.all() == []
    assert source.exists()
    assert list((catalog.root / catalog.spec.files_root).rglob("failure.nc")) == []


def test_audit_events_filter_by_user_operation_and_record(tmp_path: Path) -> None:
    """Audit event filters should narrow results by user, operation, and record."""
    root = tmp_path / "catalog"
    alice_catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"), audit_user_id="alice")
    alice_record = alice_catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/alice.zarr"),
    )
    bob_catalog = Catalog.open(root, audit_user_id="bob")
    bob_record = bob_catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/bob.zarr"),
    )
    alice_operation = next(
        event.operation_id
        for event in alice_catalog.audit_events(record_id=alice_record.id)
        if event.event_type == "commit"
    )
    bob_operation = next(
        event.operation_id
        for event in bob_catalog.audit_events(record_id=bob_record.id)
        if event.event_type == "commit"
    )

    assert {event.user_id for event in bob_catalog.audit_events(user_id="alice")} == {"alice"}
    assert {event.operation_id for event in bob_catalog.audit_events(operation_id=alice_operation)} == {
        alice_operation
    }
    assert {event.record_id for event in bob_catalog.audit_events(record_id=bob_record.id)} == {bob_record.id}
    assert bob_catalog.audit_events(operation_id=alice_operation)
    assert bob_catalog.audit_events(operation_id=bob_operation)


def test_sensitive_audit_details_are_redacted() -> None:
    """Sensitive-looking keys should have their values redacted recursively."""
    event = AuditEvent(
        operation_id="op-1",
        level="info",
        event_type="operation-started",
        message="started",
        details={
            "api_key": "abc123",
            "nested": {"password": "pw", "safe": "kept"},
            "items": [{"secret_token": "token-value"}],
        },
    )

    assert event.details["api_key"] == "[REDACTED]"
    assert event.details["nested"] == {"password": "[REDACTED]", "safe": "kept"}
    assert event.details["items"] == [{"secret_token": "[REDACTED]"}]
    assert redact_sensitive_values({"private_key": "key"})["private_key"] == "[REDACTED]"


def test_corrupt_audit_log_lines_are_skipped(tmp_path: Path) -> None:
    """A corrupt JSONL line should warn without hiding valid audit events."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    catalog.add_file(_source_file(tmp_path))
    log_path = JsonlAuditSink(catalog.root).path
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")

    with pytest.warns(RuntimeWarning, match="Skipping corrupt audit log line"):
        events = catalog.audit_events()

    assert [event.event_type for event in events]
    assert all(event.event_type != "unknown" for event in events)


def test_audit_logs_do_not_record_operation_source_payloads(tmp_path: Path) -> None:
    """OperationSource payload contents should not be written to audit logs."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    payload = {"contents": "DO-NOT-LOG-PAYLOAD"}

    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/payload.zarr"),
        source=OperationSource(
            kind="memory",
            descriptor="payload source",
            metadata={"api_key": "DO-NOT-LOG-KEY"},
            payload=payload,
        ),
    )

    raw_log = JsonlAuditSink(catalog.root).path.read_text(encoding="utf-8")
    assert "DO-NOT-LOG-PAYLOAD" not in raw_log
    assert "DO-NOT-LOG-KEY" not in raw_log
