"""Create an ogcat catalog for ACRG NAME footprint collections on Blue Pebble.

This example catalogs existing footprint directories as logical collection
artifacts. Each record represents one footprint series, with the monthly NetCDF
files recorded as collection members through a relative ``collection_pattern``.
The files stay where they already live on the shared filesystem. Nothing is
copied, moved, or opened by ogcat.

The script is aimed at the ACRG shared footprint tree on Blue Pebble, for
example:

- `/group/chem/acrg/LPDM/fp_NAME/EASTASIA/BCOB-10magl/inert/`
  `BCOB-10magl_NAME_UMG_EASTASIA_inert_202301.nc`
- `/group/chem/acrg/LPDM/fp_NAME/WESTUSA/SIO-10magl/inert/`
  `SIO-10magl_NAME_UMG_WESTUSA_inert_202401.nc`

It also supports building the catalog from a saved recursive `ls -R` listing when
the shared filesystem is not mounted locally. That is useful for development and
for documenting the structure from a machine that cannot directly read the Blue
Pebble storage.

Records created by this script include metadata for:

- `site`
- `inlet`
- `model`
- `met_model`
- `domain`
- `species`
- `years`
- `year_start`
- `year_end`
- `month_start`
- `month_end`
- `member_count`
- `collection_pattern`

The current `fp_NAME` tree implies `model=NAME`, but that field is still written
explicitly so the records remain readable and future comparisons with other LPDM
models are straightforward.

Run this example from an environment where `ogcat` is installed, for example:

- `uv sync`
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema

FOOTPRINT_COLLECTION_RECORD_TYPE = "footprint_collection"

FOOTPRINT_FILE_RE = re.compile(
    r"^(?P<site>[A-Za-z0-9]+)[-_](?P<inlet>\d{1,4}m)agl"
    r"_(?:(?P<model>NAME|FLEXPART)_)?"
    r"(?:(?P<met_model>[A-Z0-9]+)_)?"
    r"(?:(?P<domain>[A-Za-z0-9-]+)_(?P<species>[a-z0-9-]+)"
    r"|(?P<species_first>[a-z0-9-]+)_(?P<domain_last>[A-Za-z0-9-]+)"
    r"|(?P<domain_only>[A-Za-z0-9-]+))"
    r"_(?P<year>\d{4})(?P<month>\d{2})\.nc$"
)
FOOTPRINT_MEMBER_DATE_RE = re.compile(r"_(?P<year>\d{4})(?P<month>\d{2})\.nc$")


@dataclass(slots=True)
class FootprintMetadata:
    """Parsed metadata for one footprint file."""

    site: str
    inlet: str
    model: str
    met_model: str | None
    domain: str
    species: str | None
    year: int
    month: int


@dataclass(frozen=True, slots=True)
class FootprintCollectionKey:
    """Stable grouping key for one logical footprint collection."""

    collection_root: Path
    collection_pattern: str
    site: str
    inlet: str
    model: str
    met_model: str | None
    domain: str
    species: str | None


@dataclass(slots=True)
class FootprintCollection:
    """Parsed metadata for one logical footprint collection."""

    key: FootprintCollectionKey
    paths: list[Path]
    members: list[FootprintMetadata]

    def add_member(self, path: Path, metadata: FootprintMetadata) -> None:
        """Add one footprint member file to this collection."""
        self.paths.append(path)
        self.members.append(metadata)

    @property
    def collection_root(self) -> Path:
        """Return the directory that contains this collection's member files."""
        return self.key.collection_root

    @property
    def collection_pattern(self) -> str:
        """Return the member pattern relative to the collection root."""
        return self.key.collection_pattern

    def to_user_metadata(self) -> dict[str, object]:
        """Convert parsed collection metadata into catalog user metadata."""
        months = sorted({(member.year, member.month) for member in self.members})
        years = sorted({year for year, _month in months})
        start_year, start_month = months[0]
        end_year, end_month = months[-1]
        return {
            "collection_root": str(self.collection_root),
            "site": self.key.site,
            "inlet": self.key.inlet,
            "model": self.key.model,
            "met_model": self.key.met_model,
            "domain": self.key.domain,
            "species": self.key.species,
            "years": years,
            "year_start": years[0],
            "year_end": years[-1],
            "month_start": f"{start_year:04d}-{start_month:02d}",
            "month_end": f"{end_year:04d}-{end_month:02d}",
            "member_count": len(self.paths),
            "collection_pattern": self.collection_pattern,
        }


