from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogSpec,
    HookManager,
    HookWarning,
    MetadataFieldDescription,
    PluginRegistry,
    RecordSchema,
)
from ogcat.hooks import OperationContext, OperationSource
from ogcat.models import JsonValue
from ogcat.transactions import OperationState
from ogcat.validation import ValidationReport


def test_direct_registration_invokes_hook(tmp_path: Path) -> None:
    calls: list[str] = []

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(context.operation_type)

    registry = PluginRegistry()
    registry.register(Hook())
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)

    assert calls == ["add_file"]


def test_hook_iterable_convenience_invokes_hooks(tmp_path: Path) -> None:
    """Hook generator inputs should be accepted and dispatched."""

    calls: list[str] = []

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(context.operation_type)

    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        hooks=(Hook() for _ in range(1)),
    )
    source = tmp_path / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)

    assert calls == ["add_file"]


def test_hook_list_convenience_invokes_hooks(tmp_path: Path) -> None:
    """Hook list inputs should be accepted and dispatched."""

    calls: list[str] = []

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(context.operation_type)

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), hooks=[Hook()])
    source = tmp_path / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)

    assert calls == ["add_file"]


def test_plugin_iterable_convenience_invokes_hooks_on_open(tmp_path: Path) -> None:
    """Plugin iterable inputs should build a hook manager on open."""

    calls: list[str] = []

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(context.operation_type)

    created = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    catalog = Catalog.open(created.root, plugins=(Hook() for _ in range(1)))

    catalog.add_reference(ArtifactLocator(kind="uri", value="https://example.org/data.nc"))

    assert calls == ["add_artifact"]


def test_create_rejects_invalid_hook_inputs_immediately(tmp_path: Path) -> None:
    """Invalid hooks inputs should fail before catalog files are created."""

    with pytest.raises(TypeError, match="hooks must be a HookManager or iterable of hook objects"):
        Catalog.create(
            tmp_path / "catalog",
            CatalogSpec(catalog_name="files"),
            hooks="not hooks",  # type: ignore[arg-type]
        )

    assert not (tmp_path / "catalog" / "catalog.json").exists()


def test_open_rejects_invalid_plugin_inputs_immediately(tmp_path: Path) -> None:
    """Invalid plugins inputs should fail while opening the catalog."""

    created = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    with pytest.raises(TypeError, match="plugins must be a PluginRegistry or iterable of hook objects"):
        Catalog.open(created.root, plugins="not plugins")  # type: ignore[arg-type]


def test_open_rejects_non_hook_objects_in_hook_manager_immediately(tmp_path: Path) -> None:
    """Hook managers should reject entries with no supported hook methods."""

    created = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    with pytest.raises(TypeError, match="hooks item 0 must provide at least one callable hook method"):
        Catalog.open(created.root, hooks=HookManager([object()]))


def test_create_rejects_both_plugins_and_hooks(tmp_path: Path) -> None:
    """Catalog creation should preserve the plugins-or-hooks exclusivity rule."""

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            pass

    with pytest.raises(ValueError, match="Pass either plugins or hooks, not both"):
        Catalog.create(
            tmp_path / "catalog",
            CatalogSpec(catalog_name="files"),
            plugins=PluginRegistry([Hook()]),
            hooks=HookManager([Hook()]),
        )


def test_hooks_run_in_registration_order(tmp_path: Path) -> None:
    calls: list[str] = []

    class OrderedHook:
        def __init__(self, name: str) -> None:
            self.name = name

        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:before_validate_metadata")

        def resolve_artifact_locator(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:resolve_artifact_locator")

        def before_record_write(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:before_record_write")

        def before_commit(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:before_commit")

    registry = PluginRegistry([OrderedHook("first"), OrderedHook("second")])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "ordered.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)

    assert calls == [
        "first:before_validate_metadata",
        "second:before_validate_metadata",
        "first:resolve_artifact_locator",
        "second:resolve_artifact_locator",
        "first:before_record_write",
        "second:before_record_write",
        "first:before_commit",
        "second:before_commit",
    ]


def test_before_validate_hook_can_mutate_metadata_for_validation_and_naming(tmp_path: Path) -> None:
    class FilenameMetadataHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            assert context.source_path is not None
            context.user_metadata["title"] = context.source_path.stem

    registry = PluginRegistry([FilenameMetadataHook()])
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                filename_template="{title}{original_suffix}",
                metadata_fields=[
                    MetadataFieldDescription(
                        name="title",
                        description="Title derived from filename.",
                        required=True,
                    )
                ],
            ),
        ),
        plugins=registry,
    )
    source = tmp_path / "from-hook.nc"
    source.write_text("dummy", encoding="utf-8")

    record = catalog.add_file(source)

    assert record.user_metadata["title"] == "from-hook"
    assert Path(record.stored_abspath or "").name == "from-hook.nc"


