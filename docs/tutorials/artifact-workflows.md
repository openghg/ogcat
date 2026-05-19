# Artifact workflow examples

These examples show how to keep ogcat responsible for cataloging, naming,
locators, and operation rollback while domain code performs the scientific or
network-specific work.

## CAMS zip to processed boundary conditions

This workflow tracks a raw CAMS download, extracts its NetCDF members into a
UUID-managed collection directory, records those members as one collection
artifact, and writes a processed Zarr store with provenance linking the output
back to the raw inputs.

The example uses the CAMS global inversion-optimised greenhouse gas fluxes and
concentrations dataset from the Copernicus Atmosphere Data Store:

<https://ads.atmosphere.copernicus.eu/datasets/cams-global-greenhouse-gas-inversion?tab=overview>

The ADS download page configures the request. Those options are reflected in the
downloaded file. Here the downloaded archive is:

```text
~/Downloads/bab75005df9571750d518b0aacdedb35.zip
```

Its members are monthly CO2 concentration files for 2020-2022, with names like:

```text
cams73_latest_co2_conc_surface_inst_202001.nc
cams73_latest_co2_conc_surface_inst_202002.nc
...
cams73_latest_co2_conc_surface_inst_202212.nc
```

The archive listing contains 36 monthly NetCDF files covering 2020-01 through
2022-12, with a total uncompressed size of 184,833,238,572 bytes.

In this naming convention, ``latest`` is the input version, ``co2`` is the
species, ``conc`` means concentration, and ``surface_inst`` indicates surface in
situ observations. The ``73`` in ``cams73`` is kept as part of the upstream input
version string because the workflow does not interpret it further.

### Catalog spec

The raw download is an explicit reference. The extracted NetCDF collection and
processed boundary-condition store are ogcat-owned outputs. The collection uses
UUID primary storage because the member filenames already carry the useful human
meaning; the processed Zarr store uses a human-readable schema template.

```python
from pathlib import Path

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema


species_field = MetadataFieldDescription(
    name="species",
    description="Species represented by the data.",
    required=True,
)
product_field = MetadataFieldDescription(
    name="product",
    description="Upstream product or data family.",
    required=True,
)
title_field = MetadataFieldDescription(
    name="title",
    description="Human-readable data title.",
    required=True,
)
domain_field = MetadataFieldDescription(
    name="domain",
    description="Processing or model domain.",
    required=True,
)
bc_input_field = MetadataFieldDescription(
    name="bc_input",
    description="Boundary-condition input product.",
    required=True,
)

catalog = Catalog.create(
    Path("cams-bc-catalog"),
    CatalogSpec(
        catalog_name="cams_bc",
        record_schemas={
            "raw_download": RecordSchema(
                description="Raw downloaded files, including archives.",
                metadata_fields=[species_field, product_field, title_field],
            ),
            "raw_netcdf_collection": RecordSchema(
                description="Raw NetCDF collections extracted from archive artifacts.",
                metadata_fields=[species_field, product_field, title_field],
            ),
            "boundary_conditions": RecordSchema(
                description="Processed boundary-condition stores.",
                directory_template="boundary_conditions/{species}/{domain}",
                filename_template="{bc_input}_{species}_{domain}.zarr",
                metadata_fields=[species_field, domain_field, bc_input_field],
            ),
        },
    ),
)
```

These schemas are not meant to be a complete artifact-type hierarchy. They
describe the metadata fields and naming templates that differ between raw
downloads, extracted raw collections, and processed boundary-condition outputs.
A simpler catalog could use one broad ``raw`` schema and distinguish zip files,
NetCDF files, and collections with ordinary metadata such as ``format`` or
``content_kind``.

### 1. Reference the raw zip

The first record documents the raw file in the download folder. ogcat records the
locator and metadata but does not copy or inspect the archive.

