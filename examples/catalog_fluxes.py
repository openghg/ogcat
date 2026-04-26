"""Catalog ACRG flux data as external path-backed artifacts.

This example is intentionally a stress-test script rather than a new ogcat core
feature. It can build a catalog from a mounted flux tree or from a saved
recursive ``ls -R`` listing, and it can optionally build a symlinked organised
view of the same source paths.

The mounted scan mode can enrich records with filesystem metadata, netCDF
summaries, and archive listings. Listing mode can only infer metadata from paths
and filenames, because plain ``ls -R`` output does not include file size,
ownership, or timestamps.
"""

from __future__ import annotations

import argparse
import grp
import gzip
import os
import pwd
import re
import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription
from ogcat.extractors import extract_derived_metadata
from ogcat.models import JsonValue, MetadataDict
from ogcat.naming import render_storage_location

DEFAULT_SOURCE_ROOT = Path("/group/chem/acrg/ES/fluxes")
DEFAULT_CATALOG_NAME = "acrg-fluxes"
DEFAULT_VIEW_CATALOG_NAME = "acrg-fluxes-symlink-view"
DEFAULT_DIRECTORY_TEMPLATE = (
    "{mtime_year|year}/{top_collection|unknown}/{species|unknown}/{product|original_stem}"
)
DEFAULT_FILENAME_TEMPLATE = "{original_filename}"
ARCHIVE_SAMPLE_LIMIT = 25
BATCH_SIZE = 250

SPECIES_ALIASES = {
    "14CO2": "14CO2",
    "APO": "APO",
    "CH4": "CH4",
    "CO2": "CO2",
    "CO2BIO": "CO2bio",
    "D14CO2": "D14CO2",
    "N2O": "N2O",
    "O2": "O2",
}
TEMPORAL_TOKENS = {
    "3hour": "3hourly",
    "3hourly": "3hourly",
    "annual": "yearly",
    "day": "daily",
    "daily": "daily",
    "hourly": "hourly",
    "hrly": "hourly",
    "mnthly": "monthly",
    "monthly": "monthly",
    "year": "yearly",
    "yearly": "yearly",
}
VARIABLE_TOKENS = {
    "bio",
    "bio_exchange_prior",
    "cement",
    "co2flux_ocean",
    "fire",
    "fossil",
    "ff_exchange_prior",
    "gee",
    "gpp",
    "nbp",
    "nee",
    "npp",
    "ocean",
    "reco",
    "resp",
    "rg",
    "rh",
    "rm",
    "total",
}
SCRIPT_SUFFIXES = {".sh"}
TABLE_SUFFIXES = {".csv", ".xlsx"}
TEXT_SUFFIXES = {".txt", ".md", ".rst"}
NETCDF_SUFFIXES = {".nc", ".nc4", ".cdf"}


@dataclass(slots=True)
class SourcePath:
    path: Path
    discovery_mode: str


def metadata_fields() -> list[MetadataFieldDescription]:
    """Return descriptive metadata fields for the flux catalog."""
    return [
        MetadataFieldDescription(name="source_root", description="Root of the scanned flux tree."),
        MetadataFieldDescription(name="relative_source_path", description="Path relative to the flux root."),
        MetadataFieldDescription(name="top_collection", description="First directory below the flux root."),
        MetadataFieldDescription(name="domain", description="Spatial domain inferred from path or filename."),
        MetadataFieldDescription(name="species", description="Gas species or atmospheric quantity."),
        MetadataFieldDescription(name="product", description="Product, model, or inventory family."),
        MetadataFieldDescription(name="version", description="Version token inferred from path or filename."),
        MetadataFieldDescription(name="sector", description="Emissions sector or sub-product grouping."),
        MetadataFieldDescription(name="variable", description="Flux variable or component."),
        MetadataFieldDescription(
            name="temporal_resolution",
            description="Temporal cadence inferred from names.",
        ),
        MetadataFieldDescription(name="year", description="Single data year, when one is identifiable."),
        MetadataFieldDescription(name="month", description="Single data month, when one is identifiable."),
        MetadataFieldDescription(name="start_year", description="Start year for a range."),
        MetadataFieldDescription(name="end_year", description="End year for a range."),
        MetadataFieldDescription(
            name="variant",
            description="Variant token such as climat, flat, or uncertainty.",
        ),
        MetadataFieldDescription(
            name="file_role",
            description="Broad artifact role such as netcdf or archive.",
        ),
        MetadataFieldDescription(name="archive_format", description="Archive or compression format."),
        MetadataFieldDescription(name="discovery_mode", description="listing or mounted_scan."),
    ]


