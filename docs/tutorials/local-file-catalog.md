# Tutorial: local file catalog

This tutorial walks through cataloging a small set of files on your local
machine: create a catalog, add files with metadata, and search the records.

The full runnable script is at ``examples/local_file_catalog/scripts/run.py``.

## Setup

Install ogcat:

```bash
pip install -e .
```

## Create a catalog

```python
from pathlib import Path
from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema

spec = CatalogSpec(
    catalog_name="measurements",
    default_schema=RecordSchema(
        metadata_fields=[
            MetadataFieldDescription(
                name="site",
                description="Measurement site code.",
                example="MHD",
                required=True,
            ),
            MetadataFieldDescription(
                name="species",
                description="Chemical species.",
                example="CH4",
                required=True,
            ),
            MetadataFieldDescription(
                name="year",
                description="Calendar year.",
                example=2024,
            ),
        ],
    ),
)

catalog = Catalog.create("/tmp/demo-catalog", spec)
```

## Add files

```python
# Assume we have a few small files to catalog.
files = [
    ("mhd_ch4_2023.nc", {"site": "MHD", "species": "CH4", "year": 2023}),
    ("mhd_co2_2023.nc", {"site": "MHD", "species": "CO2", "year": 2023}),
    ("tac_ch4_2024.nc", {"site": "TAC", "species": "CH4", "year": 2024}),
]

for filename, meta in files:
    source = Path(filename)
    record = catalog.add_file(source, metadata=meta)
    print(f"Added {record.id}: {record.user_metadata['site']} / {record.user_metadata['species']}")
```

## Search

```python
# Find all CH4 records.
ch4_records = catalog.search(where={"species": "CH4"})
for rec in ch4_records:
    print(rec.id, rec.user_metadata["site"], rec.path())

# Find records from MHD in 2023.
mhd_2023 = catalog.search(where={"site": "MHD", "year": 2023})
```

## CLI equivalent

```bash
# Initialise
ogcat init /tmp/demo-catalog --name measurements

# Add
ogcat add mhd_ch4_2023.nc --catalog /tmp/demo-catalog --meta site=MHD species=CH4 year=2023
ogcat add mhd_co2_2023.nc --catalog /tmp/demo-catalog --meta site=MHD species=CO2 year=2023

# Search
ogcat search --catalog /tmp/demo-catalog species=CH4
ogcat search --catalog /tmp/demo-catalog site=MHD year=2023 --paths
```

## Running the example script

```bash
python examples/local_file_catalog/scripts/run.py
```

The script creates a temporary catalog, adds three generated files, searches
by metadata, and prints a summary.  It cleans up after itself.
