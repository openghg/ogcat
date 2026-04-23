from pathlib import Path

from ogcat import Catalog, CatalogSpec


def test_add_file_uses_expected_storage_layout(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="fluxes"))

    record = catalog.add_file(
        source,
        metadata={
            "product": "CTE-HR",
            "version": "v4.2",
            "species": "CO2",
            "domain": "EUROPE",
            "flux_type": "anthropogenic",
            "year": 2024,
            "month": 1,
        },
    )

    expected = (
        root
        / "files"
        / "CO2"
        / "EUROPE"
        / "CTE-HR"
        / "v4.2"
        / "anthropogenic"
        / "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc"
    )
    assert Path(record.stored_abspath) == expected
    assert expected.exists()
    assert record.original_filename == "anthropogenic.202401.nc"
    assert record.storage_mode == "copy"


def test_add_file_appends_numeric_suffix_on_collision(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.nc"
    second = source_dir / "second.nc"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="fluxes"))
    metadata = {
        "product": "CTE-HR",
        "version": "v4.2",
        "species": "CO2",
        "domain": "EUROPE",
        "flux_type": "anthropogenic",
        "year": 2024,
        "month": 1,
    }

    first_record = catalog.add_file(first, metadata=metadata)
    second_record = catalog.add_file(second, metadata=metadata)

    assert Path(first_record.stored_abspath).name == "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc"
    assert Path(second_record.stored_abspath).name == "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401_2.nc"
