import json
from pathlib import Path

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema


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


def test_catalog_spec_preserves_legacy_top_level_schema_fields(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    root.mkdir()
    legacy_spec = {
        "catalog_name": "fluxes",
        "db_backend": "tinydb",
        "db_path": "db.json",
        "files_root": "files",
        "directory_template": "{species}/{product}",
        "filename_template": "{product}{original_suffix}",
        "metadata_fields": [
            {
                "name": "species",
                "description": "Gas species.",
                "required": True,
            }
        ],
    }
    (root / "catalog.json").write_text(json.dumps(legacy_spec), encoding="utf-8")

    spec = CatalogSpec.read(root / "catalog.json")

    assert spec.directory_template == "{species}/{product}"
    assert spec.filename_template == "{product}{original_suffix}"
    assert spec.get_schema().directory_template == "{species}/{product}"
    assert spec.get_schema().metadata_fields[0].required is True


def test_catalog_spec_round_trips_record_schemas(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(
        catalog_name="fluxes",
        default_schema=RecordSchema(
            description="Generic fallback schema for heterogeneous files.",
            directory_template="{year_added}/{original_stem}",
            filename_template="{title_slug|original_stem}{original_suffix}",
            metadata_fields=[
                MetadataFieldDescription(name="title", description="Short title."),
            ],
        ),
        record_schemas={
            "flux": RecordSchema(
                description="Schema for gridded flux datasets.",
                directory_template="{species}/{product}",
                filename_template="{product}_{species}{original_suffix}",
                metadata_fields=[
                    MetadataFieldDescription(
                        name="species",
                        description="Gas species.",
                        required=True,
                    ),
                    MetadataFieldDescription(name="product", description="Product name."),
                ],
            )
        },
    )

    Catalog.create(root, spec)
    serialized = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reloaded = CatalogSpec.read(root / "catalog.json")

    assert serialized["default_schema"]["description"] == "Generic fallback schema for heterogeneous files."
    assert serialized["record_schemas"]["flux"]["metadata_fields"][0]["required"] is True
    assert reloaded == spec


def test_catalog_spec_rejects_malformed_list_fields() -> None:
    for field_name in ["metadata_fields", "field_resolution_order"]:
        try:
            CatalogSpec.from_dict({"catalog_name": "fluxes", field_name: "not-a-list"})
        except TypeError as exc:
            assert f"{field_name} must be a list" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"Expected malformed {field_name} to be rejected")


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


def test_catalog_schema_helpers_return_serialisable_values(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            record_schemas={
                "flux": RecordSchema(
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="species",
                            description="Gas species.",
                            required=True,
                        )
                    ]
                )
            },
        ),
    )

    assert catalog.list_record_schemas() == ["flux"]
    assert catalog.describe()["record_schemas"] == ["flux"]
    assert catalog.get_schema("flux")["metadata_fields"] == [
        {
            "name": "species",
            "description": "Gas species.",
            "example": None,
            "required": True,
        }
    ]
    assert catalog.list_metadata_fields("flux")[0]["name"] == "species"
