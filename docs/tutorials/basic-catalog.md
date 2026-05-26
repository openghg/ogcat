# Tutorial: basic catalog

This tutorial builds a small catalog with two named schemas, adds both managed
files and external references, searches across records, and updates metadata
through the catalog API.

## Setup

From the repository root:

```bash
uv sync
```

## Create a catalog with multiple schemas

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema

measurement_schema = RecordSchema(
    description="Managed local measurement files.",
    display_fields=["id", "site", "species", "year", "path"],
    metadata_fields=[
        MetadataFieldDescription(name="title", description="Human-readable title.", required=True),
        MetadataFieldDescription(name="site", description="Site code.", required=True),
        MetadataFieldDescription(name="species", description="Species code.", required=True),
        MetadataFieldDescription(name="year", description="Calendar year.", value_types=["int"]),
    ],
)

reference_schema = RecordSchema(
    description="References to external artifacts that ogcat does not copy.",
    display_fields=["id", "kind", "topic", "locator.uri"],
    metadata_fields=[
        MetadataFieldDescription(name="title", description="Reference title.", required=True),
        MetadataFieldDescription(name="kind", description="Reference kind.", required=True),
        MetadataFieldDescription(name="topic", description="Searchable topic."),
    ],
)

spec = CatalogSpec(
    catalog_name="tutorial",
    record_schemas={
        "measurement": measurement_schema,
        "reference": reference_schema,
    },
)
```

## Add files and references

```python
with TemporaryDirectory(prefix="ogcat-tutorial-") as tmp:
    root = Path(tmp)
    source_dir = root / "source"
    source_dir.mkdir()

    catalog = Catalog.create(root / "catalog", spec)

    ch4_file = source_dir / "mhd_ch4_2024.txt"
    ch4_file.write_text("demo methane data", encoding="utf-8")

    measurement = catalog.add_file(
        ch4_file,
        record_type="measurement",
        metadata={
            "title": "MHD methane observations",
            "site": "MHD",
            "species": "CH4",
            "year": 2024,
        },
    )

    reference = catalog.add_artifact(
        record_type="reference",
        locator=ArtifactLocator(kind="uri", value="https://example.org/mhd-method"),
        metadata={
            "title": "MHD processing method",
            "kind": "method-note",
            "topic": "methane",
        },
        storage_mode="external",
    )
```

`add_file()` copies the source file into the catalog's managed `data/objects/` tree.
`add_artifact()` records a locator and metadata without copying or moving data.

## Search and inspect records

```python
    ch4_records = catalog.search(where={"species": "CH4"})
    topic_matches = catalog.search(contains={"topic": "methane"}, ignore_case=True)
    selected = catalog.get_one(where={"site": "MHD", "species": "CH4"})
    record_ids = ch4_records.ids
    display_rows = ch4_records.rows()

    print(ch4_records[0].path())
    print(topic_matches[0].locator.value)
    print(selected.path())
    print(record_ids)
    print(display_rows)
```

Unqualified fields such as `species` and `topic` are resolved across top-level
record fields, `user_metadata`, and `derived_metadata`. Use dotted paths such as
`user_metadata.species` when you need to be explicit.

`search()` returns a `CatalogRecordSet` by default. It behaves like a sequence
of records and also provides helpers such as `ids`, `rows()`, `preview()`, and
`select(...)`.

When a `RecordSchema` defines `display_fields`, record-set previews and
`rows()` use those compact fields for notebook-style inspection. If pandas is
available, `to_dataframe(fields="default")` uses the same fields.
`to_dataframe()` with no fields still returns full record dictionaries.

## Repair metadata through the catalog API

Use `Catalog.update_metadata()` when you need to repair or annotate user
metadata after ingest. The update is normalized, validated against the record's
schema, and written through the catalog transaction helpers.

```python
    updated_measurement = catalog.update_metadata(
        measurement.id,
        {"quality_flag": "reviewed"},
        mode="shallow_merge",
    )

    reviewed = catalog.search(where={"quality_flag": "reviewed"})
    assert reviewed[0].id == measurement.id
    assert updated_measurement.user_metadata["quality_flag"] == "reviewed"
```

Use `mode="replace"` when the new metadata should replace the whole
`user_metadata` dictionary. Use `mode="shallow_merge"` when you want a
top-level dictionary update that keeps existing top-level keys. Shallow merge
does not recursively merge nested dictionaries; nested dictionaries supplied in
the update replace existing nested dictionary values. Recursive or deep merge
support is intentionally deferred.

```python
    corrected = catalog.update_metadata(
        measurement.id,
        {
            "title": "Corrected MHD methane observations",
            "site": "MHD",
            "species": "CH4",
            "year": 2024,
            "quality_flag": "reviewed",
        },
        mode="replace",
    )
    assert corrected.user_metadata["title"] == "Corrected MHD methane observations"
```

Derived metadata can be repaired separately with
`Catalog.update_derived_metadata()`, using the same replace and shallow-merge
mode names. Prefer these public methods over mutating a `CatalogRecord` and
calling `catalog.repository.update(...)` directly.

## CLI equivalents

```bash
uv run ogcat init /tmp/tutorial-catalog --name tutorial
uv run ogcat add ./mhd_ch4_2024.txt --catalog /tmp/tutorial-catalog --meta title="MHD methane observations" site=MHD species=CH4 year=2024
uv run ogcat search --catalog /tmp/tutorial-catalog species=CH4 --fields id,title,species,path
uv run ogcat search --catalog /tmp/tutorial-catalog species=CH4 --ids
uv run ogcat fields --catalog /tmp/tutorial-catalog --stored
uv run ogcat fields --catalog /tmp/tutorial-catalog --values species
```

## Delete records and managed artifacts

Use `Catalog.delete()` when you want trash-style deletion. It marks the record
as deleted, hides it from normal search, and keeps the record, locators, and
artifact descriptors available for direct inspection or restore.

```python
    deleted = catalog.delete(measurement.id, reason="superseded by corrected data")
    assert deleted.status == "deleted"
    assert catalog.search(where={"species": "CH4"}) == []
    assert catalog.get(measurement.id) is not None
    assert catalog.search(where={"species": "CH4"}, include_deleted=True).ids == [measurement.id]

    restored = catalog.restore(measurement.id, reason="keep original for comparison")
    assert restored.status == "active"
    assert catalog.search(where={"species": "CH4"}).ids == [measurement.id]
```

Use `Catalog.purge()` only when the tombstoned record and any managed
catalog-local path-backed artifacts should be removed permanently. Purge skips
external references and user-owned paths, and it raises an error while retaining
the tombstone if managed cleanup is incomplete.

```python
    catalog.delete(measurement.id, reason="remove demo record")
    catalog.purge(measurement.id)
    assert catalog.get(measurement.id) is None
```

The CLI exposes the same lifecycle:

```bash
uv run ogcat delete <id> --catalog /tmp/tutorial-catalog --reason superseded
uv run ogcat search --catalog /tmp/tutorial-catalog --only-deleted --ids
uv run ogcat restore <id> --catalog /tmp/tutorial-catalog
uv run ogcat purge <id> --catalog /tmp/tutorial-catalog --yes
```
