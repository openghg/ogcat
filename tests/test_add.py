from pathlib import Path

from ogcat import Catalog, CatalogSpec


def test_add_file_uses_generic_default_storage_layout(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "files" / record.time_added[:4] / "example" / "example.nc"
    assert Path(record.stored_abspath) == expected
    assert expected.exists()
    assert record.original_filename == "example.nc"
    assert record.suffixes == [".nc"]
    assert record.storage_mode == "copy"


def test_add_file_supports_flux_style_templates_when_requested(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            directory_template=(
                "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                "{version|unversioned}/{flux_type|misc}"
            ),
            filename_template=(
                "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                "{original_suffix}"
            ),
        ),
    )

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


def test_add_file_preserves_dotted_stems_and_simple_suffixes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "files" / record.time_added[:4] / "anthropogenic.202401" / "anthropogenic.202401.nc"
    assert Path(record.stored_abspath) == expected
    assert record.original_filename == "anthropogenic.202401.nc"
    assert record.suffixes == [".202401", ".nc"]


def test_add_file_preserves_compressed_suffixes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "archive.tar.gz"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "files" / record.time_added[:4] / "archive" / "archive.tar.gz"
    assert Path(record.stored_abspath) == expected
    assert record.original_filename == "archive.tar.gz"
    assert record.suffixes == [".tar", ".gz"]


def test_add_file_appends_numeric_suffix_on_collision(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.nc"
    second = source_dir / "second.nc"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            directory_template=(
                "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                "{version|unversioned}/{flux_type|misc}"
            ),
            filename_template=(
                "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                "{original_suffix}"
            ),
        ),
    )
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


def test_add_file_collision_suffixing_preserves_full_extension(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.tar.gz"
    second = source_dir / "second.tar.gz"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="archives",
            directory_template="{year_added}",
            filename_template="bundle{original_suffix}",
        ),
    )

    first_record = catalog.add_file(first)
    second_record = catalog.add_file(second)

    assert Path(first_record.stored_abspath).name == "bundle.tar.gz"
    assert Path(second_record.stored_abspath).name == "bundle_2.tar.gz"
