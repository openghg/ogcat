from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

import ogcat.catalog_application as catalog_application_module
import ogcat.storage_planning as storage_planning
from ogcat import ArtifactLocator, Catalog, CatalogSpec, PluginRegistry, RecordSchema
from ogcat.hooks import OperationContext, OperationSource
from ogcat.models import MetadataDict
from ogcat.reference_planning import plan_reference_locator
from ogcat.storage import (
    LocalStorageAdapter,
    adapter_for_locator,
    create_directory_target,
    ensure_parent_directory,
    ensure_target_absent,
    plan_storage,
    register_remove_on_rollback,
    remove_target,
)


def test_storage_planning_uuid_path_preserves_catalog_and_storage_relative_paths(
    tmp_path: Path,
) -> None:
    """UUID path planning keeps catalog-relative and objects-relative metadata distinct."""
    catalog_root = tmp_path / "catalog"
    objects_root = catalog_root / "data" / "objects"
    planned = storage_planning.uuid_storage_path(
        catalog_root=catalog_root,
        objects_root=objects_root,
        artifact_uuid="abcdef123456",
        original_path=tmp_path / "archive.tar.gz",
    )

    assert planned.target == objects_root / "ab" / "abcdef123456.tar.gz"
    assert planned.catalog_relative_path == "data/objects/ab/abcdef123456.tar.gz"
    assert planned.storage_relative_path == "ab/abcdef123456.tar.gz"


def test_storage_planning_joins_urlpath_roots_without_path_coercion() -> None:
    """URL path joins should preserve the protocol while normalizing boundary slashes."""
    assert storage_planning.join_urlpath("s3://bucket/prefix/", "/nested/file.nc") == (
        "s3://bucket/prefix/nested/file.nc"
    )


def test_storage_planning_relative_path_prefers_local_storage_root(
    tmp_path: Path,
) -> None:
    """Storage-relative metadata is relative to the root when the locator is underneath it."""
    storage_root = tmp_path / "objects"
    locator = ArtifactLocator.from_path(
        storage_root / "ab" / "artifact.nc",
        relative_path="data/objects/ab/artifact.nc",
    )

    assert storage_planning.storage_relative_path_for_locator(locator, storage_root=storage_root) == (
        "ab/artifact.nc"
    )


def test_storage_planning_relative_path_falls_back_to_locator_metadata(
    tmp_path: Path,
) -> None:
    """External local and URL-path locators keep their existing relative-path metadata."""
    storage_root = tmp_path / "objects"
    external_locator = ArtifactLocator.from_path(
        tmp_path / "external" / "artifact.nc",
        relative_path="external/artifact.nc",
    )
    url_locator = ArtifactLocator.from_urlpath(
        "s3://bucket/prefix/artifact.nc",
        relative_path="prefix/artifact.nc",
    )

    assert (
        storage_planning.storage_relative_path_for_locator(
            external_locator,
            storage_root=storage_root,
        )
        == "external/artifact.nc"
    )
    assert storage_planning.storage_relative_path_for_locator(url_locator, storage_root=storage_root) == (
        "prefix/artifact.nc"
    )


def test_primary_storage_planner_uuid_location_builds_storage_plan(tmp_path: Path) -> None:
    """Primary planner owns UUID placement and storage-plan metadata."""
    catalog_root = tmp_path / "catalog"
    result = storage_planning.plan_primary_storage(
        storage_planning.PrimaryStoragePlanningContext(
            catalog_root=catalog_root,
            files_root=catalog_root / "data" / "files",
            objects_root=catalog_root / "data" / "objects",
            operation_id="abcdef1234567890",
            metadata={},
            directory_template="{year_added}/{original_stem}",
            filename_template="{original_filename}",
            source_path=tmp_path / "source.nc",
            storage_root=None,
            date_added="2026-05-15",
            primary_location="uuid",
        )
    )
    plan = result.to_storage_plan(write_mode="copy", ogcat_owned=True)

    expected = catalog_root / "data" / "objects" / "ab" / "abcdef1234567890.nc"
    assert result.locator == ArtifactLocator.from_path(
        expected,
        relative_path="data/objects/ab/abcdef1234567890.nc",
    )
    assert result.storage_relative_path == "ab/abcdef1234567890.nc"
    assert result.resolved_directory == "ab"
    assert result.resolved_filename == "abcdef1234567890.nc"
    assert result.artifact_uuid == "abcdef1234567890"
    assert result.primary_location == "uuid"
    assert plan.locator == result.locator
    assert plan.storage_relative_path == "ab/abcdef1234567890.nc"
    assert plan.artifact_uuid == "abcdef1234567890"
    assert plan.primary_location == "uuid"
    assert plan.write_mode == "copy"
    assert plan.ogcat_owned


