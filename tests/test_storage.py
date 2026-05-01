from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec, RecordSchema
from ogcat.storage import LocalStorageBackend, backend_for_locator, plan_storage


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
    assert plan.locator == ArtifactLocator.path(expected, relative_path="files/CO2/2024/paris.nc")
    assert plan.write_mode == "copy"
    assert plan.ogcat_owned
    assert not expected.exists()
    assert catalog.repository.all() == []


def test_plan_artifact_collision_uses_storage_backend_exists(
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

    def fake_exists(self: LocalStorageBackend, locator: ArtifactLocator) -> bool:
        seen.append(locator.value)
        return Path(locator.value).name == "fixed.nc"

    monkeypatch.setattr(LocalStorageBackend, "exists", fake_exists)

    plan = catalog.plan_artifact(source, write_mode="copy")

    assert Path(plan.locator.value).name == "fixed_2.nc"
    assert [Path(value).name for value in seen] == ["fixed.nc", "fixed_2.nc"]


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


def test_urlpath_backend_reports_missing_fsspec_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "fsspec":
            raise ModuleNotFoundError(name)
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    local_backend = backend_for_locator(ArtifactLocator.path("/tmp/example.nc"))
    assert isinstance(local_backend, LocalStorageBackend)
    with pytest.raises(RuntimeError, match="Install with 'ogcat\\[fsspec\\]'"):
        backend_for_locator(ArtifactLocator.urlpath("memory://example.nc")).exists(
            ArtifactLocator.urlpath("memory://example.nc")
        )


def test_plan_storage_accepts_urlpath_locator_without_importing_fsspec() -> None:
    plan = plan_storage(
        ArtifactLocator.urlpath("ssh://example.org/path/data.zarr"),
        target_kind="directory",
        write_mode="reference",
        ogcat_owned=False,
    )

    assert plan.locator.kind == "urlpath"
    assert plan.write_mode == "reference"
