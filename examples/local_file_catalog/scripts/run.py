"""Local file catalog example.

Creates a small ogcat catalog, adds three generated files with metadata,
demonstrates basic search, and removes the temporary directory when done.

Run from the repository root after installing ogcat:

    python examples/local_file_catalog/scripts/run.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema


def _build_spec() -> CatalogSpec:
    """Return a catalog spec with declared metadata fields for measurements."""
    return CatalogSpec(
        catalog_name="measurements",
        default_schema=RecordSchema(
            description="Atmospheric measurement files.",
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


_SAMPLE_FILES = [
    ("mhd_ch4_2023.txt", {"site": "MHD", "species": "CH4", "year": 2023}),
    ("mhd_co2_2023.txt", {"site": "MHD", "species": "CO2", "year": 2023}),
    ("tac_ch4_2024.txt", {"site": "TAC", "species": "CH4", "year": 2024}),
]


def run(catalog_root: Path, source_dir: Path) -> None:
    """Create a catalog, add files, and print search results.

    Args:
        catalog_root: Directory where the catalog will be created.
        source_dir: Directory containing the source files to ingest.
    """
    catalog = Catalog.create(catalog_root, _build_spec())

    # Add each sample file to the catalog.
    for filename, meta in _SAMPLE_FILES:
        source = source_dir / filename
        source.write_text(f"sample content for {filename}", encoding="utf-8")
        record = catalog.add_file(source, metadata=meta)
        print(f"  added record {record.id}: site={meta['site']} species={meta['species']}")

    print()

    # Search for all CH4 records.
    ch4 = catalog.search(where={"species": "CH4"})
    print(f"CH4 records ({len(ch4)}):")
    for rec in ch4:
        print(f"  {rec.id}  site={rec.user_metadata['site']}  path={rec.path()}")

    print()

    # Search for MHD records from 2023.
    mhd_2023 = catalog.search(where={"site": "MHD", "year": 2023})
    print(f"MHD 2023 records ({len(mhd_2023)}):")
    for rec in mhd_2023:
        print(f"  {rec.id}  species={rec.user_metadata['species']}")

    print()
    info = catalog.describe()
    print(f"Total records in catalog: {info['record_count']}")


def main() -> int:
    """Entry point for the local file catalog example.

    Returns:
        Exit code (0 for success).
    """
    with tempfile.TemporaryDirectory(prefix="ogcat-local-example-") as tmp:
        tmp_path = Path(tmp)
        catalog_root = tmp_path / "catalog"
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        print(f"Catalog root: {catalog_root}")
        print()
        run(catalog_root, source_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