def discover_paths_from_listing(listing_path: Path) -> list[SourcePath]:
    """Discover file-like paths from recursive ``ls -R`` output."""
    text = listing_path.read_text(encoding="utf-8")
    current_dir: Path | None = None
    directory_headers: set[Path] = set()
    candidates: list[Path] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.endswith(":"):
            current_dir = Path(stripped[:-1])
            directory_headers.add(current_dir)
            continue
        if current_dir is not None:
            candidates.append(current_dir / stripped)

    paths = [path for path in candidates if path not in directory_headers]
    return [SourcePath(path=path, discovery_mode="listing") for path in sorted(set(paths))]


def discover_paths_from_source_root(source_root: Path) -> list[SourcePath]:
    """Discover file paths by scanning a mounted source tree."""
    return [
        SourcePath(path=path, discovery_mode="mounted_scan")
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    ]


def parse_flux_metadata(path: Path, *, source_root: Path, discovery_mode: str) -> MetadataDict:
    """Infer useful flux metadata from a source path."""
    relative = _relative_flux_path(path, source_root=source_root)
    parts = list(relative.parts)
    stem_tokens = _name_tokens(path.stem)
    all_tokens = [_normalise_token(part) for part in [*parts, *stem_tokens]]
    metadata: MetadataDict = {
        "source_root": str(source_root),
        "relative_source_path": relative.as_posix(),
        "discovery_mode": discovery_mode,
        "file_role": _file_role(path),
    }

    if parts:
        metadata["top_collection"] = parts[0]

    species = _infer_species(parts, stem_tokens)
    if species is not None:
        metadata["species"] = species

    domain = _infer_domain(parts, stem_tokens)
    if domain is not None:
        metadata["domain"] = domain

    product = _infer_product(parts, species=species)
    if product is not None:
        metadata["product"] = product

    version = _infer_version([*parts, path.name])
    if version is not None:
        metadata["version"] = version

    sector = _infer_sector(parts, product=product, species=species)
    if sector is not None:
        metadata["sector"] = sector

    variable = _infer_variable(all_tokens)
    if variable is not None:
        metadata["variable"] = variable

    temporal_resolution = _infer_temporal_resolution(all_tokens)
    if temporal_resolution is not None:
        metadata["temporal_resolution"] = temporal_resolution

    metadata.update(_infer_dates(path.name))

    archive_format = _archive_format(path)
    if archive_format is not None:
        metadata["archive_format"] = archive_format
    if path.name.lower().endswith(".nc.gz"):
        metadata["inner_suffix"] = ".nc"

    variant = _infer_variant(all_tokens)
    if variant is not None:
        metadata["variant"] = variant

    mtime_year = _mtime_year(path)
    if mtime_year is not None:
        metadata["mtime_year"] = mtime_year
    elif "year" in metadata:
        metadata["mtime_year"] = metadata["year"]
    elif "start_year" in metadata:
        metadata["mtime_year"] = metadata["start_year"]

    return metadata


