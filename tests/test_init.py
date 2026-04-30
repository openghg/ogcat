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
        default_schema=RecordSchema(
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
            ]
        ),
    )

    Catalog.create(root, spec)
    serialized = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reloaded = CatalogSpec.read(root / "catalog.json")

    assert "metadata_fields" not in serialized
    assert "directory_template" not in serialized
    assert "filename_template" not in serialized
    assert "default_schema" not in serialized
    assert serialized["default_record_schema"] == "default"
    assert serialized["record_schemas"]["default"]["metadata_fields"] == [
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

    assert "default_schema" not in serialized
    assert serialized["default_record_schema"] == "default"
    assert (
        serialized["record_schemas"]["default"]["description"]
        == "Generic fallback schema for heterogeneous files."
    )
    assert serialized["record_schemas"]["flux"]["metadata_fields"][0]["required"] is True
    assert reloaded == spec


def test_catalog_spec_supports_custom_default_record_schema(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(
        catalog_name="fluxes",
        default_record_schema="generic",
        record_schemas={
            "generic": RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(name="title", description="Short title.", required=True),
                ],
            ),
            "flux": RecordSchema(filename_template="{species}{original_suffix}"),
        },
    )

    Catalog.create(root, spec)
    serialized = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reloaded = CatalogSpec.read(root / "catalog.json")

    assert serialized["default_record_schema"] == "generic"
    assert "default_schema" not in serialized
    assert reloaded.get_schema().required_field_names() == ["title"]
    assert reloaded.get_schema("flux").directory_template == "{year_added}/{original_stem}"


def test_metadata_field_description_serialises_type_information() -> None:
    field_description = MetadataFieldDescription(
        name="species",
        description="Gas species.",
        example="co2",
        required=True,
        value_types=["str", "species"],
    )

    payload = field_description.to_dict()
    reloaded = MetadataFieldDescription.from_dict(payload)

    assert payload["type"] == ["str", "species"]
    assert reloaded == field_description


def test_catalog_spec_rejects_malformed_list_fields() -> None:
    for field_name in ["field_resolution_order"]:
        try:
            CatalogSpec.from_dict({"catalog_name": "fluxes", field_name: "not-a-list"})
        except TypeError as exc:
            assert f"{field_name} must be a list" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"Expected malformed {field_name} to be rejected")


def test_catalog_spec_does_not_mutate_default_schema_input() -> None:
    schema = RecordSchema(metadata_fields=[MetadataFieldDescription(name="title", description="Title.")])

    spec = CatalogSpec(catalog_name="files", default_schema=schema)

    assert schema.directory_template is None
    assert schema.filename_template is None
    assert isinstance(spec.default_schema, RecordSchema)
    assert spec.default_schema.directory_template == "{year_added}/{original_stem}"
    assert spec.default_schema.filename_template == "{title_slug|original_stem}{original_suffix}"


def test_catalog_spec_normalises_constructor_record_schema_keys() -> None:
    spec = CatalogSpec(
        catalog_name="files",
        record_schemas={1: RecordSchema(metadata_fields=[])},  # type: ignore[dict-item]
    )

    assert spec.list_record_schemas() == ["1", "default"]
    assert spec.get_schema("1").directory_template == "{year_added}/{original_stem}"


def test_catalog_spec_rejects_record_schema_key_collisions_after_string_coercion() -> None:
    try:
        CatalogSpec(
            catalog_name="files",
            record_schemas={
                1: RecordSchema(description="Integer key."),  # type: ignore[dict-item]
                "1": RecordSchema(description="String key."),
            },
        )
    except ValueError as exc:
        assert "duplicate schema name after string coercion: 1" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for colliding record schema keys")


def test_catalog_spec_accepts_default_schema_dict_at_constructor_boundary() -> None:
    spec = CatalogSpec(
        catalog_name="files",
        default_schema={
            "metadata_fields": [
                {
                    "name": "title",
                    "description": "Short title.",
                    "required": True,
                }
            ]
        },  # type: ignore[arg-type]
    )

    assert isinstance(spec.default_schema, RecordSchema)
    assert spec.default_schema.directory_template == "{year_added}/{original_stem}"
    assert spec.default_schema.metadata_fields[0].name == "title"
    assert spec.default_schema.metadata_fields[0].required is True
    assert spec.record_schemas["default"] == spec.default_schema


def test_catalog_spec_rejects_invalid_default_schema_at_constructor_boundary() -> None:
    try:
        CatalogSpec(catalog_name="files", default_schema="not-a-schema")  # type: ignore[arg-type]
    except TypeError as exc:
        assert "default_schema must be a dictionary" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected TypeError for invalid default_schema")


def test_record_schema_null_description_loads_as_empty_string() -> None:
    schema = RecordSchema.from_dict({"description": None, "metadata_fields": []})

    assert schema.description == ""
    assert "description" not in schema.to_dict()


def test_catalog_spec_get_schema_raises_value_error_for_unknown_schema() -> None:
    spec = CatalogSpec(catalog_name="files")

    try:
        spec.get_schema("missing")
    except ValueError as exc:
        assert "Unknown record schema: missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for unknown record schema")


def test_catalog_describe_and_list_metadata_fields_return_serialisable_values(tmp_path: Path) -> None:
    root = tmp_path / "fluxes"
    spec = CatalogSpec(
        catalog_name="fluxes",
        default_schema=RecordSchema(
            metadata_fields=[
                MetadataFieldDescription(
                    name="species",
                    description="Gas species name used for grouping and search.",
                    example="CO2",
                    required=True,
                )
            ]
        ),
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

    assert catalog.list_record_schemas() == ["default", "flux"]
    assert catalog.describe()["has_metadata_fields"] is True
    assert catalog.describe()["record_schemas"] == ["default", "flux"]
    assert catalog.get_schema("flux")["metadata_fields"] == [
        {
            "name": "species",
            "description": "Gas species.",
            "example": None,
            "required": True,
        }
    ]
    assert catalog.list_metadata_fields("flux")[0]["name"] == "species"


def test_catalog_spec_mutation_helpers_persist_updates(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    catalog.add_record_schema(
        "paper",
        RecordSchema(
            metadata_fields=[
                MetadataFieldDescription(name="title", description="Short title.", required=True),
            ]
        ),
    )
    catalog.set_default_record_schema("paper")
    catalog.update_spec(catalog_name="library", default_operation="move")
    reopened = Catalog.open(catalog.root)

    assert reopened.spec.default_record_schema == "paper"
    assert reopened.spec.catalog_name == "library"
    assert reopened.spec.default_operation == "move"
    assert reopened.list_metadata_fields()[0]["name"] == "title"


def test_catalog_update_spec_rejects_files_root_without_migration(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    try:
        catalog.update_spec(files_root="renamed-files")
    except ValueError as exc:
        assert "Changing files_root requires a file-root migration operation." in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected files_root update to be rejected")


def test_record_schema_fallbacks_preserve_empty_templates() -> None:
    schema = RecordSchema(directory_template="", filename_template="")
    fallback = RecordSchema(directory_template="{year_added}", filename_template="{original_filename}")

    resolved = schema.with_fallbacks(fallback)

    assert resolved.directory_template == ""
    assert resolved.filename_template == ""