def _metadata_fields() -> list[MetadataFieldDescription]:
    """Return descriptive metadata fields for the footprint collection catalog."""
    return [
        MetadataFieldDescription(
            name="collection_root",
            description="Directory that contains this footprint collection's member files.",
            example="/group/chem/acrg/LPDM/fp_NAME/EASTASIA/BCOB-10magl/inert",
            required=True,
        ),
        MetadataFieldDescription(
            name="site",
            description="ACRG site code extracted from the footprint filename.",
            example="BCOB",
            required=True,
        ),
        MetadataFieldDescription(
            name="inlet",
            description="Inlet height extracted from the footprint filename.",
            example="10m",
            required=True,
        ),
        MetadataFieldDescription(
            name="model",
            description="LPDM model name.",
            example="NAME",
            required=True,
        ),
        MetadataFieldDescription(
            name="met_model",
            description="Meteorological driver model, when encoded in the filename.",
            example="UMG",
        ),
        MetadataFieldDescription(
            name="domain",
            description="Spatial footprint domain.",
            example="EASTASIA",
            required=True,
        ),
        MetadataFieldDescription(
            name="species",
            description="Species or footprint flavour, when encoded in the path or filename.",
            example="inert",
        ),
        MetadataFieldDescription(
            name="years",
            description="Calendar years represented by this footprint collection.",
            example=[2023, 2024],
            required=True,
        ),
        MetadataFieldDescription(
            name="year_start",
            description="First calendar year represented by this footprint collection.",
            example=2023,
            required=True,
        ),
        MetadataFieldDescription(
            name="year_end",
            description="Last calendar year represented by this footprint collection.",
            example=2024,
            required=True,
        ),
        MetadataFieldDescription(
            name="month_start",
            description="First YYYY-MM month represented by this footprint collection.",
            example="2023-01",
        ),
        MetadataFieldDescription(
            name="month_end",
            description="Last YYYY-MM month represented by this footprint collection.",
            example="2024-12",
        ),
        MetadataFieldDescription(
            name="member_count",
            description="Number of NetCDF member files represented by this collection record.",
            example=24,
            required=True,
        ),
        MetadataFieldDescription(
            name="collection_pattern",
            description="Relative pattern that identifies collection member files.",
            example="BCOB-10magl_NAME_UMG_EASTASIA_inert_*.nc",
            required=True,
        ),
    ]


def _footprint_collection_schema() -> RecordSchema:
    """Return the named schema used by ACRG footprint collection records."""
    return RecordSchema(
        description="ACRG NAME footprint series represented as one logical collection.",
        metadata_fields=_metadata_fields(),
        display_fields=[
            "id",
            "site",
            "inlet",
            "domain",
            "species",
            "year_start",
            "year_end",
            "member_count",
            "path",
        ],
    )


def parse_footprint_path(path: Path, *, default_model: str = "NAME") -> FootprintMetadata | None:
    """Parse footprint metadata from a path.

    Args:
        path: Path to a footprint NetCDF file.
        default_model: Model to use for legacy filenames that do not encode it.

    Returns:
        Parsed footprint metadata, or `None` when the file name does not match one
        of the supported footprint patterns.
    """
    match = FOOTPRINT_FILE_RE.fullmatch(path.name)
    if match is None:
        return None

    site = match.group("site").upper()
    inlet = match.group("inlet")
    year = int(match.group("year"))
    month = int(match.group("month"))
    model = (match.group("model") or default_model).upper()
    met_model = match.group("met_model")
    domain = match.group("domain") or match.group("domain_last") or match.group("domain_only")
    species = match.group("species") or match.group("species_first")

    if species is None and path.parent.name != path.parent.name.upper():
        species = path.parent.name.lower()

    if domain is None:
        return None

    return FootprintMetadata(
        site=site,
        inlet=inlet,
        model=model,
        met_model=met_model,
        domain=domain,
        species=species,
        year=year,
        month=month,
    )


