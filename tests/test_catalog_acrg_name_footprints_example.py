from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ogcat import Catalog

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
