# Tutorial: mini BibDesk clone

This tutorial shows how to use ogcat as a simple personal paper library.
Records are created from BibTeX entry data and PDF files are attached.

The full runnable script is at ``examples/bibdesk_mini/scripts/run.py``.
The example fixture is at ``examples/bibdesk_mini/data/refs.bib``.

## What this example demonstrates

- Using ogcat outside the atmospheric-science domain.
- Named record schemas for different record types (``paper``, ``book``).
- Storing metadata from a parsed ``.bib`` file.
- Searching by author, year, and keyword.

## Setup

```bash
pip install -e .
```

The example parses a small ``.bib`` fixture bundled in ``examples/bibdesk_mini/data/``.
No external bibliography library is required — the parser is a small
built-in helper included in the example script.

## Creating the catalog

```python
from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema

paper_schema = RecordSchema(
    description="A bibliographic reference.",
    metadata_fields=[
        MetadataFieldDescription(name="title",   description="Paper title.",       required=True),
        MetadataFieldDescription(name="author",  description="Author list string.", required=True),
        MetadataFieldDescription(name="year",    description="Publication year.",   required=True),
        MetadataFieldDescription(name="journal", description="Journal name."),
        MetadataFieldDescription(name="doi",     description="DOI string."),
        MetadataFieldDescription(name="tags",    description="Keyword tags."),
    ],
)

spec = CatalogSpec(
    catalog_name="papers",
    record_schemas={"paper": paper_schema},
)
catalog = Catalog.create("/tmp/bibdesk-demo", spec)
```

## Adding records from a BibTeX file

```python
entries = parse_bibtex(Path("refs.bib"))  # returns list of dicts

for entry in entries:
    catalog.add_artifact(
        record_type="paper",
        locator=ArtifactLocator(kind="opaque", value=entry.get("doi", entry["key"])),
        metadata={
            "title":   entry.get("title", ""),
            "author":  entry.get("author", ""),
            "year":    int(entry.get("year", 0)),
            "journal": entry.get("journal", entry.get("booktitle", "")),
            "doi":     entry.get("doi", ""),
            "tags":    entry.get("keywords", "").split(",") if entry.get("keywords") else [],
        },
    )
```

## Searching

```python
# Find all records from 2019.
papers_2019 = catalog.search(where={"year": 2019})

# Find records containing "carbon" in the title.
carbon_papers = catalog.search(contains={"title": "carbon"}, ignore_case=True)

# Find records tagged "review".
reviews = catalog.search(contains={"tags": "review"})
```

## Running the example script

```bash
python examples/bibdesk_mini/scripts/run.py
```

The script reads the bundled fixture, creates a temporary catalog, adds
records, performs a few searches, and prints a brief report.