def group_footprint_collections(paths: list[Path]) -> tuple[list[FootprintCollection], list[Path]]:
    """Group footprint files into logical collection records.

    Args:
        paths: Candidate NetCDF paths discovered from a mounted tree or a saved
            recursive listing.

    Returns:
        A sorted list of footprint collections and a list of paths skipped
        because their filenames did not match the supported patterns.
    """
    grouped: dict[FootprintCollectionKey, FootprintCollection] = {}
    skipped: list[Path] = []

    for path in paths:
        metadata = parse_footprint_path(path)
        if metadata is None:
            skipped.append(path)
            continue

        key = FootprintCollectionKey(
            collection_root=path.parent,
            collection_pattern=_collection_pattern_for_member(path),
            site=metadata.site,
            inlet=metadata.inlet,
            model=metadata.model,
            met_model=metadata.met_model,
            domain=metadata.domain,
            species=metadata.species,
        )
        collection = grouped.get(key)
        if collection is None:
            collection = FootprintCollection(key=key, paths=[], members=[])
            grouped[key] = collection
        collection.add_member(path, metadata)

    collections = sorted(
        grouped.values(),
        key=lambda collection: (str(collection.collection_root), collection.collection_pattern),
    )
    for collection in collections:
        collection.paths.sort()
        collection.members.sort(key=lambda member: (member.year, member.month))
    return collections, skipped


def _collection_pattern_for_member(path: Path) -> str:
    """Return the member glob for files in the same footprint series."""
    pattern, replacements = FOOTPRINT_MEMBER_DATE_RE.subn("_*.nc", path.name)
    if replacements == 1:
        return pattern
    return "*.nc"


def discover_paths_from_source_root(source_root: Path) -> list[Path]:
    """Discover footprint files by scanning the source tree."""
    return sorted(path for path in source_root.rglob("*.nc") if path.is_file())


def discover_paths_from_listing(listing_path: Path) -> list[Path]:
    """Discover footprint files from a saved recursive directory listing."""
    listing_text = _read_listing_text(listing_path)
    current_dir: Path | None = None
    paths: list[Path] = []

    for raw_line in listing_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":"):
            current_dir = Path(stripped[:-1])
            continue
        if current_dir is None:
            continue
        candidate = current_dir / stripped
        if candidate.suffix == ".nc":
            paths.append(candidate)

    return sorted(paths)


def _read_listing_text(listing_path: Path) -> str:
    """Read a listing file as text, converting RTF on macOS when possible."""
    if listing_path.suffix.lower() != ".rtf":
        return listing_path.read_text(encoding="utf-8")

    try:
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(listing_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "RTF listings require `textutil`, or convert the file to plain text first."
        ) from exc

    return result.stdout