def test_resolve_artifact_locator_replacement_controls_add_file_target(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    replacement = root / "files" / "custom" / "replacement.nc"

    class ReplacementLocatorHook:
        def resolve_artifact_locator(self, context: OperationContext) -> None:
            context.planned_locators[0] = ArtifactLocator.path(
                replacement,
                relative_path="files/custom/replacement.nc",
            )

    registry = PluginRegistry([ReplacementLocatorHook()])
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "replace-me.nc"
    source.write_text("dummy", encoding="utf-8")

    record = catalog.add_file(source)

    assert Path(record.stored_abspath or "") == replacement
    assert replacement.read_text(encoding="utf-8") == "dummy"


def test_resolve_artifact_locator_removal_fails_clearly(tmp_path: Path) -> None:
    class RemovingLocatorHook:
        def resolve_artifact_locator(self, context: OperationContext) -> None:
            context.planned_locators.clear()

    registry = PluginRegistry([RemovingLocatorHook()])
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "missing-locator.nc"
    source.write_text("dummy", encoding="utf-8")

    with pytest.raises(ValueError, match="removed the planned artifact locator"):
        catalog.add_file(source)

    assert catalog.repository.all() == []
    assert not list((root / "files").rglob("missing-locator.nc"))


def test_hook_failure_rolls_back_staged_record_and_copied_file(tmp_path: Path) -> None:
    rollback_calls: list[str] = []

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            context.rollback(lambda: rollback_calls.append("external"), description="external cleanup")
            raise RuntimeError("simulated hook failure")

    registry = PluginRegistry([FailingHook()])
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "failure.nc"
    source.write_text("dummy", encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated hook failure"):
        catalog.add_file(source)

    assert rollback_calls == ["external"]
    assert catalog.repository.all() == []
    assert list((root / "files").rglob("failure.nc")) == []
    assert source.exists()


def test_metadata_extraction_hook_can_warn_without_failing(tmp_path: Path) -> None:
    class WarningExtractorHook:
        def extract_metadata(self, context: OperationContext) -> dict[str, object]:
            context.add_warning(
                HookWarning(
                    hook_name="filename",
                    message="filename metadata was incomplete",
                    code="filename.incomplete",
                )
            )
            return {"filename_stem": context.source_path.stem if context.source_path is not None else None}

    registry = PluginRegistry([WarningExtractorHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "warning.txt"
    source.write_text("dummy", encoding="utf-8")

    record = catalog.add_file(source)

    assert record.derived_metadata["filename_stem"] == "warning"
    assert record.derived_metadata["hook_warnings"] == [
        {
            "hook_name": "filename",
            "message": "filename metadata was incomplete",
            "code": "filename.incomplete",
        }
    ]


def test_add_artifact_uses_hook_context_and_persists_metadata_mutations(tmp_path: Path) -> None:
    calls: list[str] = []

    class ArtifactHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            context.user_metadata["title"] = "External data"
            calls.append(context.operation_type)

        def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
            assert report.ok
            calls.append(context.record_type)

        def resolve_artifact_locator(self, context: OperationContext) -> None:
            assert context.planned_locators == [ArtifactLocator(kind="uri", value="s3://bucket/data.zarr")]
            calls.append(context.planned_locators[0].kind)

        def extract_metadata(self, context: OperationContext) -> dict[str, object]:
            return {"locator_kind": context.planned_locators[0].kind}

    registry = PluginRegistry([ArtifactHook()])
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="artifacts",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="title",
                        description="Title supplied by hook.",
                        required=True,
                    )
                ]
            ),
        ),
        plugins=registry,
    )

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
    )

    assert calls == ["add_artifact", "external_reference", "uri"]
    assert record.user_metadata["title"] == "External data"
    assert record.derived_metadata["locator_kind"] == "uri"


