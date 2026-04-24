"""Create an ogcat catalog for ACRG NAME footprint files on Blue Pebble.

This example catalogs existing footprint files as external path-backed artifacts.
It uses `Catalog.add_artifact(...)` together with `ArtifactLocator.path(...)`, so
the files stay where they already live on the shared filesystem. Nothing is
copied or moved into the catalog.

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
- `year`
- `month`
- `start_date`

The current `fp_NAME` tree implies `model=NAME`, but that field is still written
explicitly so the records remain readable and future comparisons with other LPDM
models are straightforward.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn  # noqa: E402

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription  # noqa: E402

KNOWN_LPDM_MODELS = {"NAME", "FLEXPART"}
FOOTPRINT_FILE_RE = re.compile(
    r"^(?P<site>[A-Za-z0-9]+)[-_](?P<inlet>\d{1,4}m)agl"
    r"_(?:(?P<model>NAME|FLEXPART)_)?"
    r"(?:(?P<met_model>[A-Z0-9]+)_)?"
    r"(?:(?P<domain>[A-Za-z0-9-]+)_(?P<species>[a-z0-9-]+)"
    r"|(?P<species_first>[a-z0-9-]+)_(?P<domain_last>[A-Za-z0-9-]+)"
    r"|(?P<domain_only>[A-Za-z0-9-]+))"
    r"_(?P<year>\d{4})(?P<month>\d{2})\.nc$"
)


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

    def to_user_metadata(self) -> dict[str, object]:
        """Convert parsed metadata into catalog user metadata."""
        return {
            "site": self.site,
            "inlet": self.inlet,
            "model": self.model,
            "met_model": self.met_model,
            "domain": self.domain,
            "species": self.species,
            "year": self.year,
            "month": self.month,
            "start_date": f"{self.year:04d}-{self.month:02d}-01",
        }


def _metadata_fields() -> list[MetadataFieldDescription]:
    """Return descriptive metadata fields for the footprint catalog."""
    return [
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
            name="year",
            description="Calendar year for the monthly footprint file.",
            example=2024,
            required=True,
        ),
        MetadataFieldDescription(
            name="month",
            description="Calendar month for the monthly footprint file.",
            example=1,
            required=True,
        ),
        MetadataFieldDescription(
            name="start_date",
            description="Convenience monthly start date derived from year and month.",
            example="2024-01-01",
        ),
    ]


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
    domain = (
        match.group("domain")
        or match.group("domain_last")
        or match.group("domain_only")
    )
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
    """Build a catalog from a footprint tree or saved listing."""
    if source_root is None and listing_path is None:
        raise ValueError("Provide at least one of --source-root or --listing.")

    if (catalog_root / "catalog.json").exists():
        catalog = Catalog.open(catalog_root)
        if not append and catalog.describe()["record_count"] != 0:
            raise ValueError(
                "Catalog already exists and is not empty. Use --append to add more records."
            )
    else:
        spec = CatalogSpec(
            catalog_name=catalog_name,
            metadata_fields=_metadata_fields(),
        )
        catalog = Catalog.create(catalog_root, spec)

    discovered_paths: dict[str, Path] = {}
    if source_root is not None:
        for path in discover_paths_from_source_root(source_root):
            discovered_paths[str(path)] = path
    if listing_path is not None:
        for path in discover_paths_from_listing(listing_path):
            discovered_paths[str(path)] = path

    all_paths = list(discovered_paths.values())
    existing_record_number = _max_record_number(catalog)
    next_record_number = existing_record_number + 1
    batch_size = 250
    skipped: list[Path] = []
    added_count = 0
    print(f"Discovered {len(all_paths)} candidate NetCDF files.")

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        transient=False,
    )
    with progress:
        task_id = progress.add_task("Cataloging footprint files", total=len(all_paths))
        pending_artifacts: list[dict[str, object]] = []
        for index, path in enumerate(all_paths, start=1):
            if index == 1 or index % batch_size == 0 or index == len(all_paths):
                print(
                    f"Processing file {index:,} of {len(all_paths):,}: {path.name}",
                    flush=True,
                )
            metadata = parse_footprint_path(path)
            if metadata is None:
                skipped.append(path)
                progress.advance(task_id)
                continue

            pending_artifacts.append(
                {
                    "record_id": f"rec_{next_record_number:06d}",
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path(path),
                    "metadata": metadata.to_user_metadata(),
                    "original_filename": path.name,
                    "suffixes": path.suffixes,
                }
            )
            next_record_number += 1

            if len(pending_artifacts) >= batch_size:
                catalog.add_artifacts(pending_artifacts)
                added_count += len(pending_artifacts)
                pending_artifacts.clear()

            progress.advance(task_id)

        if pending_artifacts:
            catalog.add_artifacts(pending_artifacts)
            added_count += len(pending_artifacts)

    return catalog, added_count, skipped


def _max_record_number(catalog: Catalog) -> int:
    """Return the largest existing sequential record number in the catalog."""
    max_number = 0
    for record in catalog.repository.all():
        if not record.id.startswith("rec_"):
            continue
        try:
            max_number = max(max_number, int(record.id.split("_", 1)[1]))
        except ValueError:
            continue
    return max_number


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