def build_catalog(
    *,
    catalog_root: Path,
    source_root: Path | None,
    listing_path: Path | None,
    catalog_name: str,
    append: bool,
) -> tuple[Catalog, int, list[Path]]:
    """Build a catalog of footprint collections from a tree or saved listing."""
    if source_root is None and listing_path is None:
        raise ValueError("Provide at least one of --source-root or --listing.")

    catalog = _open_or_create_catalog(catalog_root, catalog_name=catalog_name, append=append)

    discovered_paths: dict[str, Path] = {}
    if source_root is not None:
        for path in discover_paths_from_source_root(source_root):
            discovered_paths[str(path)] = path
    if listing_path is not None:
        for path in discover_paths_from_listing(listing_path):
            discovered_paths[str(path)] = path

    all_paths = list(discovered_paths.values())
    collections, skipped = group_footprint_collections(all_paths)
    added_count = 0
    print(f"Discovered {len(all_paths)} candidate NetCDF files in {len(collections)} collection(s).")

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        transient=False,
    )
    with progress:
        task_id = progress.add_task("Cataloging footprint collections", total=len(collections))
        for index, collection in enumerate(collections, start=1):
            if index == 1 or index % 50 == 0 or index == len(collections):
                print(
                    f"Processing collection {index:,} of {len(collections):,}: {collection.collection_root}",
                    flush=True,
                )
            _add_footprint_collection(catalog, collection)
            added_count += 1

            progress.advance(task_id)

    return catalog, added_count, skipped


def _open_or_create_catalog(catalog_root: Path, *, catalog_name: str, append: bool) -> Catalog:
    """Open or create a footprint catalog with the named collection schema."""
    if (catalog_root / "catalog.json").exists():
        catalog = Catalog.open(catalog_root)
        if not append and catalog.describe()["record_count"] != 0:
            raise ValueError("Catalog already exists and is not empty. Use --append to add more records.")
        _ensure_footprint_collection_schema(catalog)
        return catalog

    spec = CatalogSpec(
        catalog_name=catalog_name,
        record_schemas={FOOTPRINT_COLLECTION_RECORD_TYPE: _footprint_collection_schema()},
    )
    return Catalog.create(catalog_root, spec)


def _ensure_footprint_collection_schema(catalog: Catalog) -> None:
    """Add the footprint collection schema when opening older catalogs."""
    if FOOTPRINT_COLLECTION_RECORD_TYPE not in catalog.list_record_schemas():
        catalog.add_record_schema(FOOTPRINT_COLLECTION_RECORD_TYPE, _footprint_collection_schema())


def _add_footprint_collection(catalog: Catalog, collection: FootprintCollection) -> None:
    """Record one footprint collection without requiring listing roots to exist."""
    metadata = collection.to_user_metadata()
    common_kwargs = {
        "record_type": FOOTPRINT_COLLECTION_RECORD_TYPE,
        "metadata": metadata,
        "collection_pattern": collection.collection_pattern,
        "member_format": "netcdf",
        "member_suffixes": [".nc"],
        "reader_hint": "xarray.open_mfdataset",
        "suffixes": [],
    }
    if collection.collection_root.is_dir():
        catalog.add_collection(collection.collection_root, **common_kwargs)
    else:
        catalog.add_collection(
            uri=str(collection.collection_root),
            original_path=str(collection.collection_root),
            original_filename=collection.collection_root.name,
            **common_kwargs,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalog_root",
        type=Path,
        help="Directory where the ogcat catalog should be created.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("/group/chem/acrg/LPDM/fp_NAME"),
        help="Root of the current fp_NAME footprint tree.",
    )
    parser.add_argument(
        "--listing",
        type=Path,
        help="Optional plain-text or RTF file containing `ls -R` output.",
    )
    parser.add_argument(
        "--catalog-name",
        default="acrg-name-footprints",
        help="Name written into catalog.json.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to an existing non-empty catalog instead of failing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the example script."""
    args = parse_args(argv)
    source_root = args.source_root if args.source_root and args.source_root.exists() else None

    catalog, added_count, skipped = build_catalog(
        catalog_root=args.catalog_root,
        source_root=source_root,
        listing_path=args.listing,
        catalog_name=args.catalog_name,
        append=args.append,
    )

    print(f"Catalog root: {catalog.root}")
    print(f"Added records: {added_count}")
    print(f"Total records: {catalog.describe()['record_count']}")

    if skipped:
        print(f"Skipped {len(skipped)} path(s) that did not match the expected footprint patterns.")
        for path in skipped[:10]:
            print(f"  - {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
