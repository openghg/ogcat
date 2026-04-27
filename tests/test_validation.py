from pathlib import Path

import pytest

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogRecord,
    CatalogSpec,
    MetadataFieldDescription,
    RecordSchema,
    validate_metadata,
    validate_record,
    validate_schema,
    validate_spec,
)


def test_validate_metadata_reports_required_fields() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Short title.", required=True),
        ]
    )

    report = validate_metadata({}, schema)

    assert not report.ok
    assert report.errors[0].code == "metadata.required"
    assert report.errors[0].message == "Missing required metadata for schema default: title"


def test_validate_metadata_rejects_non_dict_metadata() -> None:
    schema = RecordSchema()

    report = validate_metadata([], schema)

    assert not report.ok
    assert report.errors[0].code == "metadata.not_object"
    with pytest.raises(TypeError, match="Metadata for schema default must be a dictionary, got list"):
        report.raise_for_errors()


def test_validate_metadata_allows_unknown_fields_by_default() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Short title."),
        ]
    )

    report = validate_metadata({"title": "Example", "extra": "allowed"}, schema, strict=True)

    assert report.ok


def test_validate_metadata_rejects_unknown_fields_when_schema_is_strict() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Short title."),
        ],
        allow_unknown_metadata=False,
    )

    report = validate_metadata({"title": "Example", "extra": "blocked"}, schema, strict=True)

    assert not report.ok
    assert report.errors[0].code == "metadata.unknown"
    assert report.errors[0].path == "metadata.extra"


def test_validate_metadata_does_not_reject_unknown_fields_without_strict_mode() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Short title."),
        ],
        allow_unknown_metadata=False,
    )

    report = validate_metadata({"title": "Example", "extra": "allowed"}, schema)

    assert report.ok


def test_validate_metadata_checks_supported_pydantic_type_labels() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Title.", value_types=["str"]),
            MetadataFieldDescription(name="year", description="Year.", value_types=["integer"]),
            MetadataFieldDescription(name="published", description="Published date.", value_types=["date"]),
            MetadataFieldDescription(name="tags", description="Search tags.", value_types=["list[str]"]),
            MetadataFieldDescription(name="attrs", description="Attributes.", value_types=["dict"]),
        ]
    )

    passing = validate_metadata(
        {
            "title": "Example",
            "year": 2024,
            "published": "2024-01-02",
            "tags": ["co2", "flux"],
            "attrs": {"source": "example"},
        },
        schema,
    )
    failing = validate_metadata(
        {
            "title": "Example",
            "year": "not-an-int",
            "published": "not-a-date",
            "tags": ["co2", 42],
            "attrs": ["not", "a", "dict"],
        },
        schema,
    )

    assert passing.ok
    assert [issue.code for issue in failing.errors] == [
        "metadata.type",
        "metadata.type",
        "metadata.type",
        "metadata.type",
    ]
    assert [issue.path for issue in failing.errors] == [
        "metadata.year",
        "metadata.published",
        "metadata.tags",
        "metadata.attrs",
    ]


def test_validate_metadata_uses_strict_type_checks_with_any_supported_type() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Title.", value_types=["str"]),
            MetadataFieldDescription(
                name="identifier",
                description="Identifier.",
                value_types=["int", "str"],
            ),
        ]
    )

    passing = validate_metadata({"title": "Example", "identifier": "abc-123"}, schema)
    failing = validate_metadata({"title": 123, "identifier": 1.25}, schema)

    assert passing.ok
    assert [issue.path for issue in failing.errors] == ["metadata.title", "metadata.identifier"]


def test_validate_schema_reports_unsupported_type_labels() -> None:
    schema = RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(name="species", description="Gas species.", value_types=["species"]),
        ]
    )

    report = validate_schema(schema, schema_name="flux")

    assert not report.ok
    assert report.errors[0].code == "schema.unsupported_type"
    assert report.errors[0].path == "record_schemas.flux.metadata_fields.0.type.0"


def test_validate_spec_reports_unsupported_type_labels() -> None:
    spec = CatalogSpec(
        catalog_name="files",
        default_schema=RecordSchema(
            metadata_fields=[
                MetadataFieldDescription(
                    name="title",
                    description="Short title.",
                    value_types=["slug"],
                )
            ]
        ),
        record_schemas={
            "flux": RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="species",
                        description="Gas species.",
                        value_types=["species"],
                    )
                ]
            )
        },
    )

    report = validate_spec(spec)

    assert not report.ok
    assert [issue.path for issue in report.errors] == [
        "default_schema.metadata_fields.0.type.0",
        "record_schemas.flux.metadata_fields.0.type.0",
    ]
    assert {issue.code for issue in report.errors} == {"schema.unsupported_type"}


def test_validate_record_uses_named_schema_when_available() -> None:
    spec = CatalogSpec(
        catalog_name="files",
        record_schemas={
            "flux": RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(name="species", description="Gas species.", required=True),
                ]
            )
        },
    )
    record = CatalogRecord(
        catalog="files",
        time_added="2026-04-27T00:00:00Z",
        record_type="flux",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        user_metadata={},
    )

    report = validate_record(record, spec=spec)

    assert not report.ok
    assert report.errors[0].message == "Missing required metadata for schema flux: species"


def test_existing_catalog_record_round_trips_through_tinydb(tmp_path: Path) -> None:
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(name="title", description="Short title.", required=True),
                ]
            ),
        ),
    )
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"title": "Example", "extra": "preserved"},
    )

    reopened = Catalog.open(tmp_path / "catalog")
    reloaded = reopened.get(record.id or "")

    assert reloaded == record
    assert reloaded is not None
    assert validate_record(reloaded, spec=reopened.spec).ok


def test_record_schema_unknown_metadata_flag_round_trips() -> None:
    schema = RecordSchema(allow_unknown_metadata=False)

    payload = schema.to_dict()
    reloaded = RecordSchema.from_dict(payload)

    assert payload["allow_unknown_metadata"] is False
    assert reloaded.allow_unknown_metadata is False
