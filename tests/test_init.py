from pathlib import Path

from ogcat import Catalog, CatalogSpec


def test_create_and_open_catalog(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(catalog_name="fluxes")

    created = Catalog.create(root, spec)
    reopened = Catalog.open(root)

    assert created.root == reopened.root
    assert (root / "catalog.json").exists()
    assert (root / "db.json").exists()
    assert (root / "files").exists()
    assert reopened.spec.catalog_name == "fluxes"


def test_catalog_spec_round_trips_via_catalog_json(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(
        catalog_name="fluxes",
        default_operation="move",
        field_resolution_order=["user_metadata", "top_level", "derived_metadata"],
    )

    Catalog.create(root, spec)
    reloaded = CatalogSpec.read(root / "catalog.json")

    assert reloaded == spec