def test_primary_storage_planner_template_location_uses_schema_naming(tmp_path: Path) -> None:
    """Primary planner owns template placement without assigning an artifact UUID."""
    catalog_root = tmp_path / "catalog"

    result = storage_planning.plan_primary_storage(
        storage_planning.PrimaryStoragePlanningContext(
            catalog_root=catalog_root,
            files_root=catalog_root / "data" / "files",
            objects_root=catalog_root / "data" / "objects",
            operation_id="abcdef1234567890",
            metadata={"species": "CO2", "year": 2026, "title": "paris"},
            directory_template="{species}/{year}",
            filename_template="{title}{original_suffix}",
            source_path=tmp_path / "source.nc",
            storage_root=None,
            date_added="2026-05-15",
            primary_location="template",
        )
    )

    expected = catalog_root / "data" / "files" / "CO2" / "2026" / "paris.nc"
    assert result.locator == ArtifactLocator.from_path(expected, relative_path="data/files/CO2/2026/paris.nc")
    assert result.storage_relative_path == "CO2/2026/paris.nc"
    assert result.resolved_directory == "CO2/2026"
    assert result.resolved_filename == "paris.nc"
    assert result.artifact_uuid is None
    assert result.primary_location == "template"


def test_primary_storage_planner_user_provided_location_keeps_explicit_locator(
    tmp_path: Path,
) -> None:
    """Primary planner treats explicit locators as caller-selected storage."""
    target = tmp_path / "chosen" / "artifact.nc"
    locator = ArtifactLocator.from_path(target)

    result = storage_planning.plan_primary_storage(
        storage_planning.PrimaryStoragePlanningContext(
            catalog_root=tmp_path / "catalog",
            files_root=tmp_path / "catalog" / "data" / "files",
            objects_root=tmp_path / "catalog" / "data" / "objects",
            operation_id="abcdef1234567890",
            metadata={},
            directory_template="",
            filename_template="",
            source_path=tmp_path / "source.nc",
            storage_root=None,
            date_added="2026-05-15",
            primary_location="user_provided",
            locator=locator,
        )
    )

    assert result.locator == locator
    assert result.storage_relative_path is None
    assert result.resolved_directory == str(target.parent)
    assert result.resolved_filename == "artifact.nc"
    assert result.artifact_uuid is None
    assert result.primary_location == "user_provided"


def test_primary_storage_planner_uuid_urlpath_root_has_remote_plan_metadata(tmp_path: Path) -> None:
    """Primary planner supports UUID placement under fsspec URL roots."""
    result = storage_planning.plan_primary_storage(
        storage_planning.PrimaryStoragePlanningContext(
            catalog_root=tmp_path / "catalog",
            files_root=tmp_path / "catalog" / "data" / "files",
            objects_root=tmp_path / "catalog" / "data" / "objects",
            operation_id="abcdef1234567890",
            metadata={},
            directory_template="{year_added}/{original_stem}",
            filename_template="{original_filename}",
            source_path=tmp_path / "source.nc",
            storage_root="s3://bucket/prefix",
            date_added="2026-05-15",
            primary_location="uuid",
        )
    )
    plan = result.to_storage_plan(write_mode="copy", ogcat_owned=True)

    assert result.locator == ArtifactLocator.from_urlpath("s3://bucket/prefix/ab/abcdef1234567890.nc")
    assert result.locator.relative_path is None
    assert result.storage_relative_path == "ab/abcdef1234567890.nc"
    assert result.resolved_directory == "ab"
    assert result.resolved_filename == "abcdef1234567890.nc"
    assert plan.adapter == "fsspec"
    assert plan.artifact_uuid == "abcdef1234567890"
    assert plan.primary_location == "uuid"