```python
from datetime import date
from pathlib import Path

CAMS_MEMBER_PATTERN = "cams73_latest_co2_conc_surface_inst_*.nc"
CAMS_EXPECTED_MEMBERS = [
    f"cams73_latest_co2_conc_surface_inst_{year}{month:02d}.nc"
    for year in range(2020, 2023)
    for month in range(1, 13)
]

downloaded_zip = Path("~/Downloads/bab75005df9571750d518b0aacdedb35.zip").expanduser()
ads_dataset = (
    "https://ads.atmosphere.copernicus.eu/datasets/"
    "cams-global-greenhouse-gas-inversion?tab=overview"
)

raw_zip_record = catalog.add_reference(
    downloaded_zip,
    record_type="raw_download",
    metadata={
        "species": "co2",
        "product": "cams",
        "title": "CAMS global inversion-optimised greenhouse gas fluxes and concentrations",
        "domain": "global",
        "source_url": ads_dataset,
        "downloaded_from": "Copernicus Atmosphere Data Store",
        "downloaded_on": date(2026, 3, 18).isoformat(),
        "comment": (
            "Raw zip downloaded from the Copernicus Atmosphere Data Store; "
            "contains global concentration fields for CO2."
        ),
        "archive_name": downloaded_zip.name,
        "archive_member_glob": CAMS_MEMBER_PATTERN,
        "member_count": len(CAMS_EXPECTED_MEMBERS),
        "time_coverage": "2020-01/2022-12",
        "uncompressed_bytes": 184_833_238_572,
        "bc_input": "cams",
        "bc_input_version": "cams73_latest",
    },
)
```

### 2. Extract the NetCDF collection into managed storage

Here the extraction is an ogcat-managed write. A writer validates the archive
members, extracts the NetCDF files into the planned UUID directory target, and
returns collection classification metadata. General ``add_artifact(...)`` writes
do not schedule template-link replicas, so the managed collection has only the
UUID primary location.

```python
import fnmatch
import shutil
import zipfile

from ogcat import (
    OperationSource,
    collection_classification_metadata,
    path_source,
    source_writer,
)


def write_cams_collection_archive(
    source: OperationSource,
    target_dir: Path,
) -> dict[str, object]:
    """Extract the expected CAMS NetCDF members into a managed directory."""
    if source.path is None:
        raise ValueError("CAMS collection writer requires a local zip source path.")

    with zipfile.ZipFile(source.path) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        for member in archive.infolist():
            member_name = Path(member.filename).name
            if member.is_dir() or not fnmatch.fnmatch(member_name, CAMS_MEMBER_PATTERN):
                continue
            if member_name in members:
                raise ValueError(f"Duplicate CAMS archive member basename: {member_name}")
            members[member_name] = member

        expected_names = sorted(CAMS_EXPECTED_MEMBERS)
        matched_names = sorted(members)
        if matched_names != expected_names:
            missing = sorted(set(expected_names) - set(matched_names))
            unexpected = sorted(set(matched_names) - set(expected_names))
            raise ValueError(f"Unexpected CAMS members: missing={missing}, unexpected={unexpected}.")

        for name in expected_names:
            member = members[name]
            destination = target_dir / name
            with archive.open(member) as source_file, destination.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)

    return {
        "extracted_file_count": len(expected_names),
        "extracted_names": expected_names,
        "classification": collection_classification_metadata(
            collection_pattern=CAMS_MEMBER_PATTERN,
            member_format="netcdf",
            member_suffixes=[".nc"],
            reader_hint="xarray.open_mfdataset",
        ),
    }

collection_metadata = {
    "species": "co2",
    "product": "cams",
    "title": "CAMS CO2 concentration NetCDF collection",
    "domain": "global",
    "source_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "archive_name": downloaded_zip.name,
    "archive_member_glob": CAMS_MEMBER_PATTERN,
    "member_count": len(CAMS_EXPECTED_MEMBERS),
    "time_coverage": "2020-01/2022-12",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
}

collection_plan = catalog.plan_artifact_storage(
    record_type="raw_netcdf_collection",
    metadata=collection_metadata,
    target_kind="directory",
    write_mode="write",
    primary_location="uuid",
)

collection_record = catalog.add_artifact(
    record_type="raw_netcdf_collection",
    storage_plan=collection_plan,
    metadata=collection_metadata,
    source=path_source(downloaded_zip, kind="zip_file"),
    artifact_writer=source_writer(
        write_cams_collection_archive,
        target_kind="directory",
        source_kind="zip_file",
    ),
)
```

The resulting record points at a managed directory under ``data/objects``. It is
still one logical dataset, not 36 separate file records. The explicit
``collection_pattern`` and ``reader_hint`` in the classification metadata
document how downstream code should read the members. This uses the lower-level
artifact writer path because managed collections are not yet a first-class
``add_collection(...)`` write target. When that API grows, archive-to-collection
workflows should be expressible without manually attaching collection
classification metadata.