def build_catalog(
    *,
    catalog_root: Path,
    source_root: Path | None,
    listing_path: Path | None,
    catalog_name: str = DEFAULT_CATALOG_NAME,
    append: bool = False,
    enrich: bool = True,
) -> tuple[Catalog, int]:
    """Build an in-place external-reference catalog."""
    sources = _discover_sources(source_root=source_root, listing_path=listing_path)
    resolved_source_root = _resolve_source_root(source_root=source_root, sources=sources)
    catalog = _open_or_create_catalog(catalog_root, catalog_name=catalog_name, append=append)

    added_count = 0
    pending: list[dict[str, object]] = []
    progress = _progress()
    with progress:
        task_id = progress.add_task("Cataloging flux files", total=len(sources))
        for source in sources:
            metadata = parse_flux_metadata(
                source.path,
                source_root=resolved_source_root,
                discovery_mode=source.discovery_mode,
            )
            derived_metadata = derive_metadata(source.path, enrich=enrich)
            pending.append(
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path(source.path),
                    "metadata": metadata,
                    "storage_mode": "external",
                    "original_path": source.path,
                    "original_filename": source.path.name,
                    "suffixes": source.path.suffixes,
                    "derived_metadata": derived_metadata,
                }
            )
            if len(pending) >= BATCH_SIZE:
                catalog.add_artifacts(pending)
                added_count += len(pending)
                pending.clear()
            progress.advance(task_id)

        if pending:
            catalog.add_artifacts(pending)
            added_count += len(pending)

    return catalog, added_count


def build_symlink_view(
    *,
    source_catalog_root: Path,
    view_root: Path,
    view_catalog_root: Path,
    catalog_name: str = DEFAULT_VIEW_CATALOG_NAME,
    append: bool = False,
    dry_run: bool = False,
) -> tuple[Catalog, int]:
    """Build a symlinked organised view and catalog it as a second catalog."""
    source_catalog = Catalog.open(source_catalog_root)
    view_catalog = _open_or_create_catalog(view_catalog_root, catalog_name=catalog_name, append=append)
    records = source_catalog.search()
    added_count = 0
    pending: list[dict[str, object]] = []
    progress = _progress()
    with progress:
        task_id = progress.add_task("Creating symlink view", total=len(records))
        for record in records:
            source_path = record.path()
            if source_path is None:
                progress.advance(task_id)
                continue
            target, rel_path, resolved_filename = render_storage_location(
                files_root=view_root,
                directory_template=DEFAULT_DIRECTORY_TEMPLATE,
                filename_template=DEFAULT_FILENAME_TEMPLATE,
                context=_view_context(record.user_metadata, source_path),
            )
            link_status = _ensure_symlink(source_path, target, dry_run=dry_run)
            pending.append(
                {
                    "record_type": "symlink_view",
                    "locator": ArtifactLocator.path(target, relative_path=rel_path),
                    "metadata": dict(record.user_metadata),
                    "storage_mode": "symlink",
                    "original_path": source_path,
                    "original_filename": source_path.name,
                    "suffixes": source_path.suffixes,
                    "derived_metadata": {
                        "symlink": {
                            "target": str(source_path),
                            "link_status": link_status,
                            "dry_run": dry_run,
                        }
                    },
                    "naming_metadata": {
                        "directory_template": DEFAULT_DIRECTORY_TEMPLATE,
                        "filename_template": DEFAULT_FILENAME_TEMPLATE,
                        "resolved_filename": resolved_filename,
                    },
                }
            )
            if len(pending) >= BATCH_SIZE:
                view_catalog.add_artifacts(pending)
                added_count += len(pending)
                pending.clear()
            progress.advance(task_id)

        if pending:
            view_catalog.add_artifacts(pending)
            added_count += len(pending)

    return view_catalog, added_count


def derive_metadata(path: Path, *, enrich: bool) -> MetadataDict:
    """Return best-effort derived metadata for mounted files."""
    if not enrich or not path.exists():
        return {}

    derived: MetadataDict = {"filesystem": filesystem_metadata(path)}
    if _is_netcdf(path):
        extracted = extract_derived_metadata(path)
        derived.update(extracted)

    archive = archive_metadata(path)
    if archive is not None:
        derived["archive"] = archive

    return derived