def test_primary_storage_plan_uses_locator_directory_when_hook_target_has_no_relative_path(
    tmp_path: Path,
) -> None:
    """Hook replacement keeps locator-derived directory metadata when no relative path exists."""
    catalog_root = tmp_path / "catalog"
    result = storage_planning.plan_primary_storage(
        storage_planning.PrimaryStoragePlanningContext(
            catalog_root=catalog_root,
            files_root=catalog_root / "data" / "files",
            objects_root=catalog_root / "data" / "objects",
            operation_id="abcdef1234567890",
            metadata={},
            directory_template="{year_added}/{original_stem}",
            filename_template="{original_filename}",
            source_path=tmp_path / "source.nc",
            storage_root=None,
            date_added="2026-05-15",
            primary_location="uuid",
        )
    )
    hook_target = ArtifactLocator.from_urlpath("memory://bucket/redirected/copied.nc")

    plan = result.to_storage_plan(locator=hook_target)

    assert plan.locator == hook_target
    assert plan.storage_relative_path is None
    assert plan.resolved_directory == "memory://bucket/redirected"
    assert plan.resolved_filename == "copied.nc"


def test_reference_planning_resolves_local_path_reference(tmp_path: Path) -> None:
    """Reference planning resolves path-backed references and preserves local path metadata."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")

    plan = plan_reference_locator(source, uri=None, urlpath=None)

    assert plan.locator == ArtifactLocator.from_path(source.resolve())
    assert plan.local_path == source.resolve()


def test_reference_planning_resolves_uri_and_urlpath_references() -> None:
    """Reference planning keeps URI and URL-path references path-free."""
    uri_plan = plan_reference_locator(None, uri="https://example.org/data.nc", urlpath=None)
    urlpath_plan = plan_reference_locator(None, uri=None, urlpath="s3://bucket/data.nc")

    assert uri_plan.locator == ArtifactLocator(kind="uri", value="https://example.org/data.nc")
    assert uri_plan.local_path is None
    assert urlpath_plan.locator == ArtifactLocator.from_urlpath("s3://bucket/data.nc")
    assert urlpath_plan.local_path is None


def test_plan_artifact_storage_uses_uuid_primary_without_writing(tmp_path: Path) -> None:
    """Planning defaults to a UUID primary path without creating files or records."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{species}/{year}",
                filename_template="{title}{original_suffix}",
            ),
        ),
    )

    plan = catalog.plan_artifact_storage(
        source,
        metadata={"species": "CO2", "year": 2024, "title": "paris"},
        write_mode="copy",
    )

    assert plan.artifact_uuid is not None
    expected = root / "data" / "objects" / plan.artifact_uuid[:2] / f"{plan.artifact_uuid}.nc"
    assert plan.locator == ArtifactLocator.from_path(
        expected,
        relative_path=f"data/objects/{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc",
    )
    assert plan.write_mode == "copy"
    assert plan.locator.relative_path == f"data/objects/{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc"
    assert plan.storage_relative_path == f"{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc"
    assert plan.resolved_directory == plan.artifact_uuid[:2]
    assert plan.resolved_filename == f"{plan.artifact_uuid}.nc"
    assert plan.primary_location == "uuid"
    assert plan.ogcat_owned
    assert not expected.exists()
    assert catalog.repository.all() == []


def test_plan_artifact_storage_can_use_template_primary(tmp_path: Path) -> None:
    """Planning can still use schema templates as the primary path."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{species}/{year}",
                filename_template="{title}{original_suffix}",
            ),
        ),
    )

    plan = catalog.plan_artifact_storage(
        source,
        metadata={"species": "CO2", "year": 2024, "title": "paris"},
        write_mode="copy",
        primary_location="template",
    )

    expected = root / "data" / "files" / "CO2" / "2024" / "paris.nc"
    assert plan.locator == ArtifactLocator.from_path(expected, relative_path="data/files/CO2/2024/paris.nc")
    assert plan.storage_relative_path == "CO2/2024/paris.nc"
    assert plan.resolved_directory == "CO2/2024"
    assert plan.resolved_filename == "paris.nc"
    assert plan.primary_location == "template"
    assert not expected.exists()


def test_plan_artifact_storage_template_primary_rejects_artifact_uuid_metadata(
    tmp_path: Path,
) -> None:
    """Dry-run template planning rejects metadata that would shadow planner fields."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    with pytest.raises(ValueError, match="Metadata cannot use reserved template field\\(s\\): artifact_uuid"):
        catalog.plan_artifact_storage(
            source,
            metadata={"artifact_uuid": "user-value"},
            primary_location="template",
        )


