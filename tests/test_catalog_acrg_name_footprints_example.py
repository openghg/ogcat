from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ogcat import Catalog, CatalogSpec

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "catalog_acrg_name_footprints.py"
DATA_DIR = Path(__file__).resolve().parents[1] / "examples" / "data"
FOOTPRINT_LISTING = DATA_DIR / "acrg_name_footprints_recursive_ls.txt"


@pytest.fixture(scope="module")
def catalog_acrg_name_footprints() -> ModuleType:
    """Load the ACRG NAME footprint example once for this test module."""
    spec = importlib.util.spec_from_file_location("catalog_acrg_name_footprints_example", EXAMPLE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_group_footprint_collections_uses_series_not_member_files(
    catalog_acrg_name_footprints: ModuleType,
) -> None:
    """Footprint files from the same monthly series are grouped as one collection."""
    paths = catalog_acrg_name_footprints.discover_paths_from_listing(FOOTPRINT_LISTING)

    collections, skipped = catalog_acrg_name_footprints.group_footprint_collections(paths)
    central_asia = next(
        collection
        for collection in collections
        if collection.collection_pattern == "BCOB-10magl_NAME_UMG_CENTRALASIA_inert_*.nc"
    )
    metadata = central_asia.to_user_metadata()

    assert len(paths) == 10
    assert len(collections) == 3
    assert [path.name for path in skipped] == ["orphan.nc"]
    assert metadata["site"] == "BCOB"
    assert metadata["domain"] == "CENTRALASIA"
    assert metadata["years"] == [2023, 2024]
    assert metadata["month_start"] == "2023-01"
    assert metadata["month_end"] == "2024-02"
    assert metadata["member_count"] == 4


def test_build_catalog_from_vendored_footprint_listing_creates_collections(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """The vendored footprint listing validates collection catalog creation."""
    catalog, added_count, skipped = catalog_acrg_name_footprints.build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=None,
        listing_path=FOOTPRINT_LISTING,
        catalog_name="footprint-test",
        append=False,
    )
    records = catalog.search(where={"artifact_kind": "collection"})
    central_asia = catalog.get_one(
        where={
            "record_type": "footprint_collection",
            "site": "BCOB",
            "domain": "CENTRALASIA",
        }
    )
    classification = central_asia.derived_metadata["classification"]
    assert isinstance(classification, dict)

    assert added_count == 3
    assert len(records) == 3
    assert [path.name for path in skipped] == ["orphan.nc"]
    assert central_asia.locator.kind == "uri"
    assert central_asia.storage_mode == "reference"
    assert central_asia.user_metadata["member_count"] == 4
    assert classification["collection_pattern"] == "BCOB-10magl_NAME_UMG_CENTRALASIA_inert_*.nc"
    assert classification["member_format"] == "netcdf"
    assert classification["reader_hint"] == "xarray.open_mfdataset"


def test_build_catalog_from_mounted_footprint_tree_uses_path_collection_locator(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """Mounted footprint collections keep path-backed directory locators."""
    source_root = tmp_path / "fp_NAME"
    source_dir = source_root / "EASTASIA" / "BCOB-10magl" / "inert"
    source_dir.mkdir(parents=True)
    for month in ["202301", "202302"]:
        (source_dir / f"BCOB-10magl_NAME_UMG_EASTASIA_inert_{month}.nc").write_text(
            "not really netcdf",
            encoding="utf-8",
        )

    catalog, added_count, skipped = catalog_acrg_name_footprints.build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=source_root,
        listing_path=None,
        catalog_name="mounted-footprint-test",
        append=False,
    )
    record = Catalog.open(catalog.root).get_one(where={"artifact_kind": "collection"})

    assert added_count == 1
    assert skipped == []
    assert record.locator.kind == "path"
    assert record.path() == source_dir.resolve()
    assert record.user_metadata["member_count"] == 2


def test_select_monthly_footprints_opens_only_requested_series_and_year(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """A 12-month request selects 12 of 144 files from the chosen MHD series."""
    source_dir = tmp_path / "fp_NAME" / "EUROPE" / "MHD-10magl" / "co2"
    source_dir.mkdir(parents=True)
    for met_model in ("UKV", "UMG"):
        for year in range(2015, 2027):
            for month in range(1, 13):
                (source_dir / f"MHD-10magl_NAME_{met_model}_EUROPE_co2_{year}{month:02d}.nc").touch()

    catalog, added_count, skipped = catalog_acrg_name_footprints.build_catalog(
        catalog_root=tmp_path / "catalog",
        source_root=tmp_path / "fp_NAME",
        listing_path=None,
        catalog_name="mhd-test",
        append=False,
    )
    common = {"record_type": "footprint_collection", "site": "MHD", "domain": "EUROPE"}
    with pytest.raises(ValueError, match="multiple records"):
        catalog.get_one(where=common)
    record = catalog.get_one(where={**common, "met_model": "UKV"})
    selected = catalog_acrg_name_footprints.select_monthly_footprint_paths(
        catalog, record.id, start_month="2021-01", end_month="2021-12"
    )

    assert added_count == 2
    assert skipped == []
    assert len(catalog.member_paths(record.id)) == 144
    assert selected == [
        source_dir / f"MHD-10magl_NAME_UKV_EUROPE_co2_2021{month:02d}.nc" for month in range(1, 13)
    ]


def test_select_monthly_footprints_rejects_missing_duplicate_and_bad_dates(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """Selection fails before opening datasets when requested months are unclear."""
    source_dir = tmp_path / "footprints"
    source_dir.mkdir()
    (source_dir / "MHD_UKV_202301.nc").touch()
    (source_dir / "MHD_UKV_202303.nc").touch()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="months"))
    record = catalog.add_collection(source_dir, collection_pattern="*.nc")
    select = catalog_acrg_name_footprints.select_monthly_footprint_paths

    with pytest.raises(ValueError, match="Missing footprint months: 2023-02"):
        select(catalog, record.id, start_month="2023-01", end_month="2023-03")
    (source_dir / "MHD_UMG_202301.nc").touch()
    with pytest.raises(ValueError, match="Multiple footprint files found for 2023-01"):
        select(catalog, record.id, start_month="2023-01", end_month="2023-01")
    with pytest.raises(ValueError, match="YYYY-MM"):
        select(catalog, record.id, start_month="2023-1", end_month="2023-03")
    with pytest.raises(ValueError, match="start_month"):
        select(catalog, record.id, start_month="2023-03", end_month="2023-01")
    (source_dir / "MHD_UKV_202313.nc").touch()
    with pytest.raises(ValueError, match="invalid YYYYMM suffix"):
        select(catalog, record.id, start_month="2023-03", end_month="2023-03")


def test_append_refreshes_existing_mounted_series_without_duplicate(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """Rerunning a mounted import updates month summaries and keeps one record."""
    source_root = tmp_path / "fp_NAME"
    source_dir = source_root / "EASTASIA" / "BCOB-10magl" / "inert"
    source_dir.mkdir(parents=True)
    (source_dir / "BCOB-10magl_NAME_UMG_EASTASIA_inert_202301.nc").touch()
    build = catalog_acrg_name_footprints.build_catalog
    kwargs = {
        "catalog_root": tmp_path / "catalog",
        "source_root": source_root,
        "listing_path": None,
        "catalog_name": "footprints",
    }
    catalog, added, _ = build(**kwargs, append=False)
    first = catalog.get_one(where={"record_type": "footprint_collection"})
    (source_dir / "BCOB-10magl_NAME_UMG_EASTASIA_inert_202302.nc").touch()

    catalog, added_again, skipped = build(**kwargs, append=True)
    updated = catalog.get_one(where={"record_type": "footprint_collection"})

    assert added == 1
    assert added_again == 0
    assert skipped == []
    assert updated.id == first.id
    assert updated.user_metadata["member_count"] == 2
    assert updated.user_metadata["month_end"] == "2023-02"
    assert len(catalog.search(where={"record_type": "footprint_collection"})) == 1


def test_append_refreshes_listing_backed_uri_series_without_duplicate(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """An unmounted listing uses the URI root and canonical pattern as identity."""
    listing = tmp_path / "listing.txt"
    missing_root = tmp_path / "unmounted" / "EASTASIA" / "BCOB-10magl" / "inert"
    name = "BCOB-10magl_NAME_UMG_EASTASIA_inert"
    listing.write_text(f"{missing_root}:\n{name}_202301.nc\n", encoding="utf-8")
    build = catalog_acrg_name_footprints.build_catalog
    kwargs = {
        "catalog_root": tmp_path / "catalog",
        "source_root": None,
        "listing_path": listing,
        "catalog_name": "listing-footprints",
    }
    catalog, added, _ = build(**kwargs, append=False)
    first = catalog.get_one(where={"record_type": "footprint_collection"})
    assert first.locator.kind == "uri"
    listing.write_text(f"{missing_root}:\n{name}_202301.nc\n{name}_202302.nc\n", encoding="utf-8")

    catalog, added_again, _ = build(**kwargs, append=True)
    updated = catalog.get_one(where={"record_type": "footprint_collection"})

    assert added == 1
    assert added_again == 0
    assert updated.id == first.id
    assert updated.user_metadata["member_count"] == 2
    assert updated.user_metadata["month_end"] == "2023-02"
    assert updated.locator.kind == "uri"


def test_append_rejects_preexisting_ambiguous_collection_identity(
    catalog_acrg_name_footprints: ModuleType, tmp_path: Path
) -> None:
    """Append refuses to choose between duplicate active records for one series."""
    source_root = tmp_path / "fp_NAME"
    source_dir = source_root / "EASTASIA" / "BCOB-10magl" / "inert"
    source_dir.mkdir(parents=True)
    (source_dir / "BCOB-10magl_NAME_UMG_EASTASIA_inert_202301.nc").touch()
    build = catalog_acrg_name_footprints.build_catalog
    kwargs = {
        "catalog_root": tmp_path / "catalog",
        "source_root": source_root,
        "listing_path": None,
        "catalog_name": "footprints",
    }
    catalog, _, _ = build(**kwargs, append=False)
    collections, _ = catalog_acrg_name_footprints.group_footprint_collections(
        catalog_acrg_name_footprints.discover_paths_from_source_root(source_root)
    )
    catalog_acrg_name_footprints._add_footprint_collection(catalog, collections[0])

    with pytest.raises(ValueError, match="Multiple existing footprint collections"):
        build(**kwargs, append=True)
