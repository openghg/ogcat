from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

from ogcat import Catalog

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "catalog_fluxes.py"
SPEC = importlib.util.spec_from_file_location("catalog_fluxes_example", EXAMPLE_PATH)
assert SPEC is not None
catalog_fluxes = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = catalog_fluxes
SPEC.loader.exec_module(catalog_fluxes)

archive_metadata = catalog_fluxes.archive_metadata
build_catalog = catalog_fluxes.build_catalog
build_symlink_view = catalog_fluxes.build_symlink_view
discover_paths_from_listing = catalog_fluxes.discover_paths_from_listing
parse_flux_metadata = catalog_fluxes.parse_flux_metadata


def test_discover_paths_from_listing_skips_directory_entries(tmp_path: Path) -> None:
    listing = tmp_path / "fluxes_ls.txt"
    listing.write_text(
        "\n".join(
            [
                "/group/chem/acrg/ES/fluxes:",
                "EDGAR",
                "EUROPE",
                "CTE-HR-ffCO2-2021_to2022.nc",
                "/group/chem/acrg/ES/fluxes/EDGAR:",
                "EDGAR_v8.0",
                "EDGAR_v6.0.tar.gz",
                "/group/chem/acrg/ES/fluxes/EDGAR/EDGAR_v8.0:",
                "WASTE",
                "/group/chem/acrg/ES/fluxes/EDGAR/EDGAR_v8.0/WASTE:",
                "v8.0_FT2022_GHG_CO2_2000_WASTE_flx.nc",
                "/group/chem/acrg/ES/fluxes/EUROPE:",
            ]
        ),
        encoding="utf-8",
    )

    paths = [source.path.as_posix() for source in discover_paths_from_listing(listing)]

    assert paths == [
        "/group/chem/acrg/ES/fluxes/CTE-HR-ffCO2-2021_to2022.nc",
        "/group/chem/acrg/ES/fluxes/EDGAR/EDGAR_v6.0.tar.gz",
        "/group/chem/acrg/ES/fluxes/EDGAR/EDGAR_v8.0/WASTE/v8.0_FT2022_GHG_CO2_2000_WASTE_flx.nc",
    ]


def test_parse_flux_metadata_for_europe_edgar_sector() -> None:
    path = Path(
        "/group/chem/acrg/ES/fluxes/EUROPE/CO2/edgarv8/agric/"
        "EUROPE-co2-edgarv8-agric-2012.nc"
    )

    metadata = parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/ES/fluxes"),
        discovery_mode="listing",
    )

    assert metadata["relative_source_path"] == "EUROPE/CO2/edgarv8/agric/EUROPE-co2-edgarv8-agric-2012.nc"
    assert metadata["top_collection"] == "EUROPE"
    assert metadata["domain"] == "EUROPE"
    assert metadata["species"] == "CO2"
    assert metadata["product"] == "edgarv8"
    assert metadata["sector"] == "agric"
    assert metadata["year"] == 2012
    assert metadata["file_role"] == "netcdf"


def test_parse_flux_metadata_for_archive_and_date_range() -> None:
    path = Path("/group/chem/acrg/ES/fluxes/GridFEDv2024.0/GCP-GridFEDv2024.0_2012.zip")

    metadata = parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/ES/fluxes"),
        discovery_mode="listing",
    )

    assert metadata["top_collection"] == "GridFEDv2024.0"
    assert metadata["product"] == "GridFEDv2024.0"
    assert metadata["version"] == "v2024.0"
    assert metadata["year"] == 2012
    assert metadata["archive_format"] == "zip"
    assert metadata["file_role"] == "archive"


def test_parse_flux_metadata_for_compressed_netcdf() -> None:
    path = Path("/group/chem/acrg/ES/fluxes/APO/JenaCarboscope/apo99XS_v2022_daily.nc.gz")

    metadata = parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/ES/fluxes"),
        discovery_mode="listing",
    )

    assert metadata["species"] == "APO"
    assert metadata["product"] == "JenaCarboscope"
    assert metadata["archive_format"] == "gz"
    assert metadata["inner_suffix"] == ".nc"
    assert metadata["temporal_resolution"] == "daily"
    assert metadata["file_role"] == "compressed_netcdf"


def test_archive_metadata_records_zip_members(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("nested/a.nc", "a")
        zip_file.writestr("nested/b.nc", "bb")

    metadata = archive_metadata(archive)

    assert metadata == {
        "format": "zip",
        "member_count": 2,
        "members_sample": ["nested/a.nc", "nested/b.nc"],
        "total_uncompressed_size": 3,
    }


def test_build_catalog_from_listing_creates_external_reference_records(tmp_path: Path) -> None:
    listing = tmp_path / "fluxes_ls.txt"
    listing.write_text(
        "\n".join(
            [
                "/group/chem/acrg/ES/fluxes:",
                "EUROPE",
                "/group/chem/acrg/ES/fluxes/EUROPE:",
                "CO2",
                "/group/chem/acrg/ES/fluxes/EUROPE/CO2:",
                "edgarv8",
                "/group/chem/acrg/ES/fluxes/EUROPE/CO2/edgarv8:",
                "agric",
                "/group/chem/acrg/ES/fluxes/EUROPE/CO2/edgarv8/agric:",
                "EUROPE-co2-edgarv8-agric-2012.nc",
            ]
        ),
        encoding="utf-8",
    )

    catalog, added_count = build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=None,
        listing_path=listing,
        enrich=True,
    )
    records = catalog.search()

    assert added_count == 1
    assert len(records) == 1
    assert records[0].record_type == "external_reference"
    assert records[0].storage_mode == "external"
    assert records[0].user_metadata["discovery_mode"] == "listing"
    assert records[0].derived_metadata == {}


def test_build_catalog_from_mounted_scan_adds_filesystem_metadata(tmp_path: Path) -> None:
    source_root = tmp_path / "fluxes"
    source_dir = source_root / "EUROPE" / "CO2" / "edgarv8" / "agric"
    source_dir.mkdir(parents=True)
    source = source_dir / "EUROPE-co2-edgarv8-agric-2012.nc"
    source.write_text("not actually netcdf", encoding="utf-8")

    catalog, added_count = build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=source_root,
        listing_path=None,
        enrich=True,
    )
    record = catalog.search()[0]

    assert added_count == 1
    assert record.user_metadata["mtime_year"] == 2026 or isinstance(record.user_metadata["mtime_year"], int)
    assert record.derived_metadata["filesystem"]["size_bytes"] == len("not actually netcdf")


def test_build_symlink_view_creates_second_catalog(tmp_path: Path) -> None:
    source_root = tmp_path / "fluxes"
    source_dir = source_root / "EUROPE" / "CO2" / "edgarv8" / "agric"
    source_dir.mkdir(parents=True)
    source = source_dir / "EUROPE-co2-edgarv8-agric-2012.nc"
    source.write_text("dummy", encoding="utf-8")
    source_catalog, _ = build_catalog(
        catalog_root=tmp_path / "source-catalog",
        source_root=source_root,
        listing_path=None,
        enrich=False,
    )

    view_catalog, added_count = build_symlink_view(
        source_catalog_root=source_catalog.root,
        view_root=tmp_path / "view",
        view_catalog_root=tmp_path / "view-catalog",
    )
    record = Catalog.open(view_catalog.root).search()[0]
    link_path = record.path()

    assert added_count == 1
    assert record.record_type == "symlink_view"
    assert record.storage_mode == "symlink"
    assert link_path is not None
    assert link_path.is_symlink()
    assert link_path.resolve() == source
