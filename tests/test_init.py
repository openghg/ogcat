from pathlib import Path
import json

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription


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
        metadata_fields=[
            MetadataFieldDescription(
                name="species",
                description="Gas species name used for grouping and search.",
                example="CO2",
                required=True,
            ),
            MetadataFieldDescription(
                name="product",
                description="Upstream product identifier.",
                example="CTE-HR",
            ),
        ],
    )

    Catalog.create(root, spec)
    serialized = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reloaded = CatalogSpec.read(root / "catalog.json")

    assert serialized["metadata_fields"] == [
        {
            "name": "species",
            "description": "Gas species name used for grouping and search.",
            "example": "CO2",
            "required": True,
        },
        {
            "name": "product",
            "description": "Upstream product identifier.",
            "example": "CTE-HR",
            "required": False,
        },
    ]
    assert reloaded == spec


def test_catalog_describe_and_list_metadata_fields_return_serialisable_values(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(
        catalog_name="fluxes",
        metadata_fields=[
            MetadataFieldDescription(
                name="species",
                description="Gas species name used for grouping and search.",
                example="CO2",
                required=True,
            )
        ],
    )

    catalog = Catalog.create(root, spec)
    description = catalog.describe()
    metadata_fields = catalog.list_metadata_fields()

    assert description["catalog_name"] == "fluxes"
    assert description["record_count"] == 0
    assert description["has_metadata_fields"] is True
    assert description["field_resolution_order"] == [
        "top_level",
        "user_metadata",
        "derived_metadata",
    ]
    assert metadata_fields == [
        {
            "name": "species",
            "description": "Gas species name used for grouping and search.",
            "example": "CO2",
            "required": True,
        }
    ]
