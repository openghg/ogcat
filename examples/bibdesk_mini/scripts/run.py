"""Mini BibDesk clone example.

Parses a small BibTeX fixture and stores each entry as an ogcat record with a
``paper`` record type.  Demonstrates named record schemas and ``add_artifact``
for records without a managed local file.

Run from the repository root after installing ogcat:

    uv run python examples/bibdesk_mini/scripts/run.py
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema

_BIB_DATA = Path(__file__).resolve().parents[1] / "data" / "refs.bib"

_FIELD_RE = re.compile(r"\s{2}(\w+)\s*=\s*\{(.*?)\}", re.DOTALL)
_ENTRY_RE = re.compile(r"@\w+\{([^,]+),(.*?)\n\}", re.DOTALL)


def parse_bibtex(path: Path) -> list[dict[str, str]]:
    """Parse a BibTeX file into a list of entry dictionaries.

    Each dictionary has lowercase field names as keys.  The BibTeX entry key is
    stored under ``"key"``.

    Args:
        path: Path to the ``.bib`` file.

    Returns:
        A list of dictionaries, one per BibTeX entry.
    """
    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, str]] = []
    for entry_match in _ENTRY_RE.finditer(text):
        entry: dict[str, str] = {"key": entry_match.group(1).strip()}
        for field_match in _FIELD_RE.finditer(entry_match.group(2)):
            name = field_match.group(1).lower().strip()
            value = field_match.group(2).strip()
            entry[name] = value
        entries.append(entry)
    return entries


def _build_spec() -> CatalogSpec:
    """Return a catalog spec with a ``paper`` record schema."""
    paper_schema = RecordSchema(
        description="A bibliographic reference.",
        metadata_fields=[
            MetadataFieldDescription(name="title", description="Paper title.", required=True),
            MetadataFieldDescription(name="author", description="Author list string.", required=True),
            MetadataFieldDescription(name="year", description="Publication year.", required=True),
            MetadataFieldDescription(name="journal", description="Journal name."),
            MetadataFieldDescription(name="doi", description="DOI string."),
            MetadataFieldDescription(name="tags", description="Keyword tags list."),
        ],
    )
    return CatalogSpec(
        catalog_name="papers",
        record_schemas={"paper": paper_schema},
    )


def run(catalog_root: Path, bib_path: Path) -> None:
    """Create a catalog from BibTeX entries and print search results.

    Args:
        catalog_root: Directory where the catalog will be created.
        bib_path: Path to the BibTeX fixture file.
    """
    catalog = Catalog.create(catalog_root, _build_spec())

    entries = parse_bibtex(bib_path)
    print(f"Parsed {len(entries)} BibTeX entries from {bib_path.name}")
    print()

    for entry in entries:
        tags = [t.strip() for t in entry.get("keywords", "").split(",") if t.strip()]
        locator = ArtifactLocator(kind="opaque", value=entry.get("doi", entry["key"]))
        catalog.add_artifact(
            record_type="paper",
            locator=locator,
            metadata={
                "title": entry.get("title", ""),
                "author": entry.get("author", ""),
                "year": int(entry.get("year", 0)),
                "journal": entry.get("journal", entry.get("booktitle", "")),
                "doi": entry.get("doi", ""),
                "tags": tags,
            },
        )
        print(f"  added: {entry.get('title', entry['key'])[:60]}")

    print()

    # Search by year.
    papers_2019 = catalog.search(where={"year": 2019})
    print(f"Papers from 2019 ({len(papers_2019)}):")
    for rec in papers_2019:
        print(f"  {rec.user_metadata['title'][:60]}")

    print()

    # Substring search across titles (case-insensitive).
    carbon_papers = catalog.search(contains={"title": "carbon"}, ignore_case=True)
    print(f"Papers with 'carbon' in title ({len(carbon_papers)}):")
    for rec in carbon_papers:
        print(f"  {rec.user_metadata['title'][:60]}")

    print()

    # Find papers tagged 'review'.
    reviews = catalog.search(contains={"tags": "review"})
    print(f"Papers tagged 'review' ({len(reviews)}):")
    for rec in reviews:
        print(f"  {rec.user_metadata['title'][:60]}")

    print()
    info = catalog.describe()
    print(f"Total records in catalog: {info['record_count']}")


def main() -> int:
    """Entry point for the bibdesk mini example.

    Returns:
        Exit code (0 for success).
    """
    with tempfile.TemporaryDirectory(prefix="ogcat-bibdesk-example-") as tmp:
        catalog_root = Path(tmp) / "catalog"
        print(f"Catalog root: {catalog_root}")
        print()
        run(catalog_root, _BIB_DATA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