def test_operation_context_source_compatibility_properties_are_mutable(tmp_path: Path) -> None:
    source_paths: list[Path | None] = []
    descriptors: list[str | None] = []

    class SourceCompatibilityHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            context.source_path = tmp_path / "replacement.txt"
            context.source_descriptor = "replacement descriptor"

        def extract_metadata(self, context: OperationContext) -> dict[str, object]:
            source_paths.append(context.source.path)
            descriptors.append(context.source.descriptor)
            return {}

    registry = PluginRegistry([SourceCompatibilityHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
    )

    assert source_paths == [tmp_path / "replacement.txt"]
    assert descriptors == ["replacement descriptor"]


def test_add_artifact_persists_resolved_locator(tmp_path: Path) -> None:
    class ReplacementLocatorHook:
        def resolve_artifact_locator(self, context: OperationContext) -> None:
            context.planned_locators[0] = ArtifactLocator(kind="uri", value="s3://bucket/replacement.zarr")

    registry = PluginRegistry([ReplacementLocatorHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/original.zarr"),
    )

    assert record.locator == ArtifactLocator(kind="uri", value="s3://bucket/replacement.zarr")


def test_record_write_hooks_fire_for_add_file_and_add_artifact(tmp_path: Path) -> None:
    calls: list[str] = []

    class RecordWriteHook:
        def before_record_write(self, context: OperationContext) -> None:
            calls.append(f"before:{context.operation_type}")

        def after_record_write(self, context: OperationContext) -> None:
            calls.append(f"after:{context.operation_type}")

    registry = PluginRegistry([RecordWriteHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "record-write.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
    )

    assert calls == [
        "before:add_file",
        "after:add_file",
        "before:add_artifact",
        "after:add_artifact",
    ]


def test_before_record_write_metadata_mutation_is_persisted(tmp_path: Path) -> None:
    class RecordMetadataHook:
        def before_record_write(self, context: OperationContext) -> None:
            context.user_metadata["record_phase"] = context.operation_type
            context.derived_metadata["record_hook"] = True

    registry = PluginRegistry([RecordMetadataHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
    )

    assert record.user_metadata["record_phase"] == "add_artifact"
    assert record.derived_metadata["record_hook"] is True


def test_after_commit_hook_failure_does_not_fail_add_file(tmp_path: Path) -> None:
    class FailingAfterCommitHook:
        def after_commit(self, context: OperationContext) -> None:
            raise RuntimeError("post-commit notification failed")

    registry = PluginRegistry([FailingAfterCommitHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "committed.nc"
    source.write_text("dummy", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="FailingAfterCommitHook: .*post-commit notification failed"):
        record = catalog.add_file(source)

    assert record.id is not None
    assert catalog.get(record.id) == record
    assert Path(record.stored_abspath or "").exists()


def test_after_commit_hook_failure_does_not_fail_add_artifact(tmp_path: Path) -> None:
    class FailingAfterCommitHook:
        def after_commit(self, context: OperationContext) -> None:
            raise RuntimeError("post-commit notification failed")

    registry = PluginRegistry([FailingAfterCommitHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.warns(RuntimeWarning, match="FailingAfterCommitHook: .*post-commit notification failed"):
        record = catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/data.zarr"),
        )

    assert record.id is not None
    assert catalog.get(record.id) == record


def test_add_artifact_does_not_auto_rollback_caller_owned_transaction(tmp_path: Path) -> None:
    class FailingAfterWriteHook:
        def after_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("external transaction hook failure")

    registry = PluginRegistry([FailingAfterWriteHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

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


def test_add_artifacts_runs_hooks_for_each_item(tmp_path: Path) -> None:
    calls: list[str] = []

    class BatchHook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(f"{context.operation_type}:{context.source_descriptor}")
            context.user_metadata["seen_by_hook"] = context.source_descriptor or "missing"

    registry = PluginRegistry([BatchHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value="s3://bucket/first.zarr"),
            },
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value="s3://bucket/second.zarr"),
            },
        ]
    )

    assert calls == [
        "add_artifact:s3://bucket/first.zarr",
        "add_artifact:s3://bucket/second.zarr",
    ]
    assert [record.user_metadata["seen_by_hook"] for record in records] == [
        "s3://bucket/first.zarr",
        "s3://bucket/second.zarr",
    ]


def test_add_artifact_is_record_only_without_writer(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=OperationSource(kind="text", descriptor="in-memory text"),
    )

    assert record.locator == ArtifactLocator.path(target)
    assert not target.exists()


def test_plugin_writer_receives_source_and_target_and_persists_metadata(tmp_path: Path) -> None:
    class TextWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            target_path = target.as_path()
            assert target_path is not None
            assert source.kind == "text"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(str(source.metadata["text"]), encoding="utf-8")
            context.rollback(lambda path=target_path: path.unlink(missing_ok=True), description="remove text")
            context.derived_metadata["writer_source_kind"] = source.kind
            context.derived_metadata["writer_target_name"] = target_path.name

    target = tmp_path / "out" / "artifact.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=OperationSource(kind="text", descriptor="text payload", metadata={"text": "hello"}),
        artifact_writer=TextWriter(),
    )

    assert target.read_text(encoding="utf-8") == "hello"
    assert record.derived_metadata["writer_source_kind"] == "text"
    assert record.derived_metadata["writer_target_name"] == "artifact.txt"


def test_plugin_writer_rollback_removes_created_artifact_after_hook_failure(tmp_path: Path) -> None:
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
            context.rollback(lambda path=target_path: path.unlink(missing_ok=True), description="remove text")

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("record hook failed")

    target = tmp_path / "out" / "artifact.txt"
    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.raises(RuntimeError, match="record hook failed"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            source=OperationSource(kind="text", descriptor="text payload"),
            artifact_writer=TextWriter(),
        )

    assert not target.exists()
    assert catalog.repository.all() == []


def test_unzip_style_writer_persists_directory_metadata_and_rolls_back(tmp_path: Path) -> None:
    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            if context.user_metadata.get("fail"):
                raise RuntimeError("fail after unzip")

    class UnzipWriter:
        def write(
            self,
            context: OperationContext,
            source: OperationSource,
            target: ArtifactLocator,
        ) -> None:
            if source.path is None:
                raise ValueError("zip source path is required")
            target_path = target.as_path()
            if target_path is None:
                raise ValueError("zip target path is required")

            target_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source.path) as archive:
                archive.extractall(target_path)
                names = sorted(archive.namelist())

            context.rollback(
                lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
                description=f"remove extracted directory {target_path}",
            )
            context.derived_metadata["file_count"] = len(names)
            extracted_names: list[JsonValue] = list(names)
            context.derived_metadata["extracted_names"] = extracted_names

    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.txt", "alpha")
        archive.writestr("nested/b.txt", "bravo")

    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)
    writer = UnzipWriter()
    extracted = tmp_path / "extracted"

    record = catalog.add_artifact(
        record_type="zip_directory",
        locator=ArtifactLocator.path(extracted),
        source=OperationSource(kind="zip_file", path=archive_path, descriptor=str(archive_path)),
        artifact_writer=writer,
    )

    assert (extracted / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert record.derived_metadata["file_count"] == 2
    assert record.derived_metadata["extracted_names"] == ["a.txt", "nested/b.txt"]

    failing_target = tmp_path / "failing-extracted"
    with pytest.raises(RuntimeError, match="fail after unzip"):
        catalog.add_artifact(
            record_type="zip_directory",
            locator=ArtifactLocator.path(failing_target),
            metadata={"fail": True},
            source=OperationSource(kind="zip_file", path=archive_path, descriptor=str(archive_path)),
            artifact_writer=writer,
        )

    assert not failing_target.exists()
