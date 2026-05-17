# Artifact workflow examples

These examples show how to keep ogcat responsible for cataloging, naming,
locators, and operation rollback while domain code performs the scientific or
network-specific work.

## CAMS zip to processed boundary conditions

This workflow tracks a raw CAMS download, extracts its NetCDF members into a
managed directory artifact, and writes a processed Zarr store with provenance
linking the output back to the raw inputs.

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

In this naming convention, ``latest`` is the input version, ``co2`` is the
species, ``conc`` means concentration, and ``surface_inst`` indicates surface in
situ observations. The ``73`` in ``cams73`` is kept as part of the upstream input
version string because the workflow does not interpret it further.

### Catalog spec

The raw data schemas use readable title slugs and year/month fields when those
are present. Raw files and extracted raw collections live under ``files/raw``.
The processed boundary-condition schema uses species, domain, and boundary
condition input metadata.

```python
from pathlib import Path

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema


def required_field(name: str, description: str) -> MetadataFieldDescription:
    """Describe a required metadata field."""
    return MetadataFieldDescription(name=name, description=description, required=True)

catalog = Catalog.create(
    Path("cams-bc-catalog"),
    CatalogSpec(
        catalog_name="cams_bc",
        default_schema=RecordSchema(
            directory_template="raw/{product}/{species}",
            filename_template="{title_slug|original_stem}_{year_month_or_original_stem}{original_suffix}",
        ),
        record_schemas={
            "raw_download": RecordSchema(
                description="Raw downloaded files, including archives.",
                directory_template="raw/{product}/{species}",
                filename_template="{title_slug|original_stem}{original_suffix}",
                metadata_fields=[
                    required_field("species", "Species represented by the data."),
                    required_field("product", "Upstream product or data family."),
                    required_field("title", "Human-readable data title."),
                ],
            ),
            "raw_netcdf_collection": RecordSchema(
                description="Raw NetCDF file collections extracted from archive artifacts.",
                directory_template="raw/{product}/{species}",
                filename_template="{title_slug|original_stem}",
                metadata_fields=[
                    required_field("species", "Species represented by the data."),
                    required_field("product", "Upstream product or data family."),
                    required_field("title", "Human-readable data title."),
                ],
            ),
            "boundary_conditions": RecordSchema(
                description="Processed boundary-condition stores.",
                directory_template="boundary_conditions/{species}/{domain}",
                filename_template="{bc_input}_{species}_{domain}.zarr",
                metadata_fields=[
                    required_field("species", "Species represented by the data."),
                    required_field("domain", "Processing or model domain."),
                    required_field("bc_input", "Boundary-condition input product."),
                ],
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
        "archive_member_glob": "*.nc",
        "member_count": 36,
        "time_coverage": "2020-01/2022-12",
        "bc_input": "cams",
        "bc_input_version": "cams73_latest",
    },
)
```

### 2. Extract the NetCDF collection into managed storage

The extracted collection is a directory artifact. ``UnzipArtifactWriter`` owns
the write operation and rollback cleanup. ogcat owns the plan and record.

```python
from ogcat import UnzipArtifactWriter, path_source

collection_metadata = {
    "species": "co2",
    "product": "cams",
    "title": "CAMS CO2 concentration NetCDF collection",
    "domain": "global",
    "source_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "archive_name": downloaded_zip.name,
    "archive_member_glob": "*.nc",
    "member_count": 36,
    "time_coverage": "2020-01/2022-12",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
}

collection_plan = catalog.plan_artifact_storage(
    downloaded_zip,
    record_type="raw_netcdf_collection",
    target_kind="directory",
    write_mode="write",
    metadata=collection_metadata,
)

collection_record = catalog.add_artifact(
    record_type="raw_netcdf_collection",
    storage_plan=collection_plan,
    metadata=collection_metadata,
    source=path_source(downloaded_zip, kind="zip_file"),
    artifact_writer=UnzipArtifactWriter(),
)
```

### 3. Process the collection into a Zarr artifact

The processing writer is domain code. It opens the extracted NetCDF files with
``xarray.open_mfdataset()``, calls a project-specific ``create_cams_bc()``
function, and writes the returned dataset to a single ``.zarr`` store.

