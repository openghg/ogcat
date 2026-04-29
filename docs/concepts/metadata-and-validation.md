# Metadata and validation

## User metadata

User metadata is a flat or nested JSON-compatible dictionary attached to each
record.  Any key–value pairs are accepted by default.

```python
record = catalog.add_file(
    path,
    metadata={
        "species": "CO2",
        "year": 2024,
        "tags": ["paris", "europe"],
    },
)
```

Keys and values must be JSON-serialisable (strings, numbers, booleans, lists,
or nested dictionaries).

## Record schemas

A *record schema* declares which metadata fields a catalog expects.  Schemas
are stored in ``catalog.json`` and are purely advisory unless you also enable
strict validation.

```python
from ogcat import CatalogSpec, RecordSchema, MetadataFieldDescription

spec = CatalogSpec(
    catalog_name="fluxes",
    default_schema=RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(
                name="species",
                description="Chemical species code.",
                example="CO2",
                required=True,
            ),
            MetadataFieldDescription(
                name="year",
                description="Calendar year.",
                example=2024,
                required=True,
            ),
        ],
    ),
)
```

Named schemas let one catalog hold different record types with different
metadata expectations:

```python
spec = CatalogSpec(
    catalog_name="measurements",
    record_schemas={
        "surface": RecordSchema(
            metadata_fields=[
                MetadataFieldDescription(name="site", description="Site code.", required=True),
            ],
        ),
        "satellite": RecordSchema(
            metadata_fields=[
                MetadataFieldDescription(name="platform", description="Satellite name.", required=True),
            ],
        ),
    },
)
```

## Validation

Validation checks that required fields declared by the effective schema are
present in the record's user metadata.

```python
from ogcat import validate_metadata

report = validate_metadata(record.user_metadata, schema)
if report.issues:
    for issue in report.issues:
        print(issue.path, issue.message)
```

Validation is run automatically during ``add_file()`` and ``add_artifact()``.
Missing required fields are errors and block ingest. Use a
``before_validate_metadata`` hook to fill defaults before validation, or call
``validate_metadata()`` directly when you want to inspect a report without
writing a record.

## The ``ogcat fields`` command

```bash
ogcat fields --catalog ./my-catalog
ogcat fields --catalog ./my-catalog --record-type surface
ogcat fields --catalog ./my-catalog --json
```

This prints the declared metadata fields from the catalog spec.
