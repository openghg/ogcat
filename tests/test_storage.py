from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import ogcat.catalog as catalog_module
from ogcat import ArtifactLocator, Catalog, CatalogSpec, PluginRegistry, RecordSchema
from ogcat.hooks import OperationContext, OperationSource
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


def test_plan_artifact_renders_local_template_without_writing(tmp_path: Path) -> None:
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

    plan = catalog.plan_artifact(
        source,
        metadata={"species": "CO2", "year": 2024, "title": "paris"},
        write_mode="copy",
    )

    expected = root / "files" / "CO2" / "2024" / "paris.nc"
    assert plan.locator == ArtifactLocator.from_path(expected, relative_path="files/CO2/2024/paris.nc")
    assert plan.write_mode == "copy"
    assert plan.ogcat_owned
    assert not expected.exists()
    assert catalog.repository.all() == []


def test_plan_artifact_collision_uses_storage_adapter_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    plan = catalog.plan_artifact(source, write_mode="copy")

    assert Path(plan.locator.value).name == "fixed_2.nc"
    assert [Path(value).name for value in seen] == ["fixed.nc", "fixed_2.nc"]


def test_plan_artifact_urlpath_root_does_not_import_fsspec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "fsspec":
            raise AssertionError("planning should not import fsspec")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    plan = catalog.plan_artifact(
        source,
        metadata={"species": "CO2", "title": "remote"},
        storage_root="s3://bucket/prefix",
        write_mode="copy",
    )

    assert plan.locator == ArtifactLocator.from_urlpath(
        "s3://bucket/prefix/CO2/remote.nc",
        relative_path="CO2/remote.nc",
    )
    assert plan.adapter == "fsspec"


def test_plan_artifact_custom_local_root_has_no_catalog_relative_path(tmp_path: Path) -> None:
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

    plan = catalog.plan_artifact(
        source,
        metadata={"species": "CO2", "title": "external"},
        storage_root=external_root,
        write_mode="reference",
        ogcat_owned=False,
    )
    record = catalog.add_artifact(record_type="external_file", storage_plan=plan)

    assert plan.locator == ArtifactLocator.from_path(external_root / "CO2" / "external.nc")
    assert record.stored_relpath is None
    assert record.locator.relative_path is None


def test_add_file_hook_urlpath_redirect_updates_plan_and_skips_path_extractor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(catalog_module.CopyArtifactWriter, "write", fake_write)
    monkeypatch.setattr(catalog_module, "extract_derived_metadata", fail_extract)

    record = catalog.add_file(source_file, operation="copy")

    assert record.locator.kind == "urlpath"
    assert record.derived_metadata["writer"] == "redirected"


def test_add_artifact_records_external_uri_without_existence_check(tmp_path: Path) -> None:
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
    plan = plan_storage(
        ArtifactLocator.from_urlpath("ssh://example.org/path/data.zarr"),
        target_kind="directory",
        write_mode="reference",
        ogcat_owned=False,
    )

    assert plan.locator.kind == "urlpath"
    assert plan.write_mode == "reference"


def test_artifact_locator_constructor_aliases(tmp_path: Path) -> None:
    path = tmp_path / "example.nc"

    assert ArtifactLocator.from_path(path) == ArtifactLocator.path(path)
    assert ArtifactLocator.from_urlpath("memory://example.nc") == ArtifactLocator.urlpath(
        "memory://example.nc"
    )


def test_storage_helpers_prepare_and_remove_local_targets(tmp_path: Path) -> None:
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