```python
import shutil
from dataclasses import dataclass
from pathlib import Path

import xarray as xr

from ogcat import ArtifactLocator, OperationContext, OperationSource, memory_source


def create_cams_bc(ds: xr.Dataset, *, species: str, domain: str) -> xr.Dataset:
    """Create boundary-condition data from CAMS concentration fields."""
    ...


@dataclass(frozen=True)
class CamsBoundaryConditionWriter:
    """Write processed CAMS boundary conditions to a planned Zarr directory."""

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        target_path = target.as_path()
        if target_path is None:
            raise ValueError("CAMS boundary-condition writer requires a path target.")
        if target_path.exists():
            raise FileExistsError(target_path)

        collection_path = Path(source.metadata["collection_path"])
        input_paths = sorted(collection_path.glob("cams73_latest_co2_conc_surface_inst_*.nc"))
        species = str(source.metadata.get("species", "co2"))
        processing_domain = str(source.metadata["processing_domain"])

        target_path.parent.mkdir(parents=True, exist_ok=True)
        context.rollback(
            lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
            description=f"remove processed Zarr store {target_path}",
        )

        with xr.open_mfdataset(input_paths) as ds:
            processed = create_cams_bc(ds, species=species, domain=processing_domain)
            processed.to_zarr(target_path, mode="w")

        context.derived_metadata.update(
            {
                "input_record_id": source.metadata["collection_record_id"],
                "raw_zip_record_id": source.metadata["raw_zip_record_id"],
                "input_file_count": len(input_paths),
                "input_files": [path.name for path in input_paths],
                "species": species,
                "bc_input": "cams",
                "bc_input_version": "cams73_latest",
                "domain": processing_domain.lower(),
                "reader_hint": "xarray.open_zarr",
            }
        )
```

Plan and write the processed artifact:

```python
collection_path = collection_record.path()
if collection_path is None:
    raise RuntimeError("Expected extracted collection to have a local path.")

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
    "provenance": {
        "raw_zip_record_id": raw_zip_record.id,
        "netcdf_collection_record_id": collection_record.id,
        "operation": "create_cams_bc",
        "opened_with": "xarray.open_mfdataset",
    },
}

processed_plan = catalog.plan_artifact_storage(
    record_type="boundary_conditions",
    target_kind="directory",
    write_mode="write",
    metadata=processed_metadata,
)

processed_record = catalog.add_artifact(
    record_type="boundary_conditions",
    storage_plan=processed_plan,
    metadata=processed_metadata,
    source=memory_source(
        None,
        kind="cams_netcdf_collection",
        metadata={
            "collection_path": str(collection_path),
            "collection_record_id": collection_record.id,
            "raw_zip_record_id": raw_zip_record.id,
            "species": "co2",
            "processing_domain": "EUROPE",
        },
    ),
    artifact_writer=CamsBoundaryConditionWriter(),
)
```

The resulting record is a generic directory artifact whose locator points to the
``.zarr`` store. The fact that it can be opened with xarray, and the provenance
needed to rebuild it, are ordinary metadata rather than special ogcat core
concepts.

## Local CAMS zip to processed Zarr with temporary extraction

The same operation can avoid a persistent extracted collection when the raw zip
already lives on a filesystem visible to the processing job. fsspec can still be
useful here: the writer can wrap the already-recorded local path as a ``file://``
URL-path, open it through ``fsspec.open_local()``, and then treat it as a zip
filesystem. The writer extracts the NetCDF members into a temporary directory
only long enough for ``xarray.open_mfdataset()`` and writes a single managed
Zarr artifact.

This pattern is useful when the intermediate NetCDF collection is an
implementation detail of the processing step rather than an artifact you want to
keep in the catalog.

The raw zip can be the same path-backed reference recorded earlier. Its metadata
does not need to choose this later processing strategy. A compact archive
summary, such as ``archive_member_glob="*.nc"`` and ``member_count=36``, is
usually enough for downstream processing decisions. A full ``ncdump -h`` for
every member would probably be better as a separate sidecar artifact than as
inline catalog metadata.

```python
zip_path = raw_zip_record.path()
if zip_path is None:
    raise RuntimeError("Expected the raw CAMS zip record to have a local path.")
```

Then use a writer that opens the zip through fsspec, extracts the NetCDF members
into a temporary directory, opens them with ``xarray.open_mfdataset()``, and
removes the temporary files when the operation finishes. The target Zarr store is
the only managed artifact created by this operation.

