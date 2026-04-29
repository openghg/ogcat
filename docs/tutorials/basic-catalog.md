# Tutorial: basic catalog

This tutorial builds a small catalog with two named schemas, adds both managed
files and external references, searches across records, and updates metadata
through the repository API.

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
    metadata_fields=[
        MetadataFieldDescription(name="title", description="Human-readable title.", required=True),
        MetadataFieldDescription(name="site", description="Site code.", required=True),
        MetadataFieldDescription(name="species", description="Species code.", required=True),
        MetadataFieldDescription(name="year", description="Calendar year.", value_types=["int"]),
    ],
)

reference_schema = RecordSchema(
    description="References to external artifacts that ogcat does not copy.",
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

`add_file()` copies the source file into the catalog's managed `files/` tree.
`add_artifact()` records a locator and metadata without copying or moving data.

## Search and inspect records

```python
    ch4_records = catalog.search(where={"species": "CH4"})
    topic_matches = catalog.search(contains={"topic": "methane"}, ignore_case=True)

    print(ch4_records[0].path())
    print(topic_matches[0].locator.value)
```

Unqualified fields such as `species` and `topic` are resolved across top-level
record fields, `user_metadata`, and `derived_metadata`. Use dotted paths such as
`user_metadata.species` when you need to be explicit.

## Modify metadata with the repository

`Catalog.repository` is the low-level record store. Use it when you need to
update an existing record in place.

```python
    saved = catalog.get(measurement.id)
    assert saved is not None
    saved.user_metadata["quality_flag"] = "reviewed"
    catalog.repository.update(saved)

    reviewed = catalog.search(where={"quality_flag": "reviewed"})
    assert reviewed[0].id == measurement.id
```

Repository updates replace the stored record with the modified
`CatalogRecord`. Keep record IDs and reserved fields intact unless you are
intentionally changing them.

## CLI equivalents

```bash
uv run ogcat init /tmp/tutorial-catalog --name tutorial
uv run ogcat add ./mhd_ch4_2024.txt --catalog /tmp/tutorial-catalog --meta title="MHD methane observations" site=MHD species=CH4 year=2024
uv run ogcat search --catalog /tmp/tutorial-catalog species=CH4 --fields id,title,species,path
```