def test_add_artifact_uses_planned_timestamp_for_date_named_paths(tmp_path: Path) -> None:
    """Records created from plans keep the timestamp used for date templates."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(directory_template="{year_added}", filename_template="{title}.txt"),
        ),
    )

    metadata: MetadataDict = {"title": "planned"}
    plan = catalog.plan_artifact_storage(
        metadata=metadata,
        write_mode="reference",
        primary_location="template",
    )
    record = catalog.add_artifact(
        record_type="managed_artifact",
        storage_plan=plan,
        metadata=metadata,
    )

    assert plan.time_added is not None
    assert record.time_added == plan.time_added
    assert record.locator.relative_path == f"data/files/{plan.time_added[:4]}/planned.txt"
    assert record.user_metadata == metadata
    assert record.naming_metadata["resolved_filename"] == "planned.txt"


def test_plan_artifact_storage_collision_uses_storage_adapter_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local planning asks the storage adapter when allocating a suffix."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(directory_template="", filename_template="fixed{original_suffix}"),
        ),
    )
    seen: list[str] = []

    def fake_exists(self: LocalStorageAdapter, locator: ArtifactLocator) -> bool:
        seen.append(locator.value)
        return Path(locator.value).name == "fixed.nc"

    monkeypatch.setattr(LocalStorageAdapter, "exists", fake_exists)

    plan = catalog.plan_artifact_storage(source, write_mode="copy", primary_location="template")

    assert Path(plan.locator.value).name == "fixed_2.nc"
    assert [Path(value).name for value in seen] == ["fixed.nc", "fixed_2.nc"]


def test_plan_artifact_storage_urlpath_root_does_not_import_fsspec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote planning skips fsspec imports when the optional dependency is absent."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{species}", filename_template="{title}{original_suffix}"
            ),
        ),
    )
    real_import_module = importlib.import_module
    real_find_spec = importlib.util.find_spec

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "fsspec":
            raise AssertionError("planning should not import fsspec")
        return real_import_module(name, package)

    def fake_find_spec(name: str):
        if name == "fsspec":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    plan = catalog.plan_artifact_storage(
        source,
        metadata={"species": "CO2", "title": "remote"},
        storage_root="s3://bucket/prefix",
        write_mode="copy",
        primary_location="template",
    )

    assert plan.locator == ArtifactLocator.from_urlpath(
        "s3://bucket/prefix/CO2/remote.nc",
    )
    assert plan.locator.relative_path is None
    assert plan.adapter == "fsspec"
    assert plan.storage_relative_path == "CO2/remote.nc"
    assert plan.resolved_directory == "CO2"
    assert plan.resolved_filename == "remote.nc"


def test_plan_artifact_storage_uuid_urlpath_root_uses_root_relative_metadata(
    tmp_path: Path,
) -> None:
    """UUID primary URL roots produce fsspec plans with root-relative metadata."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    plan = catalog.plan_artifact_storage(
        source,
        storage_root="s3://bucket/prefix",
        write_mode="copy",
    )

    assert plan.artifact_uuid is not None
    assert plan.locator == ArtifactLocator.from_urlpath(
        f"s3://bucket/prefix/{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc"
    )
    assert plan.locator.relative_path is None
    assert plan.adapter == "fsspec"
    assert plan.storage_relative_path == f"{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc"
    assert plan.resolved_directory == plan.artifact_uuid[:2]
    assert plan.resolved_filename == f"{plan.artifact_uuid}.nc"
    assert plan.primary_location == "uuid"


def test_urlpath_storage_root_does_not_populate_stored_relpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote planned locators do not fill local compatibility path fields."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{species}", filename_template="{title}{original_suffix}"
            ),
        ),
    )
    monkeypatch.setattr(storage_planning, "urlpath_exists_if_supported", lambda _urlpath: False)

    plan = catalog.plan_artifact_storage(
        source,
        metadata={"species": "CO2", "title": "remote"},
        storage_root="s3://bucket/prefix",
        write_mode="reference",
        primary_location="template",
    )
    record = catalog.add_artifact(
        record_type="managed_file",
        storage_plan=plan,
        metadata={"species": "CO2", "title": "remote"},
    )

    assert record.locator.kind == "urlpath"
    assert record.locator.relative_path is None
    assert record.stored_relpath is None


