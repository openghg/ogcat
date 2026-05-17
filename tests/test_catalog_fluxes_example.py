from __future__ import annotations

import importlib.util
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from ogcat import Catalog

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "catalog_fluxes.py"
DATA_DIR = Path(__file__).resolve().parents[1] / "examples" / "data"
PERSONAL_FLUXES_LISTING = DATA_DIR / "personal_fluxes_recursive_ls.txt"


@pytest.fixture
def catalog_fluxes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("catalog_fluxes_example", EXAMPLE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_discover_paths_from_listing_skips_directory_entries(
    catalog_fluxes: ModuleType, tmp_path: Path
) -> None:
    listing = tmp_path / "fluxes_ls.txt"
    listing.write_text(
        "\n".join(
            [
                "/group/chem/acrg/OC/fluxes:",
                "EDGAR",
                "EUROPE",
                "CTE-HR-ffCO2-2021_to2022.nc",
                "/group/chem/acrg/OC/fluxes/EDGAR:",
                "EDGAR_v8.0",
                "EDGAR_v6.0.tar.gz",
                "/group/chem/acrg/OC/fluxes/EDGAR/EDGAR_v8.0:",
                "WASTE",
                "/group/chem/acrg/OC/fluxes/EDGAR/EDGAR_v8.0/WASTE:",
                "v8.0_FT2022_GHG_CO2_2000_WASTE_flx.nc",
                "/group/chem/acrg/OC/fluxes/EUROPE:",
            ]
        ),
        encoding="utf-8",
    )

    paths = [source.path.as_posix() for source in catalog_fluxes.discover_paths_from_listing(listing)]

    assert paths == [
        "/group/chem/acrg/OC/fluxes/CTE-HR-ffCO2-2021_to2022.nc",
        "/group/chem/acrg/OC/fluxes/EDGAR/EDGAR_v6.0.tar.gz",
        "/group/chem/acrg/OC/fluxes/EDGAR/EDGAR_v8.0/WASTE/v8.0_FT2022_GHG_CO2_2000_WASTE_flx.nc",
    ]


def test_vendored_personal_flux_listing_is_sanitized() -> None:
    """The flux listing fixture should not preserve personal source naming."""
    text = PERSONAL_FLUXES_LISTING.read_text(encoding="utf-8")

    assert "/group/chem/acrg/ES/fluxes" not in text
    assert "eric" not in text.lower()
    assert "/group/chem/acrg/OC/fluxes" in text


def test_parse_flux_metadata_for_europe_edgar_sector(catalog_fluxes: ModuleType) -> None:
    path = Path("/group/chem/acrg/OC/fluxes/EUROPE/CO2/edgarv8/agric/EUROPE-co2-edgarv8-agric-2012.nc")

    metadata = catalog_fluxes.parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/OC/fluxes"),
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


def test_parse_flux_metadata_for_archive_and_date_range(catalog_fluxes: ModuleType) -> None:
    path = Path("/group/chem/acrg/OC/fluxes/GridFEDv2024.0/GCP-GridFEDv2024.0_2012.zip")

    metadata = catalog_fluxes.parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/OC/fluxes"),
        discovery_mode="listing",
    )

    assert metadata["top_collection"] == "GridFEDv2024.0"
    assert metadata["product"] == "GridFEDv2024.0"
    assert metadata["version"] == "v2024.0"
    assert metadata["year"] == 2012
    assert metadata["archive_format"] == "zip"
    assert metadata["file_role"] == "archive"


def test_parse_flux_metadata_for_compressed_netcdf(catalog_fluxes: ModuleType) -> None:
    path = Path("/group/chem/acrg/OC/fluxes/APO/JenaCarboscope/apo99XS_v2022_daily.nc.gz")

    metadata = catalog_fluxes.parse_flux_metadata(
        path,
        source_root=Path("/group/chem/acrg/OC/fluxes"),
        discovery_mode="listing",
    )

    assert metadata["species"] == "APO"
    assert metadata["product"] == "JenaCarboscope"
    assert metadata["archive_format"] == "gz"
    assert metadata["inner_suffix"] == ".nc"
    assert metadata["temporal_resolution"] == "daily"
    assert metadata["file_role"] == "compressed_netcdf"


