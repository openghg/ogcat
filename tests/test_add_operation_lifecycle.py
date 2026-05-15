"""Regression tests for the internal add-operation lifecycle phases."""

from pathlib import Path

import pytest

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogRecord,
    CatalogSpec,
    MetadataFieldDescription,
    OperationContext,
    OperationSource,
    OperationState,
    PluginRegistry,
    RecordSchema,
    ValidationReport,
)
from ogcat.catalog_application import CatalogApplication
from ogcat.operation_runner import AddOperationRequest, OperationRunner
from ogcat.storage import StoragePlan


def test_add_file_facade_delegates_to_application_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog.add_file keeps API coercion while delegating operation orchestration."""
    captured: dict[str, object] = {}

    def fake_add_file(self: CatalogApplication, **kwargs: object) -> CatalogRecord:
        captured.update(kwargs)
        source = kwargs["source"]
        assert isinstance(source, Path)
        return CatalogRecord(
            catalog=self.catalog.spec.catalog_name,
            time_added="2026-05-15T00:00:00Z",
            id="delegated",
            record_type=str(kwargs["record_type"]),
            locator=ArtifactLocator.path(source),
        )

    monkeypatch.setattr(CatalogApplication, "add_file", fake_add_file)
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source, metadata={"title": "Example"}, operation="copy")

    assert record.id == "delegated"
    assert captured["source"] == source.resolve()
    assert captured["metadata"] == {"title": "Example"}
    assert captured["record_type"] == "managed_file"
    assert captured["operation"] == "copy"
    assert captured["primary_location"] == "uuid"


def test_run_add_operation_delegates_to_operation_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog add setup delegates the lifecycle to OperationRunner."""
    requests: list[AddOperationRequest] = []
    runners: list[OperationRunner] = []

    class FakeRunner(OperationRunner):
        def __init__(self, request: AddOperationRequest) -> None:
            self.request = request

        def run(self) -> CatalogRecord:
            return CatalogRecord(
                catalog="artifacts",
                time_added="2026-05-14T00:00:00+00:00",
                id="runner",
                record_type=self.request.record_type,
                locator=ArtifactLocator(kind="uri", value="s3://bucket/delegated.zarr"),
            )

    def build_fake_runner(self: Catalog, request: AddOperationRequest) -> OperationRunner:
        requests.append(request)
        runner = FakeRunner(request)
        runners.append(runner)
        return runner

    monkeypatch.setattr(Catalog, "_build_add_operation_runner", build_fake_runner)
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
        metadata={"title": "Delegated"},
    )

    assert len(requests) == 1
    assert len(runners) == 1
    request = requests[0]
    assert isinstance(runners[0], OperationRunner)
    assert request.transaction.repository is catalog.repository
    assert request.commit is True
    assert request.operation_type == "add_artifact"
    assert request.record_type == "external_reference"
    assert request.metadata == {"title": "Delegated"}
    assert request.materialization_intent.writer is None
    assert request.materialization_intent.write_mode == "reference"
    assert request.materialization_intent.ogcat_owned is False
    assert record.id == "runner"


def test_add_file_application_request_uses_copy_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed file requests carry explicit copy materialization intent."""
    requests: list[AddOperationRequest] = []

    class FakeRunner(OperationRunner):
        def __init__(self, request: AddOperationRequest) -> None:
            self.request = request

        def run(self) -> CatalogRecord:
            return CatalogRecord(
                catalog="files",
                time_added="2026-05-15T00:00:00Z",
                id="runner",
                record_type=self.request.record_type,
                locator=ArtifactLocator.path(tmp_path / "stored.nc"),
            )

    def build_fake_runner(self: Catalog, request: AddOperationRequest) -> OperationRunner:
        requests.append(request)
        return FakeRunner(request)

    monkeypatch.setattr(Catalog, "_build_add_operation_runner", build_fake_runner)
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    catalog.add_file(source, operation="copy")

    request = requests[0]
    assert request.operation_type == "add_file"
    assert request.materialization_intent.target_kind == "file"
    assert request.materialization_intent.write_mode == "copy"
    assert request.materialization_intent.ogcat_owned is True
    assert type(request.materialization_intent.writer).__name__ == "CopyArtifactWriter"
    assert len(request.secondary_artifact_operations) == 1
    assert request.secondary_artifact_operations[0].role == "template_link"


def test_add_file_template_primary_request_has_no_template_link_secondary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Template-primary file requests do not schedule a template-link secondary."""
    requests: list[AddOperationRequest] = []

    class FakeRunner(OperationRunner):
        def __init__(self, request: AddOperationRequest) -> None:
            self.request = request

        def run(self) -> CatalogRecord:
            return CatalogRecord(
                catalog="files",
                time_added="2026-05-15T00:00:00Z",
                id="runner",
                record_type=self.request.record_type,
                locator=ArtifactLocator.path(tmp_path / "stored.nc"),
            )

    def build_fake_runner(self: Catalog, request: AddOperationRequest) -> OperationRunner:
        requests.append(request)
        return FakeRunner(request)

    monkeypatch.setattr(Catalog, "_build_add_operation_runner", build_fake_runner)
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    catalog.add_file(source, operation="copy", primary_location="template")

    request = requests[0]
    assert request.operation_type == "add_file"
    assert request.secondary_artifact_operations == ()