### 3. Process the collection into a Zarr artifact

The processing writer is domain code. A small helper uses the collection's
reader and glob hints to open the matching NetCDF members with
``xarray.open_mfdataset()`` and pass the opened dataset as the operation-source
payload. A function writer consumes that dataset, calls a project-specific
``create_cams_bc()`` function, and writes the returned dataset to a single
``.zarr`` store.

```python
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import xarray as xr

from ogcat import CatalogRecord, OperationSource, memory_source, source_writer


def create_cams_bc(ds: xr.Dataset, *, species: str, domain: str) -> xr.Dataset:
    """Create boundary-condition data from CAMS concentration fields."""
    ...


@contextmanager
def xarray_collection_source(record: CatalogRecord, **metadata: object) -> Iterator[OperationSource]:
    """Open a collection record as an xarray-backed operation source."""
    collection_root = record.path()
    if collection_root is None:
        raise ValueError("Expected collection record to have a local path.")

    classification = record.derived_metadata.get("classification", {})
    if classification.get("reader_hint") != "xarray.open_mfdataset":
        raise ValueError("Collection is not marked for xarray.open_mfdataset.")

    collection_pattern = str(classification["collection_pattern"])
    input_paths = sorted(collection_root.glob(collection_pattern))
    expected_count = record.user_metadata.get("member_count")
    if expected_count is not None and len(input_paths) != int(expected_count):
        raise ValueError(f"Expected {expected_count} members, found {len(input_paths)}.")

    source_metadata = {
        "collection_record_id": record.id,
        "collection_pattern": collection_pattern,
        "input_file_count": len(input_paths),
        "input_paths": [str(path) for path in input_paths],
        "reader_hint": classification["reader_hint"],
    }
    source_metadata.update(metadata)

    with xr.open_mfdataset(input_paths) as ds:
        yield memory_source(
            ds,
            kind="xarray_netcdf_collection",
            metadata=source_metadata,
        )


def write_cams_boundary_conditions(source: OperationSource, target: Path) -> dict[str, object]:
    """Write processed CAMS boundary conditions from an opened xarray dataset."""
    if not isinstance(source.payload, xr.Dataset):
        raise TypeError("Expected source.payload to be an xarray Dataset.")

    species = str(source.metadata.get("species", "co2"))
    processing_domain = str(source.metadata["processing_domain"])
    processed = create_cams_bc(source.payload, species=species, domain=processing_domain)
    processed.to_zarr(target, mode="w")

    return {
        "input_record_id": source.metadata["collection_record_id"],
        "raw_zip_record_id": source.metadata["raw_zip_record_id"],
        "input_file_count": source.metadata["input_file_count"],
        "input_collection_pattern": source.metadata["collection_pattern"],
        "input_reader": source.metadata["reader_hint"],
        "species": species,
        "bc_input": "cams",
        "bc_input_version": "cams73_latest",
        "domain": processing_domain.lower(),
        "reader_hint": "xarray.open_zarr",
    }
```

Plan and write the processed artifact:

```python
processed_metadata = {
    "species": "co2",
    "domain": "europe",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
    "title": "CAMS CO2 boundary conditions for EUROPE",
    "source_record_id": collection_record.id,
    "raw_zip_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "processing_domain": "EUROPE",
    "operation": "create_cams_bc",
    "opened_with": "xarray.open_mfdataset",
}

processed_plan = catalog.plan_artifact_storage(
    record_type="boundary_conditions",
    target_kind="directory",
    write_mode="write",
    metadata=processed_metadata,
    primary_location="template",
)

with xarray_collection_source(
    collection_record,
    raw_zip_record_id=raw_zip_record.id,
    species="co2",
    processing_domain="EUROPE",
) as source:
    processed_record = catalog.add_artifact(
        record_type="boundary_conditions",
        storage_plan=processed_plan,
        metadata=processed_metadata,
        source=source,
        artifact_writer=source_writer(
            write_cams_boundary_conditions,
            target_kind="directory",
            source_kind="xarray_netcdf_collection",
        ),
    )
```

The resulting record is a generic directory artifact whose locator points to the
schema-rendered ``.zarr`` store at
``boundary_conditions/co2/europe/cams_co2_europe.zarr``. The fact that it can be
opened with xarray, and the provenance needed to rebuild it, are ordinary
metadata rather than special ogcat core concepts.

### Variant: build the fsspec input at processing time