def test_archive_metadata_records_zip_members(catalog_fluxes: ModuleType, tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("nested/a.nc", "a")
        zip_file.writestr("nested/b.nc", "bb")

    metadata = catalog_fluxes.archive_metadata(archive)

    assert metadata == {
        "format": "zip",
        "member_count": 2,
        "members_sample": ["nested/a.nc", "nested/b.nc"],
        "total_uncompressed_size": 3,
    }


def test_catalog_spec_tolerates_template_less_catalog_spec(
    catalog_fluxes: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TemplateLessCatalogSpec:
        def __init__(self, catalog_name: str, metadata_fields: object | None = None) -> None:
            self.catalog_name = catalog_name
            self.metadata_fields = metadata_fields

    monkeypatch.setattr(catalog_fluxes, "CatalogSpec", TemplateLessCatalogSpec)

    spec = catalog_fluxes._catalog_spec("fluxes")

    assert spec.catalog_name == "fluxes"
    assert spec.metadata_fields is not None


def test_derive_metadata_records_enrichment_errors(
    catalog_fluxes: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "bad.nc"
    source.write_bytes(b"CDF broken")

    def fail_archive_metadata(path: Path) -> None:
        raise ValueError("simulated archive failure")

    monkeypatch.setattr(catalog_fluxes, "archive_metadata", fail_archive_metadata)

    metadata = catalog_fluxes.derive_metadata(source, enrich=True)

    assert metadata["filesystem"]["size_bytes"] == len(b"CDF broken")
    assert metadata["enrichment_errors"] == {"archive": "ValueError: simulated archive failure"}


def test_build_catalog_from_listing_creates_external_reference_records(
    catalog_fluxes: ModuleType, tmp_path: Path
) -> None:
    listing = tmp_path / "fluxes_ls.txt"
    listing.write_text(
        "\n".join(
            [
                "/group/chem/acrg/OC/fluxes:",
                "EUROPE",
                "/group/chem/acrg/OC/fluxes/EUROPE:",
                "CO2",
                "/group/chem/acrg/OC/fluxes/EUROPE/CO2:",
                "edgarv8",
                "/group/chem/acrg/OC/fluxes/EUROPE/CO2/edgarv8:",
                "agric",
                "/group/chem/acrg/OC/fluxes/EUROPE/CO2/edgarv8/agric:",
                "EUROPE-co2-edgarv8-agric-2012.nc",
            ]
        ),
        encoding="utf-8",
    )

    catalog, added_count = catalog_fluxes.build_catalog(
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
    classification = records[0].derived_metadata["classification"]
    assert isinstance(classification, dict)
    assert classification["format"] == "netcdf"
    assert classification["artifact_kind"] == "file"


def test_build_catalog_from_vendored_personal_flux_listing(
    catalog_fluxes: ModuleType, tmp_path: Path
) -> None:
    """The vendored flux listing fixture validates realistic listing mode."""
    catalog, added_count = catalog_fluxes.build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=None,
        listing_path=PERSONAL_FLUXES_LISTING,
        enrich=False,
    )
    records = catalog.search()
    gridfed = catalog.get_one(where={"product": "GridFEDv2024.0", "year": 2012})
    apo = catalog.get_one(
        where={
            "species": "APO",
            "temporal_resolution": "daily",
            "file_role": "compressed_netcdf",
        }
    )

    assert added_count == 12
    assert len(records) == 12
    assert all("/OC/fluxes/" in str(record.locator.value) for record in records)
    assert gridfed.user_metadata["archive_format"] == "zip"
    assert apo.user_metadata["file_role"] == "compressed_netcdf"


def test_build_catalog_from_mounted_scan_adds_filesystem_metadata(
    catalog_fluxes: ModuleType, tmp_path: Path
) -> None:
    source_root = tmp_path / "fluxes"
    source_dir = source_root / "EUROPE" / "CO2" / "edgarv8" / "agric"
    source_dir.mkdir(parents=True)
    source = source_dir / "EUROPE-co2-edgarv8-agric-2012.nc"
    source.write_text("not actually netcdf", encoding="utf-8")

    catalog, added_count = catalog_fluxes.build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=source_root,
        listing_path=None,
        enrich=True,
    )
    record = catalog.search()[0]

    assert added_count == 1
    assert record.user_metadata["mtime_year"] == datetime.now(tz=UTC).year
    assert record.derived_metadata["filesystem"]["size_bytes"] == len("not actually netcdf")


def test_build_symlink_view_creates_second_catalog(catalog_fluxes: ModuleType, tmp_path: Path) -> None:
    _skip_if_symlinks_are_unavailable(tmp_path)
    source_root = tmp_path / "fluxes"
    source_dir = source_root / "EUROPE" / "CO2" / "edgarv8" / "agric"
    source_dir.mkdir(parents=True)
    source = source_dir / "EUROPE-co2-edgarv8-agric-2012.nc"
    source.write_text("dummy", encoding="utf-8")
    source_catalog, _ = catalog_fluxes.build_catalog(
        catalog_root=tmp_path / "source-catalog",
        source_root=source_root,
        listing_path=None,
        enrich=False,
    )

    view_catalog, added_count = catalog_fluxes.build_symlink_view(
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


def _skip_if_symlinks_are_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "symlink-source"
    target = tmp_path / "symlink-target"
    source.write_text("probe", encoding="utf-8")
    try:
        target.symlink_to(source)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable in this environment: {exc}")
    finally:
        if target.exists() or target.is_symlink():
            target.unlink()
