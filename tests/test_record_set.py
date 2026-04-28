from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rich.table import Table

import ogcat.record_set as record_set_module
from ogcat import Catalog, CatalogRecordSet, CatalogSpec


def _create_catalog(tmp_path: Path) -> Catalog:
    source = tmp_path / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(
        source,
        metadata={
            "title": "Anthropogenic test flux",
            "product": "CTE-HR",
            "species": "CO2",
        },
    )
    record.derived_metadata["netcdf"] = {"dims": {"time": 12}}
    catalog.repository.update(record)
    return catalog


def test_search_can_return_record_set_with_cli_style_field_selection(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    results = catalog.search(where={"species": "CO2"}, as_record_set=True)

    assert isinstance(results, CatalogRecordSet)
    assert len(results) == 1
    assert results.select("id", "species", "path", "derived_metadata.netcdf.dims.time") == [
        {
            "id": results[0].id,
            "species": "CO2",
            "path": str(results[0].path()),
            "derived_metadata.netcdf.dims.time": 12,
        }
    ]
    assert results[0:1].select("species") == [{"species": "CO2"}]


def test_record_set_to_dataframe_raises_without_pandas(tmp_path: Path, monkeypatch) -> None:
    catalog = _create_catalog(tmp_path)
    results = catalog.search(as_record_set=True)

    def _raise_import_error(name: str) -> object:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(record_set_module, "import_module", _raise_import_error)

    try:
        results.to_dataframe()
    except ImportError as exc:
        assert "Install pandas" in str(exc)
    else:
        raise AssertionError("Expected ImportError when pandas is unavailable.")


def test_record_set_to_dataframe_uses_selected_rows_when_pandas_is_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog = _create_catalog(tmp_path)
    results = catalog.search(as_record_set=True)

    class FakeDataFrame:
        @classmethod
        def from_records(cls, records: list[dict[str, object]]) -> list[dict[str, object]]:
            return records

    monkeypatch.setattr(
        record_set_module,
        "import_module",
        lambda name: SimpleNamespace(DataFrame=FakeDataFrame),
    )

    frame = results.to_dataframe(fields=["id", "species"])

    assert frame == [{"id": results[0].id, "species": "CO2"}]


def test_record_set_rich_preview_returns_table(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    preview = catalog.search(as_record_set=True).preview(fields=["id", "species"])

    assert isinstance(preview, Table)
    assert [column.header for column in preview.columns] == ["id", "species"]