def test_urlpath_locator_relative_path_does_not_populate_stored_relpath(tmp_path: Path) -> None:
    """Explicit URL-path relative paths do not fill local compatibility fields."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    record = catalog.add_artifact(
        record_type="remote_file",
        locator=ArtifactLocator.from_urlpath(
            "s3://bucket/prefix/example.nc",
            relative_path="prefix/example.nc",
        ),
    )

    assert record.locator.relative_path == "prefix/example.nc"
    assert record.stored_relpath is None


def test_plan_artifact_storage_urlpath_root_uses_available_collision_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote planning allocates a unique suffix when a URL path is known to exist."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files", default_schema=RecordSchema(filename_template="fixed{original_suffix}")
        ),
    )
    seen: list[str] = []

    def fake_exists(urlpath: str) -> bool:
        seen.append(urlpath)
        return urlpath.endswith("/fixed.nc")

    monkeypatch.setattr(storage_planning, "urlpath_exists_if_supported", fake_exists)

    plan = catalog.plan_artifact_storage(
        source,
        storage_root="s3://bucket/prefix",
        write_mode="copy",
        primary_location="template",
    )

    assert plan.locator == ArtifactLocator.from_urlpath(
        "s3://bucket/prefix/2026/source/fixed_2.nc",
    )
    assert plan.locator.relative_path is None
    assert seen == (
        [
            "s3://bucket/prefix/2026/source/fixed.nc",
            "s3://bucket/prefix/2026/source/fixed_2.nc",
        ]
    )


def test_plan_artifact_storage_custom_local_root_has_no_catalog_relative_path(tmp_path: Path) -> None:
    """Custom local storage roots do not populate catalog-relative compatibility paths."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    external_root = tmp_path / "external-root"
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{species}", filename_template="{title}{original_suffix}"
            ),
        ),
    )

    plan = catalog.plan_artifact_storage(
        source,
        metadata={"species": "CO2", "title": "external"},
        storage_root=external_root,
        write_mode="reference",
        ogcat_owned=False,
        primary_location="template",
    )
    record = catalog.add_artifact(
        record_type="external_file",
        storage_plan=plan,
        metadata={"species": "CO2", "title": "external"},
    )

    assert plan.locator == ArtifactLocator.from_path(external_root / "CO2" / "external.nc")
    assert plan.storage_relative_path == "CO2/external.nc"
    assert plan.resolved_directory == "CO2"
    assert plan.resolved_filename == "external.nc"
    assert record.stored_relpath is None
    assert record.locator.relative_path is None


def test_plan_artifact_storage_uuid_custom_local_root_has_no_catalog_relative_path(
    tmp_path: Path,
) -> None:
    """UUID primary custom local roots keep storage metadata root-relative."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    external_root = tmp_path / "external-root"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    plan = catalog.plan_artifact_storage(
        source,
        storage_root=external_root,
        write_mode="reference",
        ogcat_owned=False,
    )

    assert plan.artifact_uuid is not None
    assert plan.locator == ArtifactLocator.from_path(
        external_root / plan.artifact_uuid[:2] / f"{plan.artifact_uuid}.nc"
    )
    assert plan.locator.relative_path is None
    assert plan.storage_relative_path == f"{plan.artifact_uuid[:2]}/{plan.artifact_uuid}.nc"
    assert plan.resolved_directory == plan.artifact_uuid[:2]
    assert plan.resolved_filename == f"{plan.artifact_uuid}.nc"
    assert plan.primary_location == "uuid"


def test_plan_artifact_storage_explicit_locator_overrides_primary_location(tmp_path: Path) -> None:
    """Explicit locators remain caller-selected primary storage locations."""
    source = tmp_path / "source.nc"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    target = tmp_path / "chosen" / "artifact.nc"

    plan = catalog.plan_artifact_storage(
        source,
        locator=ArtifactLocator.from_path(target),
        write_mode="reference",
    )

    assert plan.locator == ArtifactLocator.from_path(target)
    assert plan.primary_location == "user_provided"
    assert plan.artifact_uuid is None
    assert plan.resolved_directory == str(target.parent)
    assert plan.resolved_filename == "artifact.nc"


def test_add_file_hook_urlpath_redirect_updates_plan_and_skips_path_extractor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook-redirected add_file operations keep plan metadata consistent."""

    class RedirectHook:
        def resolve_artifact_locator(self, context: OperationContext) -> None:
            context.planned_locators = [
                ArtifactLocator.from_urlpath(
                    "memory://bucket/copied.nc",
                    relative_path="copied.nc",
                )
            ]

        def before_record_write(self, context: OperationContext) -> None:
            assert context.storage_plan is not None
            assert context.storage_plan.locator.kind == "urlpath"
            assert context.storage_plan.adapter == "fsspec"

    def fake_write(self, context: OperationContext, source: OperationSource, target: ArtifactLocator) -> None:
        assert source.path == source_file
        assert target.kind == "urlpath"
        context.derived_metadata["writer"] = "redirected"

    def fail_extract(path: Path) -> dict[str, object]:
        raise AssertionError(f"path extractor should not run for redirected target {path}")

    source_file = tmp_path / "source.nc"
    source_file.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="files"),
        plugins=PluginRegistry([RedirectHook()]),
    )
    monkeypatch.setattr(catalog_application_module.CopyArtifactWriter, "write", fake_write)
    monkeypatch.setattr(catalog_application_module, "extract_derived_metadata", fail_extract)

    record = catalog.add_file(source_file, operation="copy")

    assert record.locator.kind == "urlpath"
    assert record.derived_metadata["writer"] == "redirected"
    assert record.naming_metadata["storage_relative_path"] == "copied.nc"
    assert record.naming_metadata["resolved_directory"] == ""
    assert record.naming_metadata["resolved_filename"] == "copied.nc"