def test_add_artifact_application_request_uses_writer_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer-backed artifact requests carry target kind and write mode intent."""
    requests: list[AddOperationRequest] = []

    class DirectoryWriter:
        target_kind = "directory"
        write_mode = "write"

        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            raise AssertionError("fake runner should not invoke writer")

    class FakeRunner(OperationRunner):
        def __init__(self, request: AddOperationRequest) -> None:
            self.request = request

        def run(self) -> CatalogRecord:
            return CatalogRecord(
                catalog="artifacts",
                time_added="2026-05-15T00:00:00Z",
                id="runner",
                record_type=self.request.record_type,
                locator=ArtifactLocator.path(tmp_path / "stored.zarr"),
            )

    def build_fake_runner(self: Catalog, request: AddOperationRequest) -> OperationRunner:
        requests.append(request)
        return FakeRunner(request)

    writer = DirectoryWriter()
    monkeypatch.setattr(Catalog, "_build_add_operation_runner", build_fake_runner)
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    catalog.add_artifact(
        record_type="zarr_store",
        locator=ArtifactLocator.path(tmp_path / "store.zarr"),
        source=OperationSource(kind="memory", descriptor="generated"),
        artifact_writer=writer,
    )

    request = requests[0]
    assert request.operation_type == "add_artifact"
    assert request.materialization_intent.writer is writer
    assert request.materialization_intent.target_kind == "directory"
    assert request.materialization_intent.write_mode == "write"
    assert request.materialization_intent.ogcat_owned is True


def test_add_file_lifecycle_preserves_hook_order_and_file_storage(tmp_path: Path) -> None:
    """add_file runs hooks in lifecycle order and stores the copied file."""
    calls: list[str] = []

    class LifecycleHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append("before_validate_metadata")

        def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
            calls.append(f"after_validate_metadata:{report.ok}")

        def resolve_artifact_locator(self, context: OperationContext) -> None:
            calls.append("resolve_artifact_locator")

        def extract_metadata(self, context: OperationContext) -> dict[str, object]:
            calls.append("extract_metadata")
            return {"extract_hook": context.operation_type}

        def before_record_write(self, context: OperationContext) -> None:
            calls.append("before_record_write")

        def after_record_write(self, context: OperationContext) -> None:
            calls.append(f"after_record_write:{context.record_id is not None}")

        def before_commit(self, context: OperationContext) -> None:
            calls.append("before_commit")

        def after_commit(self, context: OperationContext) -> None:
            calls.append("after_commit")

    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        plugins=PluginRegistry([LifecycleHook()]),
    )

    record = catalog.add_file(source)

    assert calls == [
        "before_validate_metadata",
        "after_validate_metadata:True",
        "resolve_artifact_locator",
        "extract_metadata",
        "before_record_write",
        "after_record_write:True",
        "before_commit",
        "after_commit",
    ]
    assert record.stored_abspath is not None
    assert Path(record.stored_abspath).exists()
    assert record.derived_metadata["extract_hook"] == "add_file"


def test_record_only_add_artifact_skips_artifact_write(tmp_path: Path) -> None:
    """Record-only add_artifact stores the locator without creating the target."""
    target = tmp_path / "generated.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=OperationSource(kind="text", descriptor="record only"),
    )

    assert record.locator == ArtifactLocator.path(target)
    assert record.stored_abspath == str(target)
    assert not target.exists()
    assert catalog.repository.all() == [record]


def test_explicit_reference_storage_plan_skips_artifact_writer(tmp_path: Path) -> None:
    """Explicit reference plans are authoritative and do not run supplied writers."""

    class ExplodingWriter:
        target_kind = "file"
        write_mode = "write"

        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            raise AssertionError("reference storage plan should skip the writer")

    target = tmp_path / "planned.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="planned_reference",
        storage_plan=StoragePlan(locator=ArtifactLocator.path(target), write_mode="reference"),
        source=OperationSource(kind="text", descriptor="record only"),
        artifact_writer=ExplodingWriter(),
    )

    assert record.locator == ArtifactLocator.path(target)
    assert not target.exists()


def test_explicit_storage_plan_rejects_writer_write_mode_mismatch(tmp_path: Path) -> None:
    """Writer-declared write modes must match authoritative explicit plans."""

    class CopyDeclaredWriter:
        target_kind = "file"
        write_mode = "copy"

        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            raise AssertionError("mismatched writer should fail before writing")

    target = tmp_path / "planned.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(
        ValueError,
        match="Artifact writer write_mode 'copy' does not match storage plan write_mode 'move'",
    ):
        catalog.add_artifact(
            record_type="planned_move",
            storage_plan=StoragePlan(
                locator=ArtifactLocator.path(target),
                write_mode="move",
                ogcat_owned=True,
            ),
            source=OperationSource(kind="text", descriptor="planned move"),
            artifact_writer=CopyDeclaredWriter(),
        )

    assert not target.exists()


def test_writer_backed_add_artifact_writes_and_persists_metadata(tmp_path: Path) -> None:
    """Writer-backed add_artifact writes the target and persists writer metadata."""

    class TextWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            target_path = target.as_path()
            assert target_path is not None
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(str(source.metadata["text"]), encoding="utf-8")
            context.derived_metadata["writer_text_length"] = len(str(source.metadata["text"]))

    target = tmp_path / "outputs" / "generated.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=OperationSource(kind="text", descriptor="inline text", metadata={"text": "hello"}),
        artifact_writer=TextWriter(),
    )

    assert target.read_text(encoding="utf-8") == "hello"
    assert record.derived_metadata["writer_text_length"] == 5


def test_hook_failure_after_writer_rolls_back_artifact_and_record(tmp_path: Path) -> None:
    """A post-write hook failure rolls back writer cleanup and the staged record."""
    rollback_calls: list[str] = []

    class TextWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            target_path = target.as_path()
            assert target_path is not None
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("created", encoding="utf-8")

            def rollback() -> None:
                rollback_calls.append("writer")
                target_path.unlink(missing_ok=True)

            context.rollback(rollback, description="remove generated text")

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop after writer")

    target = tmp_path / "outputs" / "generated.txt"
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="artifacts"),
        plugins=PluginRegistry([FailingHook()]),
    )

    with pytest.raises(RuntimeError, match="stop after writer"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            source=OperationSource(kind="text", descriptor="inline text"),
            artifact_writer=TextWriter(),
        )

    assert rollback_calls == ["writer"]
    assert not target.exists()
    assert catalog.repository.all() == []


def test_after_record_hook_failure_rolls_back_template_secondary(
    tmp_path: Path,
) -> None:
    """Failures after template-link creation roll back the symlink, primary, and record."""
    root = tmp_path / "catalog"
    source = tmp_path / "secondary.nc"
    source.write_text("payload", encoding="utf-8")
    files_root = root / "data" / "files"
    objects_root = root / "data" / "objects"

    class FailingAfterRecordHook:
        def after_record_write(self, context: OperationContext) -> None:
            created_links = [path for path in files_root.rglob("*.nc") if path.is_symlink()]
            assert created_links, "template secondary should exist before after_record_write"
            raise RuntimeError("stop after secondary artifact")

    catalog = Catalog.create(
        root,
        CatalogSpec(catalog_name="files"),
        plugins=PluginRegistry([FailingAfterRecordHook()]),
    )

    with pytest.raises(RuntimeError, match="stop after secondary artifact"):
        catalog.add_file(source)

    assert catalog.repository.all() == []
    assert list(files_root.rglob("*.nc")) == []
    assert list(objects_root.rglob("*.nc")) == []


def test_operation_runner_preserves_failure_audit_phase_and_rollback(tmp_path: Path) -> None:
    """Runner-owned failures keep the existing failure phase and rollback audit."""

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop before record")

    source = tmp_path / "failure.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        plugins=PluginRegistry([FailingHook()]),
    )

    with pytest.raises(RuntimeError, match="stop before record"):
        catalog.add_file(source)

    events = catalog.audit_events()
    failure = next(event for event in events if event.event_type == "failure")
    rollback_events = [event for event in events if event.event_type == "rollback"]
    assert failure.details["phase"] == "before_record_write"
    assert [event.message for event in rollback_events] == ["Rollback started.", "Rollback completed."]
    assert catalog.repository.all() == []
    assert source.exists()


def test_operation_runner_preserves_caller_owned_transaction_on_hook_failure(tmp_path: Path) -> None:
    """Caller-owned transactions remain staged when post-record hooks fail."""

    class FailingAfterWriteHook:
        def after_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("external transaction hook failure")

    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="artifacts"),
        plugins=PluginRegistry([FailingAfterWriteHook()]),
    )

    with catalog.transaction() as transaction:
        with pytest.raises(RuntimeError, match="external transaction hook failure"):
            catalog.add_artifact(
                record_type="external_reference",
                locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
                transaction=transaction,
            )

        assert transaction.state is OperationState.STAGED
        assert len(catalog.repository.all()) == 1
        transaction.rollback()
        assert catalog.repository.all() == []


def test_validation_failure_runs_after_validate_and_stops_before_write(tmp_path: Path) -> None:
    """Validation errors still call after_validate_metadata and stop before writes."""
    calls: list[str] = []

    class ValidationHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append("before_validate_metadata")

        def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
            calls.append(f"after_validate_metadata:{report.ok}")

        def resolve_artifact_locator(self, context: OperationContext) -> None:
            calls.append("resolve_artifact_locator")

    class TextWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            calls.append("writer")
            target_path = target.as_path()
            assert target_path is not None
            target_path.write_text("should not happen", encoding="utf-8")

    target = tmp_path / "outputs" / "generated.txt"
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="artifacts",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="title",
                        description="Required title.",
                        required=True,
                    )
                ]
            ),
        ),
        plugins=PluginRegistry([ValidationHook()]),
    )

    with pytest.raises(ValueError, match="Missing required metadata for schema default: title"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            metadata={},
            source=OperationSource(kind="text", descriptor="inline text"),
            artifact_writer=TextWriter(),
        )

    assert calls == ["before_validate_metadata", "after_validate_metadata:False"]
    assert not target.exists()
    assert catalog.repository.all() == []


def test_derived_metadata_from_writer_extract_hook_and_record_hook_persists(tmp_path: Path) -> None:
    """Derived metadata mutations from write, extract, and record phases persist."""

    class MetadataHook:
        def extract_metadata(self, context: OperationContext) -> dict[str, object]:
            return {"extract_hook": True}

        def before_record_write(self, context: OperationContext) -> None:
            context.derived_metadata["record_hook"] = context.record_type

    class TextWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            target_path = target.as_path()
            assert target_path is not None
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("payload", encoding="utf-8")
            context.derived_metadata["writer"] = source.kind

    target = tmp_path / "outputs" / "metadata.txt"
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="artifacts"),
        plugins=PluginRegistry([MetadataHook()]),
    )

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=OperationSource(kind="text", descriptor="inline text"),
        artifact_writer=TextWriter(),
    )

    assert record.derived_metadata["writer"] == "text"
    assert record.derived_metadata["extract_hook"] is True
    assert record.derived_metadata["record_hook"] == "generated_text"


def test_writer_failure_runs_registered_rollback_and_leaves_no_record(tmp_path: Path) -> None:
    """A failing writer rolls back registered cleanup before any record exists."""
    rollback_calls: list[str] = []

    class FailingWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            target_path = target.as_path()
            assert target_path is not None
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("partial", encoding="utf-8")

            def rollback() -> None:
                rollback_calls.append("writer")
                target_path.unlink(missing_ok=True)

            context.rollback(rollback, description="remove partial writer output")
            raise OSError("writer failed")

    target = tmp_path / "outputs" / "partial.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(OSError, match="writer failed"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            source=OperationSource(kind="text", descriptor="inline text"),
            artifact_writer=FailingWriter(),
        )

    assert rollback_calls == ["writer"]
    assert not target.exists()
    assert catalog.repository.all() == []
