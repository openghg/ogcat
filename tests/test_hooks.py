from __future__ import annotations

from pathlib import Path

import pytest

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogSpec,
    HookWarning,
    MetadataFieldDescription,
    PluginRegistry,
    RecordSchema,
)
from ogcat.hooks import OperationContext
from ogcat.validation import ValidationReport


def test_direct_registration_invokes_hook(tmp_path: Path) -> None:
    calls: list[str] = []

    class Hook:
        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(context.operation)

    registry = PluginRegistry()
    registry.register(Hook())
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"), plugins=registry)
    source = tmp_path / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog.add_file(source)

    assert calls == ["add_file"]


def test_hooks_run_in_registration_order(tmp_path: Path) -> None:
    calls: list[str] = []

    class OrderedHook:
        def __init__(self, name: str) -> None:
            self.name = name

        def before_validate_metadata(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:before_validate_metadata")

        def plan_locator(self, context: OperationContext) -> None:
            calls.append(f"{self.name}:plan_locator")

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
        "first:plan_locator",
        "second:plan_locator",
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


def test_hook_failure_rolls_back_staged_record_and_copied_file(tmp_path: Path) -> None:
    rollback_calls: list[str] = []

    class FailingHook:
        def after_write_artifact(self, context: OperationContext) -> None:
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
            calls.append(context.operation)

        def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
            assert report.ok
            assert context.planned_locators == [ArtifactLocator(kind="uri", value="s3://bucket/data.zarr")]
            calls.append(context.record_type)

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

    assert calls == ["add_artifact", "external_reference"]
    assert record.user_metadata["title"] == "External data"
    assert record.derived_metadata["locator_kind"] == "uri"