```python
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fsspec
import xarray as xr

from ogcat import ArtifactLocator, OperationContext, OperationSource, memory_source


@dataclass(frozen=True)
class LocalCamsZipToZarrWriter:
    """Create a Zarr store directly from a local CAMS zip path."""

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        target_path = target.as_path()
        if target_path is None:
            raise ValueError("CAMS writer requires a local Zarr target.")
        if target_path.exists():
            raise FileExistsError(target_path)

        zip_path = Path(str(source.metadata["zip_path"]))
        zip_urlpath = zip_path.as_uri()
        species = str(source.metadata.get("species", "co2"))
        processing_domain = str(source.metadata["processing_domain"])
        member_glob = str(source.metadata.get("archive_member_glob", "*.nc"))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        context.rollback(
            lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
            description=f"remove processed Zarr store {target_path}",
        )

        with tempfile.TemporaryDirectory(prefix="ogcat-cams-") as work_dir:
            work_path = Path(work_dir)
            extracted_dir = work_path / "netcdf"
            extracted_dir.mkdir()

            local_zip = fsspec.open_local(zip_urlpath, mode="rb")
            zip_fs = fsspec.filesystem("zip", fo=local_zip)
            try:
                input_paths: list[Path] = []
                for member in sorted(zip_fs.glob(member_glob)):
                    local_member = extracted_dir / Path(member).name
                    with zip_fs.open(member, "rb") as src, local_member.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    input_paths.append(local_member)

                with xr.open_mfdataset(input_paths) as ds:
                    processed = create_cams_bc(ds, species=species, domain=processing_domain)
                    processed.to_zarr(target_path, mode="w")
            finally:
                zip_fs.close()

        context.derived_metadata.update(
            {
                "raw_zip_record_id": source.metadata["raw_zip_record_id"],
                "zip_urlpath": zip_urlpath,
                "archive_member_glob": member_glob,
                "input_file_count": len(input_paths),
                "input_files": [path.name for path in input_paths],
                "temporary_extract": "NetCDF members extracted inside a TemporaryDirectory",
                "species": species,
                "bc_input": "cams",
                "bc_input_version": "cams73_latest",
                "domain": processing_domain.lower(),
                "reader_hint": "xarray.open_zarr",
            }
        )
```

Plan and run the processing step:

```python
local_processed_metadata = {
    "species": "co2",
    "domain": "europe",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
    "title": "CAMS CO2 boundary conditions for EUROPE",
    "source_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "processing_domain": "EUROPE",
    "provenance": {
        "raw_zip_record_id": raw_zip_record.id,
        "operation": "create_cams_bc",
        "opened_with": "xarray.open_mfdataset",
        "zip_access": "fsspec zip filesystem over local file URL",
    },
}

local_processed_plan = catalog.plan_artifact_storage(
    record_type="boundary_conditions",
    target_kind="directory",
    write_mode="write",
    metadata=local_processed_metadata,
)

local_processed_record = catalog.add_artifact(
    record_type="boundary_conditions",
    storage_plan=local_processed_plan,
    metadata=local_processed_metadata,
    source=memory_source(
        None,
        kind="local_cams_zip_urlpath",
        metadata={
            "zip_path": str(zip_path),
            "raw_zip_record_id": raw_zip_record.id,
            "species": "co2",
            "processing_domain": "EUROPE",
            "archive_member_glob": raw_zip_record.user_metadata.get("archive_member_glob", "*.nc"),
        },
    ),
    artifact_writer=LocalCamsZipToZarrWriter(),
)
```

This example still keeps fsspec and xarray out of ogcat core. ogcat records the
local zip path and the processed Zarr locator; the writer owns the temporary
extracted NetCDF files, processing call, and cleanup. If the zip later moves to a
remote filesystem, the same writer shape can be adapted to use fsspec URL
chaining and a temporary ``simplecache`` directory before opening the zip
filesystem.

### Variant: build the fsspec chain at processing time

The raw zip record does not need to know how it will be processed later. Keep the
raw record as a plain path or URL-path reference, then decide during the
processing operation whether to unzip permanently, read through fsspec, cache
members temporarily, or use some other domain-specific strategy.

In this variant, a hook builds the fsspec URL pattern only during the processing
operation, immediately before the custom writer runs:

```text
simplecache::zip://*.nc::file:///.../bab75005df9571750d518b0aacdedb35.zip
```

The existing ``resolve_artifact_locator`` hook is best kept for the artifact
being written, which in this operation is the output Zarr store. The input
interpretation belongs with this processing operation, so the example uses
``before_validate_metadata`` to enrich the operation source instead of rewriting
the raw data record.

The chain uses fsspec's zip filesystem and ``simplecache``. fsspec's chaining
syntax with ``::`` passes the nested path through each filesystem layer. With
``simplecache`` as the outer layer, the selected zip members can be cached as
local files in a directory chosen by the writer.

The zip record stays faithful to the source file. In this example, reuse the
``raw_zip_record`` created earlier from ``~/Downloads`` rather than creating a
second record just to support the fsspec processing path:

