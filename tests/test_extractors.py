from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ogcat import Catalog, CatalogSpec, extractors
from ogcat.extractors import extract_derived_metadata


def test_extract_derived_metadata_skips_optional_netcdf_extractor_when_xarray_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "example.nc"
    source.write_text("not really netcdf", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "xarray", None)

    assert extract_derived_metadata(source) == {}


def test_extract_derived_metadata_can_report_extractor_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    class BrokenExtractor:
        name = "broken"

        def can_extract(self, path: Path) -> bool:
            return path.suffix == ".nc"

        def extract(self, path: Path) -> None:
            raise ValueError("simulated extractor failure")

    monkeypatch.setattr(extractors, "_EXTRACTORS", (BrokenExtractor(),))

    assert extract_derived_metadata(source) == {}
    assert extract_derived_metadata(source, include_errors=True) == {
        "extractor_errors": {"broken": "ValueError: simulated extractor failure"}
    }


def test_extract_derived_metadata_can_report_extractor_selection_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Broken extractor selection should not fail best-effort metadata extraction."""
    source = tmp_path / "example.zarr"
    source.mkdir()

    class BrokenCanExtract:
        name = "broken_can_extract"

        def can_extract(self, path: Path) -> bool:
            raise TypeError("simulated selection failure")

        def extract(self, path: Path) -> None:
            raise AssertionError("selection failure should skip extraction")

    monkeypatch.setattr(extractors, "_EXTRACTORS", (BrokenCanExtract(),))

    assert extract_derived_metadata(source) == {}
    assert extract_derived_metadata(source, include_errors=True) == {
        "extractor_errors": {"broken_can_extract": "TypeError: simulated selection failure"}
    }


def test_add_file_zarr_directory_keeps_working_when_extractor_selection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default extractor selection errors should not block managed directory ingest."""
    source = tmp_path / "example.zarr"
    source.mkdir()
    (source / "zarr.json").write_text("{}", encoding="utf-8")

    class BrokenCanExtract:
        name = "broken_can_extract"

        def can_extract(self, path: Path) -> bool:
            raise TypeError("simulated selection failure")

        def extract(self, path: Path) -> None:
            raise AssertionError("selection failure should skip extraction")

    monkeypatch.setattr(extractors, "_EXTRACTORS", (BrokenCanExtract(),))

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source, create_template_replica=False)

    stored_path = record.path()
    assert stored_path is not None
    assert stored_path.is_dir()
    assert "extractor_errors" not in record.derived_metadata


def test_extract_derived_metadata_can_report_unreadable_netcdf_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.nc"

    monkeypatch.setitem(sys.modules, "xarray", SimpleNamespace())

    assert extract_derived_metadata(missing) == {}
    assert extract_derived_metadata(missing, include_errors=True) == {
        "extractor_errors": {"netcdf": f"FileNotFoundError: [Errno 2] No such file or directory: '{missing}'"}
    }


def test_add_file_keeps_working_for_netcdf_suffix_without_xarray(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "xarray", None)

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    assert "netcdf" not in record.derived_metadata
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)
    assert classification["format"] == "netcdf"


def test_add_file_extracts_small_netcdf_metadata_when_xarray_is_available(tmp_path: Path) -> None:
    xarray = pytest.importorskip("xarray")

    try:
        dataset = xarray.Dataset(
            data_vars={
                "temperature": (("time", "lat"), [[280.0, 281.5], [279.5, 280.5]]),
            },
            coords={
                "time": [0, 1],
                "lat": [51.0, 52.0],
            },
            attrs={
                "title": "Example dataset",
                "institution": "OpenAI",
                "ignored_attr": "should not be persisted",
            },
        )
        source = tmp_path / "example.nc"
        dataset.to_netcdf(source)
    except Exception as exc:  # pragma: no cover - depends on optional backend availability
        pytest.skip(f"xarray is installed, but no writable netCDF backend is available: {exc}")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    assert record.derived_metadata["netcdf"] == {
        "dims": {"lat": 2, "time": 2},
        "data_vars": ["temperature"],
        "coords": ["lat", "time"],
        "attrs": {
            "title": "Example dataset",
            "institution": "OpenAI",
        },
    }
