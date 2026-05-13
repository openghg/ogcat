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

Keys and values must be JSON-compatible. ogcat normalizes common Python values
before validation, naming, and storage:

- `pathlib.Path` values become strings.
- `datetime.date` and `datetime.datetime` values become ISO 8601 strings.
- Mapping keys become strings and mapping values are normalized recursively.
- Tuples and lists become JSON lists.
- Sets and frozensets become deterministic lists where their normalized values
  can be sorted.
- NumPy scalar-like values are converted through `.item()` when available.

Unsupported objects are rejected with a `TypeError` naming the metadata path.

`contains` filters follow the type of the stored value:

- strings use substring containment;
- lists and other stored sequences use membership matching, and a list expected
  value requires every expected item to be present;
- mappings match an expected mapping as a subset of key/value pairs;
- scalar values fall back to equality.

```python
matches = catalog.search(contains={"tags": "paris"})
all_tags = catalog.search(contains={"tags": ["paris", "europe"]})
site = catalog.search(contains={"site": {"code": "MHD"}})
```

The equivalent CLI forms are:

```bash
ogcat search --catalog ./my-catalog tags:paris
ogcat search --catalog ./my-catalog --contains tags=paris
```

When list metadata is used in a naming template, list items are joined with
hyphens before path-safe normalisation, so `["a", "b", "c"]` renders as
`a-b-c`.

## Record schemas

A *record schema* declares which metadata fields a catalog expects.  Schemas
are stored in ``catalog.json`` and are purely advisory unless you also enable
strict validation.

```python
from ogcat import CatalogSpec, RecordSchema, MetadataFieldDescription

spec = CatalogSpec(
    catalog_name="fluxes",
    default_schema=RecordSchema(
        display_fields=["id", "species", "year", "path"],
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

`display_fields` declares the compact fields to show in search result previews
and notebook dataframes:

```python
results = catalog.search(where={"species": "CO2"})
rows = results.rows()
df = results.to_dataframe(fields="default")
```

`to_dataframe()` requires pandas to be installed in the active environment.
Calling `results.to_dataframe()` with no fields still returns full record
dictionaries for compatibility.

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

This prints the schema-declared metadata fields from the catalog spec.

To discover fields actually present in stored records, use:

```bash
ogcat fields --catalog ./my-catalog --stored
ogcat fields --catalog ./my-catalog --values species
```