The persistent collection is useful when the extracted NetCDF files are shared
inputs. If the extraction is only an implementation detail, run this variant
instead of the collection-backed processing step above. Keep the raw zip record
as the source of truth and build the fsspec URL path during the processing
operation. The hook below prepares a ``simplecache`` plus ``zip`` URL path only
for this operation; the writer then receives local cached member files from
fsspec and writes the same managed Zarr output.

```text
simplecache::zip://cams73_latest_co2_conc_surface_inst_*.nc::file:///.../bab75005df9571750d518b0aacdedb35.zip
```

```python
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fsspec
import xarray as xr

from ogcat import ArtifactLocator, ArtifactWriteRequest, ArtifactWriteResult, OperationContext, memory_source


def cams_zip_member_urlpath(locator: ArtifactLocator, *, member_glob: str) -> str:
    """Build a chained fsspec URL pattern for CAMS NetCDF members."""
    if locator.kind == "path":
        zip_url = Path(locator.value).as_uri()
    elif locator.kind == "urlpath":
        zip_url = locator.value
    else:
        raise ValueError(f"Cannot build a fsspec zip chain from {locator.kind!r}")
    return f"simplecache::zip://{member_glob}::{zip_url}"


@dataclass(frozen=True)
class CamsZipMemberUrlpathHook:
    """Prepare a fsspec zip-member URL path for CAMS processing."""

    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.record_type != "boundary_conditions":
            return
        if context.source.kind != "cams_zip_member_urlpath":
            return

        raw_locator = ArtifactLocator.from_dict(context.source.metadata["raw_locator"])
        member_glob = str(context.source.metadata["archive_member_glob"])
        context.source.metadata["input_urlpath"] = cams_zip_member_urlpath(
            raw_locator,
            member_glob=member_glob,
        )


@dataclass(frozen=True)
class CamsZipChainToZarrWriter:
    """Create a Zarr store from a prepared fsspec zip-member URL path."""

    def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
        context = request.context
        source = request.source
        target_path = request.locator.as_path()
        if target_path is None:
            raise ValueError("CAMS writer requires a local Zarr target.")
        if target_path.exists():
            raise FileExistsError(target_path)

        input_urlpath = str(source.metadata["input_urlpath"])
        species = str(source.metadata.get("species", "co2"))
        processing_domain = str(source.metadata["processing_domain"])

        target_path.parent.mkdir(parents=True, exist_ok=True)
        context.rollback(
            lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
            description=f"remove processed Zarr store {target_path}",
        )

        with tempfile.TemporaryDirectory(prefix="ogcat-cams-cache-") as cache_dir:
            local_members = fsspec.open_local(
                input_urlpath,
                mode="rb",
                simplecache={"cache_storage": cache_dir},
            )
            input_paths = [Path(path) for path in local_members]
            expected_count = int(source.metadata["member_count"])
            if len(input_paths) != expected_count:
                raise ValueError(f"Expected {expected_count} members, found {len(input_paths)}.")

            with xr.open_mfdataset(input_paths) as ds:
                processed = create_cams_bc(ds, species=species, domain=processing_domain)
                processed.to_zarr(target_path, mode="w")

        context.derived_metadata.update(
            {
                "raw_zip_record_id": source.metadata["raw_zip_record_id"],
                "input_urlpath": input_urlpath,
                "archive_member_glob": source.metadata["archive_member_glob"],
                "input_file_count": len(input_paths),
                "temporary_cache": "fsspec simplecache inside a TemporaryDirectory",
                "species": species,
                "bc_input": "cams",
                "bc_input_version": "cams73_latest",
                "domain": processing_domain.lower(),
                "reader_hint": "xarray.open_zarr",
            }
        )
        return ArtifactWriteResult.for_data_artifact(
            relationship={
                "kind": "processed_from",
                "source_record_id": source.metadata["raw_zip_record_id"],
            }
        )
```

Then plan the same kind of boundary-condition artifact, but pass the raw zip
record's locator and archive hints as operation-source metadata:

```python
catalog.hook_manager.register(CamsZipMemberUrlpathHook())

fsspec_processed_metadata = {
    "species": "co2",
    "domain": "europe",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
    "title": "CAMS CO2 boundary conditions for EUROPE",
    "source_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "processing_domain": "EUROPE",
    "operation": "create_cams_bc",
    "opened_with": "xarray.open_mfdataset",
    "zip_access": "fsspec simplecache over zip filesystem",
}

fsspec_processed_plan = catalog.plan_artifact_storage(
    record_type="boundary_conditions",
    target_kind="directory",
    write_mode="write",
    metadata=fsspec_processed_metadata,
    primary_location="template",
)

fsspec_processed_record = catalog.add_artifact(
    record_type="boundary_conditions",
    storage_plan=fsspec_processed_plan,
    metadata=fsspec_processed_metadata,
    source=memory_source(
        None,
        kind="cams_zip_member_urlpath",
        metadata={
            "raw_locator": raw_zip_record.locator.to_dict(),
            "raw_zip_record_id": raw_zip_record.id,
            "species": "co2",
            "processing_domain": "EUROPE",
            "archive_member_glob": raw_zip_record.user_metadata["archive_member_glob"],
            "member_count": raw_zip_record.user_metadata["member_count"],
        },
    ),
    artifact_writer=CamsZipChainToZarrWriter(),
)
```

This variant demonstrates fsspec without making the raw zip record depend on a
specific processing strategy. The tradeoff is that the NetCDF member collection
is not independently searchable in the catalog.

## URI reference followed by a download writer

This pattern records an external URI first, then creates a managed local copy
with a custom writer. It is useful when downloads are performed by ``requests``,
``curl``, an authenticated client, or a project-specific API.

The catalog can plan a template-primary target so the downloaded source
filename does not need to be meaningful, while the managed output still lands
at a human-readable path:

```python
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, CatalogSpec, RecordSchema

catalog = Catalog.create(
    Path("download-catalog"),
    CatalogSpec(
        catalog_name="downloads",
        default_schema=RecordSchema(
            directory_template="downloads/{year_added}",
            filename_template="{title_slug|original_stem}{original_suffix}",
        ),
    ),
)
```

Record the external reference:

```python
from datetime import date

source_record = catalog.add_artifact(
    record_type="download_reference",
    locator=ArtifactLocator(kind="uri", value="https://example.org/data/example.nc"),
    storage_mode="external",
    metadata={
        "species": "co2",
        "product": "example",
        "title": "Example downloadable CO2 data",
        "download_page": "https://example.org/data",
        "selected_options": {"format": "netcdf", "variable": "co2"},
        "reference_recorded_on": date.today().isoformat(),
    },
)
```

Write a managed copy with a small requests-based writer:

```python
import requests

from ogcat import ArtifactLocator, ArtifactWriteRequest, ArtifactWriteResult, memory_source


class RequestsDownloadWriter:
    """Download a URI to the planned local target."""

    def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
        context = request.context
        source = request.source
        target_path = request.locator.as_path()
        if target_path is None:
            raise ValueError("Download writer requires a local path target.")
        if target_path.exists():
            raise FileExistsError(target_path)

        url = str(source.metadata["url"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        context.rollback(lambda path=target_path: path.unlink(missing_ok=True), description="remove download")

        with requests.get(url, timeout=60, stream=True) as response:
            response.raise_for_status()
            with target_path.open("wb") as target_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target_file.write(chunk)

        context.derived_metadata.update(
            {
                "source_record_id": source.metadata["source_record_id"],
                "downloaded_from": url,
                "downloaded_on": source.metadata["downloaded_on"],
                "byte_count": target_path.stat().st_size,
            }
        )
        return ArtifactWriteResult.for_data_artifact(
            relationship={"kind": "downloaded_from", "url": url},
            diagnostics={"byte_count": target_path.stat().st_size},
        )


download_metadata = {
    "species": "co2",
    "product": "example",
    "title": "Example downloaded CO2 data",
    "source_record_id": source_record.id,
}

download_plan = catalog.plan_artifact_storage(
    Path("example.nc"),
    write_mode="write",
    metadata=download_metadata,
    primary_location="template",
)

download_record = catalog.add_artifact(
    record_type="downloaded_file",
    storage_plan=download_plan,
    metadata=download_metadata,
    source=memory_source(
        None,
        kind="download_uri",
        metadata={
            "url": source_record.locator.value,
            "source_record_id": source_record.id,
            "downloaded_on": date.today().isoformat(),
        },
    ),
    artifact_writer=RequestsDownloadWriter(),
)
```

This keeps the distinction clear: the URI record says where the data came from,
and the managed file record says what was downloaded, where it was stored, and
which source record it came from.