```python
assert raw_zip_record.locator.kind == "path"
assert raw_zip_record.user_metadata["archive_member_glob"] == "*.nc"
```

The processing operation receives the raw record's locator as source metadata. A
hook converts that locator and archive member glob to a fsspec URL path and stores it on
``context.source.metadata``. The writer then gives ``simplecache`` a temporary
directory, receives local cached member files from ``fsspec.open_local()``, opens
them with xarray, and lets the temporary directory clean up the cache when it
exits.

```python
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fsspec
import xarray as xr

from ogcat import ArtifactLocator, OperationContext, OperationSource, memory_source


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
    """Prepare a chained fsspec URL path for a CAMS processing operation."""

    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.record_type != "boundary_conditions":
            return
        if context.source.kind != "cams_zip_member_urlpath":
            return

        raw_locator = ArtifactLocator.from_dict(context.source.metadata["raw_locator"])
        member_glob = str(context.source.metadata.get("archive_member_glob", "*.nc"))
        context.source.metadata["input_urlpath"] = cams_zip_member_urlpath(
            raw_locator,
            member_glob=member_glob,
        )


@dataclass(frozen=True)
class CamsZipChainToZarrWriter:
    """Create a Zarr store from a prepared fsspec zip-member URL path."""

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        target_path = target.as_path()
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

            with xr.open_mfdataset(input_paths) as ds:
                processed = create_cams_bc(ds, species=species, domain=processing_domain)
                processed.to_zarr(target_path, mode="w")

        context.derived_metadata.update(
            {
                "raw_zip_record_id": source.metadata["raw_zip_record_id"],
                "input_urlpath": input_urlpath,
                "archive_member_glob": source.metadata.get("archive_member_glob", "*.nc"),
                "input_file_count": len(input_paths),
                "input_files": [path.name for path in input_paths],
                "temporary_cache": "fsspec simplecache inside a TemporaryDirectory",
                "species": species,
                "bc_input": "cams",
                "bc_input_version": "cams73_latest",
                "domain": processing_domain.lower(),
                "reader_hint": "xarray.open_zarr",
            }
        )
```

Then plan and write the final Zarr artifact:

```python
catalog.hook_manager.register(CamsZipMemberUrlpathHook())

zip_chain_processed_metadata = {
    "species": "co2",
    "domain": "europe",
    "bc_input": "cams",
    "bc_input_version": "cams73_latest",
    "title": "CAMS CO2 boundary conditions for EUROPE",
    "source_record_id": raw_zip_record.id,
    "source_url": ads_dataset,
    "processing_domain": "EUROPE",
    "provenance": {
        "raw_zip_record_id": raw_zip_record.id,
        "operation": "create_cams_bc",
        "opened_with": "xarray.open_mfdataset",
        "zip_access": "writer built fsspec chain from raw locator",
    },
}

zip_chain_processed_plan = catalog.plan_artifact_storage(
    record_type="boundary_conditions",
    target_kind="directory",
    write_mode="write",
    metadata=zip_chain_processed_metadata,
)

zip_chain_processed_record = catalog.add_artifact(
    record_type="boundary_conditions",
    storage_plan=zip_chain_processed_plan,
    metadata=zip_chain_processed_metadata,
    source=memory_source(
        None,
        kind="cams_zip_member_urlpath",
        metadata={
            "raw_locator": raw_zip_record.locator.to_dict(),
            "raw_zip_record_id": raw_zip_record.id,
            "species": "co2",
            "processing_domain": "EUROPE",
            "archive_member_glob": raw_zip_record.user_metadata.get("archive_member_glob", "*.nc"),
        },
    ),
    artifact_writer=CamsZipChainToZarrWriter(),
)
```

This version leaves the raw record independent of the later processing strategy.
The processing operation chooses the fsspec interpretation when the writer runs,
so future workflows can use the same raw zip record with different extraction or
transformation approaches.

## URI reference followed by a download writer

This pattern records an external URI first, then creates a managed local copy
with a custom writer. It is useful when downloads are performed by ``requests``,
``curl``, an authenticated client, or a project-specific API.

The catalog can use its default UUID primary placement so the downloaded source
filename does not need to be meaningful. The schema template remains
human-readable:

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

from ogcat import ArtifactLocator, OperationContext, OperationSource, memory_source


class RequestsDownloadWriter:
    """Download a URI to the planned local target."""

    def write(self, context: OperationContext, source: OperationSource, target: ArtifactLocator) -> None:
        target_path = target.as_path()
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