def filesystem_metadata(path: Path) -> MetadataDict:
    """Collect generic filesystem metadata."""
    stat_result = path.stat()
    return {
        "size_bytes": stat_result.st_size,
        "mtime": _utc_from_timestamp(stat_result.st_mtime),
        "ctime": _utc_from_timestamp(stat_result.st_ctime),
        "owner": _owner_name(stat_result.st_uid),
        "group": _group_name(stat_result.st_gid),
        "uid": stat_result.st_uid,
        "gid": stat_result.st_gid,
        "mode": oct(stat_result.st_mode),
        "inode": stat_result.st_ino,
    }


def archive_metadata(path: Path, *, sample_limit: int = ARCHIVE_SAMPLE_LIMIT) -> MetadataDict | None:
    """Collect a small archive listing summary without extracting data."""
    archive_format = _archive_format(path)
    if archive_format is None:
        return None

    try:
        if archive_format == "zip":
            return _zip_metadata(path, sample_limit=sample_limit)
        if archive_format in {"tar", "tar.gz", "tgz"}:
            return _tar_metadata(path, archive_format=archive_format, sample_limit=sample_limit)
        if archive_format == "gz":
            return _gzip_metadata(path)
    except Exception as exc:
        return {"format": archive_format, "error": f"{type(exc).__name__}: {exc}"}
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    catalog_parser = subparsers.add_parser("catalog", help="Catalog flux files in place.")
    catalog_parser.add_argument("catalog_root", type=Path)
    catalog_parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    catalog_parser.add_argument("--listing", type=Path)
    catalog_parser.add_argument("--catalog-name", default=DEFAULT_CATALOG_NAME)
    catalog_parser.add_argument("--append", action="store_true")
    catalog_parser.add_argument("--no-enrich", action="store_true")

    view_parser = subparsers.add_parser("symlink-view", help="Create an organised symlink view.")
    view_parser.add_argument("source_catalog_root", type=Path)
    view_parser.add_argument("view_root", type=Path)
    view_parser.add_argument("view_catalog_root", type=Path)
    view_parser.add_argument("--catalog-name", default=DEFAULT_VIEW_CATALOG_NAME)
    view_parser.add_argument("--append", action="store_true")
    view_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("choose a command: catalog or symlink-view")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the example script."""
    args = parse_args(argv)
    if args.command == "catalog":
        source_root = args.source_root if args.source_root and args.source_root.exists() else None
        catalog, added_count = build_catalog(
            catalog_root=args.catalog_root,
            source_root=source_root,
            listing_path=args.listing,
            catalog_name=args.catalog_name,
            append=args.append,
            enrich=not args.no_enrich,
        )
        print(f"Catalog root: {catalog.root}")
        print(f"Added records: {added_count}")
        print(f"Total records: {catalog.describe()['record_count']}")
        return 0

    catalog, added_count = build_symlink_view(
        source_catalog_root=args.source_catalog_root,
        view_root=args.view_root,
        view_catalog_root=args.view_catalog_root,
        catalog_name=args.catalog_name,
        append=args.append,
        dry_run=args.dry_run,
    )
    print(f"View catalog root: {catalog.root}")
    print(f"Added records: {added_count}")
    print(f"Total records: {catalog.describe()['record_count']}")
    return 0


def _discover_sources(*, source_root: Path | None, listing_path: Path | None) -> list[SourcePath]:
    if source_root is None and listing_path is None:
        raise ValueError("Provide --source-root for a mounted scan or --listing for listing mode.")

    discovered: dict[str, SourcePath] = {}
    if source_root is not None:
        for source in discover_paths_from_source_root(source_root):
            discovered[str(source.path)] = source
    if listing_path is not None:
        for source in discover_paths_from_listing(listing_path):
            discovered[str(source.path)] = source
    return list(discovered.values())


def _resolve_source_root(*, source_root: Path | None, sources: list[SourcePath]) -> Path:
    if source_root is not None:
        return source_root
    for source in sources:
        root = _root_through_fluxes(source.path)
        if root is not None:
            return root
    return DEFAULT_SOURCE_ROOT


def _open_or_create_catalog(catalog_root: Path, *, catalog_name: str, append: bool) -> Catalog:
    if (catalog_root / "catalog.json").exists():
        catalog = Catalog.open(catalog_root)
        if not append and catalog.describe()["record_count"] != 0:
            raise ValueError("Catalog already exists and is not empty. Use --append to add records.")
        return catalog
    spec = CatalogSpec(
        catalog_name=catalog_name,
        directory_template=DEFAULT_DIRECTORY_TEMPLATE,
        filename_template=DEFAULT_FILENAME_TEMPLATE,
        metadata_fields=metadata_fields(),
    )
    return Catalog.create(catalog_root, spec)


def _relative_flux_path(path: Path, *, source_root: Path) -> Path:
    try:
        return path.relative_to(source_root)
    except ValueError:
        root = _root_through_fluxes(path)
        if root is not None:
            try:
                return path.relative_to(root)
            except ValueError:
                pass
    return Path(path.name)


def _root_through_fluxes(path: Path) -> Path | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part == "fluxes":
            return Path(*parts[: index + 1])
    return None


def _file_role(path: Path) -> str:
    archive_format = _archive_format(path)
    if archive_format is not None:
        return "archive" if not path.name.lower().endswith(".nc.gz") else "compressed_netcdf"
    suffix = path.suffix.lower()
    if suffix in NETCDF_SUFFIXES:
        return "netcdf"
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in SCRIPT_SUFFIXES:
        return "script"
    return "other"


def _archive_format(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return "tar.gz"
    if name.endswith(".tgz"):
        return "tgz"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".gz"):
        return "gz"
    return None


def _is_netcdf(path: Path) -> bool:
    return path.suffix.lower() in NETCDF_SUFFIXES


def _infer_species(parts: list[str], tokens: list[str]) -> str | None:
    for value in [*parts, *tokens]:
        key = _normalise_token(value).upper()
        if key in SPECIES_ALIASES:
            return SPECIES_ALIASES[key]
    for token in tokens:
        upper = _normalise_token(token).upper()
        for alias, species in SPECIES_ALIASES.items():
            if alias in upper:
                return species
    return None


def _infer_domain(parts: list[str], tokens: list[str]) -> str | None:
    for value in [*parts, *tokens]:
        token = _normalise_token(value)
        if token == "EUROPE":
            return "EUROPE"
        if token in {"GLOBAL", "GLB"}:
            return "GLOBAL"
        if token == "UK":
            return "UK"
    return None


def _infer_product(parts: list[str], *, species: str | None) -> str | None:
    if not parts:
        return None
    if parts[0] == "EUROPE" and len(parts) >= 3 and species is not None:
        return parts[2]
    if parts[0] == "UKGHG" and len(parts) >= 2:
        return "UKGHG"
    if parts[0] == "EDGAR" and len(parts) >= 2:
        return parts[1]
    if len(parts) == 1 or "." in parts[-1] and len(parts) == 2:
        return parts[0]
    if len(parts) >= 2 and _normalise_token(parts[1]).upper() not in SPECIES_ALIASES:
        return parts[1]
    return parts[0]


def _infer_version(values: Iterable[str]) -> str | None:
    for value in values:
        match = re.search(r"(?i)(?:^|[_-])((?:v|V)\d+(?:\.\d+)*(?:[A-Za-z0-9]*)?)", value)
        if match is not None:
            return match.group(1)
        edgar = re.search(r"(?i)EDGAR[_-]?(v\d+(?:\.\d+)*)", value)
        if edgar is not None:
            return edgar.group(1)
        embedded = re.search(r"(?i)(v\d+(?:\.\d+)+)", value)
        if embedded is not None:
            return embedded.group(1)
    return None


def _infer_sector(parts: list[str], *, product: str | None, species: str | None) -> str | None:
    if len(parts) < 2:
        return None
    parent = parts[-2] if "." in parts[-1] else parts[-1]
    if parent in {product, species, "fluxes", "EUROPE"}:
        return None
    if _normalise_token(parent).upper() in SPECIES_ALIASES:
        return None
    return parent


def _infer_variable(tokens: Iterable[str]) -> str | None:
    for token in tokens:
        normalised = _normalise_token(token).lower()
        if normalised in VARIABLE_TOKENS:
            return normalised
    return None


def _infer_temporal_resolution(tokens: Iterable[str]) -> str | None:
    for token in tokens:
        normalised = _normalise_token(token).lower()
        if normalised in TEMPORAL_TOKENS:
            return TEMPORAL_TOKENS[normalised]
    return None


def _infer_variant(tokens: Iterable[str]) -> str | None:
    for token in tokens:
        normalised = _normalise_token(token).lower()
        if normalised in {
            "alphaff",
            "climat",
            "countryflat",
            "flat",
            "flatprior",
            "gridded",
            "uncert",
            "uncertainty",
        }:
            return normalised
    return None


def _infer_dates(name: str) -> MetadataDict:
    metadata: MetadataDict = {}
    range_match = re.search(r"(?<![A-Za-z0-9.])(\d{4})(?:\d{2})?[_-](\d{4})(?:\d{2})?(?!\d)", name)
    if range_match is not None:
        metadata["start_year"] = int(range_match.group(1))
        metadata["end_year"] = int(range_match.group(2))
        return metadata

    yyyymm_match = re.search(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(?!\d)", name)
    if yyyymm_match is not None:
        metadata["year"] = int(yyyymm_match.group(1))
        metadata["month"] = int(yyyymm_match.group(2))
        return metadata

    years = [
        int(token)
        for token in re.split(r"[^A-Za-z0-9]+", name)
        if re.fullmatch(r"(?:19|20)\d{2}", token)
    ]
    if len(years) == 1:
        metadata["year"] = years[0]
    elif len(years) > 1:
        metadata["year"] = years[-1]
    return metadata


def _mtime_year(path: Path) -> int | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).year


def _name_tokens(name: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9]+", name) if token]


def _normalise_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", value).upper()


def _utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _zip_metadata(path: Path, *, sample_limit: int) -> MetadataDict:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        return {
            "format": "zip",
            "member_count": len(infos),
            "members_sample": [info.filename for info in infos[:sample_limit]],
            "total_uncompressed_size": sum(info.file_size for info in infos),
        }


def _tar_metadata(path: Path, *, archive_format: str, sample_limit: int) -> MetadataDict:
    mode = "r:gz" if archive_format in {"tar.gz", "tgz"} else "r:"
    with tarfile.open(path, mode) as archive:
        members = archive.getmembers()
        return {
            "format": archive_format,
            "member_count": len(members),
            "members_sample": [member.name for member in members[:sample_limit]],
            "total_uncompressed_size": sum(member.size for member in members if member.isfile()),
        }


def _gzip_metadata(path: Path) -> MetadataDict:
    with gzip.open(path, "rb") as stream:
        stream.peek(1)
    return {
        "format": "gz",
        "member_count": 1,
        "members_sample": [path.name[:-3] if path.name.lower().endswith(".gz") else path.stem],
    }


def _view_context(metadata: Mapping[str, JsonValue], source_path: Path) -> dict[str, object]:
    context: dict[str, object] = dict(metadata)
    context["original_filename"] = source_path.name
    context["original_stem"] = source_path.stem
    context["original_suffix"] = "".join(source_path.suffixes[-1:])
    date_added = datetime.now(tz=UTC).date().isoformat()
    context["date_added"] = date_added
    context["year_added"] = date_added[:4]
    return context


def _ensure_symlink(source: Path, target: Path, *, dry_run: bool) -> str:
    if dry_run:
        return "planned"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        existing = os.readlink(target)
        existing_target = (target.parent / existing).resolve(strict=False)
        if Path(existing) == source or existing_target == source.resolve(strict=False):
            return "reused"
        target = _unique_symlink_path(target)
    elif target.exists():
        target = _unique_symlink_path(target)

    try:
        relative_source = os.path.relpath(source, start=target.parent)
    except ValueError:
        relative_source = str(source)
    target.symlink_to(relative_source)
    return "created"


def _unique_symlink_path(path: Path) -> Path:
    stem = path.stem
    suffix = "".join(path.suffixes)
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        counter += 1


def _progress() -> Progress:
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        transient=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