def test_add_artifact_records_external_uri_without_existence_check(tmp_path: Path) -> None:
    """External URI references are recorded without filesystem checks."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="ssh://example.org/data/example.nc"),
        storage_mode="external",
    )

    assert record.locator.kind == "uri"
    assert record.storage_mode == "external"
    assert record.stored_abspath is None


def test_urlpath_adapter_reports_missing_fsspec_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL-path adapters raise the optional fsspec error only when used."""
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "fsspec":
            raise ModuleNotFoundError(name)
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    local_adapter = adapter_for_locator(ArtifactLocator.from_path("/tmp/example.nc"))
    assert isinstance(local_adapter, LocalStorageAdapter)
    with pytest.raises(RuntimeError, match="Install with 'ogcat\\[fsspec\\]'"):
        adapter_for_locator(ArtifactLocator.from_urlpath("memory://example.nc")).exists(
            ArtifactLocator.from_urlpath("memory://example.nc")
        )


def test_plan_storage_accepts_urlpath_locator_without_importing_fsspec() -> None:
    """StoragePlan construction is descriptive and does not load fsspec."""
    plan = plan_storage(
        ArtifactLocator.from_urlpath("ssh://example.org/path/data.zarr"),
        target_kind="directory",
        write_mode="reference",
        ogcat_owned=False,
    )

    assert plan.locator.kind == "urlpath"
    assert plan.write_mode == "reference"


def test_artifact_locator_constructor_aliases(tmp_path: Path) -> None:
    """The path constructor alias remains compatible with from_path."""
    path = tmp_path / "example.nc"

    assert ArtifactLocator.from_path(path) == ArtifactLocator.path(path)


def test_storage_helpers_prepare_and_remove_local_targets(tmp_path: Path) -> None:
    """Storage helper functions create parents, reject collisions, and remove targets."""
    file_locator = ArtifactLocator.from_path(tmp_path / "nested" / "output.txt")
    directory_locator = ArtifactLocator.from_path(tmp_path / "store.zarr")

    ensure_parent_directory(file_locator)
    assert (tmp_path / "nested").is_dir()
    ensure_target_absent(file_locator)
    (tmp_path / "nested" / "output.txt").write_text("payload", encoding="utf-8")
    with pytest.raises(FileExistsError, match="target already exists"):
        ensure_target_absent(file_locator)

    create_directory_target(directory_locator)
    assert (tmp_path / "store.zarr").is_dir()
    with pytest.raises(FileExistsError, match="target already exists"):
        create_directory_target(directory_locator)

    remove_target(file_locator)
    remove_target(directory_locator, target_kind="directory")
    assert not (tmp_path / "nested" / "output.txt").exists()
    assert not (tmp_path / "store.zarr").exists()


def test_register_remove_on_rollback_uses_keyword_description(tmp_path: Path) -> None:
    """Rollback helper passes descriptions using the OperationContext-compatible keyword."""
    target = tmp_path / "written.txt"
    target.write_text("payload", encoding="utf-8")
    actions = []
    descriptions: list[str | None] = []

    def rollback(action, *, description: str | None = None):
        actions.append(action)
        descriptions.append(description)

    register_remove_on_rollback(
        rollback,
        ArtifactLocator.from_path(target),
        description="remove test target",
    )

    assert descriptions == ["remove test target"]
    actions[0]()
    assert not target.exists()
