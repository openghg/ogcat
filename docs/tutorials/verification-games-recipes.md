# Tutorial: verification-games workflow recipes

These recipes condense common ogcat patterns from the verification-games
notebooks into small copy/paste examples. They use temporary directories and
tiny synthetic local data, so they do not depend on the verification-games
repositories or shared ACRG data paths.

The examples are written as notebook-style cells that can be run in order. The
xarray cells are optional; they require xarray and a local NetCDF or Zarr
backend in the active environment.

## Create and reopen a local catalog

Start by creating a tiny catalog with named schemas for raw fluxes, derived
fluxes, and model output. The schema fields are intentionally broad: they make
common search and display workflows easy without forcing every project to use
the same metadata vocabulary.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogSpec,
    MetadataFieldDescription,
    RecordSchema,
)
from ogcat.example_data import (
    EXAMPLE_FLUX_FILENAME,
    example_flux_cdl,
    example_flux_collection_path,
    write_example_flux_netcdf_or_placeholder,
)
from ogcat.writers import memory_source, source_writer


def metadata_field(name: str, description: str, *, required: bool = False) -> MetadataFieldDescription:
    """Return a concise metadata field description."""
    return MetadataFieldDescription(name=name, description=description, required=required)


common_fields = [
    metadata_field("title", "Short human-readable description."),
    metadata_field("product", "Product, model, or inventory family.", required=True),
    metadata_field("species", "Species or atmospheric quantity."),
    metadata_field("domain", "Spatial domain."),
    metadata_field("year", "Single year or compact year range."),
    metadata_field("keywords", "Search keywords."),
    metadata_field("provenance", "raw or derived.", required=True),
    metadata_field("format", "Normalised format such as netcdf, zarr, or text.", required=True),
    metadata_field("processing_stage", "Workflow stage that produced this artifact."),
]

spec = CatalogSpec(
    catalog_name="verification-games-demo",
    files_root="data",
    record_schemas={
        "raw_flux": RecordSchema(
            description="Raw or externally supplied flux data.",
            display_fields=["id", "product", "species", "year", "path"],
            directory_template="flux/raw",
            filename_template="{title_slug|original_stem}{original_suffix}",
            metadata_fields=common_fields,
        ),
        "derived_flux": RecordSchema(
            description="Processed flux data and intermediate products.",
            display_fields=["id", "product", "processing_stage", "format", "path"],
            directory_template="flux/derived",
            filename_template="{title_slug|original_stem}{original_suffix}",
            metadata_fields=[
                *common_fields,
                metadata_field("inputs", "Input record ids or external input labels."),
                metadata_field("modifications", "Short description of processing changes."),
            ],
        ),
        "verification_games_obs": RecordSchema(
            description="Synthetic observation or forward-model output.",
            display_fields=["id", "species", "domain", "processing_stage", "path"],
            directory_template="verification_games_obs",
            filename_template="{title_slug|original_stem}{original_suffix}",
            metadata_fields=[
                *common_fields,
                metadata_field("inputs", "Input record ids or external input labels."),
                metadata_field("modifications", "Short description of processing changes."),
                metadata_field("site", "Observation or footprint site code."),
            ],
        ),
    },
)

work = TemporaryDirectory(prefix="ogcat-verification-games-")
root = Path(work.name)
external_dir = root / "external"
scratch_dir = root / "scratch"
external_dir.mkdir()
scratch_dir.mkdir()

catalog = Catalog.create(root / "catalog", spec)
catalog = Catalog.open(root / "catalog")
print(catalog.describe()["record_schemas"])
```

The helper module used below provides a generic HPC-style path and a small
CDL-style description based on the kinds of `ncdump -h` and `xr.Dataset`
printouts used in the notebooks:

```python
print(example_flux_collection_path())
print(example_flux_cdl())
```

## Choose the right add method

| Method | Use it when | Storage effect |
| --- | --- | --- |
| `add_reference(...)` | The artifact already exists and should stay where it is. | Records a local path, URI, or URL path; does not copy, move, or write data. |
| `add_file(...)` | ogcat should manage a local copy or move a finished file into catalog storage. | Copies or moves one local file into the catalog object storage root and, by default, creates a readable template symlink under `files_root`. |
| `add_artifact(...)` with a `StoragePlan` and writer | Domain code should create the output while ogcat handles planning, record creation, and rollback hooks. | The writer materialises the planned file or directory, then ogcat records it. |

For the detailed storage model, see
[Locators and storage](../concepts/locators-and-storage.md).

## Add an existing path as a reference

Use `add_reference(...)` for mounted shared files, generated outputs that have
already been moved into place, or remote identifiers that domain code will open
later. The file below is deliberately tiny; in a real workflow it might be a
large GridFED zip, NetCDF file, or Zarr directory.

```python
raw_path = write_example_flux_netcdf_or_placeholder(external_dir / EXAMPLE_FLUX_FILENAME)

raw_record = catalog.add_reference(
    raw_path,
    record_type="raw_flux",
    metadata={
        "title": "GridFED TOTAL 2021 raw file",
        "product": "GridFED",
        "species": ["co2", "o2"],
        "domain": "global",
        "year": 2021,
        "keywords": ["fossil", "ff"],
        "provenance": "raw",
        "format": "netcdf",
        "processing_stage": "downloaded",
    },
)

print(raw_record.id, raw_record.locator.value)
```

URI references use the same method when ogcat should not interpret or inspect
the target:

```python
catalog.add_reference(
    uri="https://example.org/data/gridfed-total-2020.nc",
    record_type="raw_flux",
    metadata={
        "title": "Remote GridFED TOTAL 2020 raw file",
        "product": "GridFED",
        "species": ["co2", "o2"],
        "domain": "global",
        "year": 2020,
        "keywords": ["fossil", "ff", "remote"],
        "provenance": "raw",
        "format": "netcdf",
        "processing_stage": "remote_catalogue_entry",
    },
)
```

## Add a managed copy

Use `add_file(...)` when the catalog should own the stored file. The default
operation is a copy; pass `operation="move"` when the scratch file should be
removed after a successful catalog add.

```python
note_path = scratch_dir / "mhd_co2_screening.txt"
note_path.write_text("small screening output\n", encoding="utf-8")

managed_record = catalog.add_file(
    note_path,
    record_type="verification_games_obs",
    metadata={
        "title": "MHD CO2 screening output",
        "product": "screening",
        "species": "co2",
        "domain": "EUROPE",
        "year": 2021,
        "keywords": ["screening", "copy"],
        "provenance": "derived",
        "format": "text",
        "processing_stage": "screened_site_output",
        "inputs": [raw_record.id],
        "site": "MHD",
    },
)

print(managed_record.path())
```

## Search, get one record, and open with xarray

The notebooks often search with enough metadata to identify exactly one input,
then open the record's locator with xarray.

```python
selected = catalog.get_one(
    where={
        "product": "GridFED",
        "year": 2021,
        "provenance": "raw",
        "format": "netcdf",
    },
    contains={"keywords": "fossil"},
)
print(selected.id, selected.locator.value)
```

If xarray and a NetCDF writer backend are installed, this cell turns the fake
path into a tiny real NetCDF and opens it through the selected record. If those
optional pieces are missing, the helper writes a readable `.nc` placeholder and
this cell reports that xarray cannot open it.

```python
try:
    import xarray as xr
except ImportError:
    xr = None

if xr is not None:
    try:
        write_example_flux_netcdf_or_placeholder(raw_path)
        with xr.open_dataset(selected.locator.value) as ds:
            print(dict(ds.sizes))
    except Exception as exc:
        print(f"xarray NetCDF example skipped: {exc}")
```

For a Zarr artifact, use the same record lookup and the stored
`reader_hint`:

```python
zarr_path = scratch_dir / "staged_flux.zarr"
zarr_path.mkdir(exist_ok=True)

zarr_record = catalog.add_reference(
    zarr_path,
    record_type="derived_flux",
    metadata={
        "title": "Staged flux Zarr",
        "product": "PARIS_CTE-HR_filled_fluxes_staged",
        "species": ["co2", "o2"],
        "domain": "EUROPE",
        "year": "202012-202112",
        "keywords": ["flux_stage", "forward_model_input"],
        "provenance": "derived",
        "format": "zarr",
        "processing_stage": "forward_model_input",
        "inputs": [raw_record.id],
        "modifications": "Stacked source fluxes and filled missing values before forward modelling.",
    },
    derived_metadata={
        "reader_hint": "xarray.open_zarr",
        "source_record_ids": [raw_record.id],
        "source_paths": [str(raw_record.locator.value)],
        "modifications": "Stacked source fluxes and filled missing values before forward modelling.",
    },
    naming_metadata={"target_kind": "directory"},
)

if zarr_record.derived_metadata.get("reader_hint") == "xarray.open_zarr":
    print(f"Open with xr.open_zarr({zarr_record.locator.value!r})")
```

## Search and display fields

`search(...)` returns a `CatalogRecordSet` by default. Use `rows(...)` for
JSON-friendly notebook output, `display_rows(...)` for terminal-style strings,
or `to_dataframe(...)` when pandas is installed.

```python
derived = catalog.search(
    where={"provenance": "derived"},
    contains={"keywords": "forward_model_input"},
    as_record_set=True,
)

fields = ["id", "product", "processing_stage", "format", "path"]
print(derived.rows(fields))
print(derived.display_rows(fields))

try:
    df = derived.to_dataframe(fields)
    print(df)
except ImportError:
    print("Install pandas to use CatalogRecordSet.to_dataframe().")
```

For record-set details, see
[Search and record sets](../api/search.rst).

## Store generated artifacts after computation

A common notebook pattern is to do heavy work outside ogcat, then register the
small set of final outputs serially.

### Register an already-written artifact

When the computation has already written and validated the output path, record
it with `add_reference(...)` and store provenance in metadata.

```python
staged_output = scratch_dir / "already_written_stage.zarr"
staged_output.mkdir(exist_ok=True)

registered_stage = catalog.add_reference(
    staged_output,
    record_type="derived_flux",
    metadata={
        "title": "Already-written staged flux store",
        "product": "PARIS_CTE-HR_filled_fluxes_staged",
        "species": ["co2", "o2"],
        "domain": "EUROPE",
        "year": "202012-202112",
        "keywords": ["flux_stage", "filled_nan_zero"],
        "provenance": "derived",
        "format": "zarr",
        "processing_stage": "forward_model_input",
        "inputs": [raw_record.id],
        "modifications": "Stacked source fluxes into a forward-model input store.",
    },
    derived_metadata={
        "reader_hint": "xarray.open_zarr",
        "source_record_ids": [raw_record.id],
        "source_paths": [str(raw_record.locator.value)],
        "sizes": {"time": 1, "source": 1},
        "chunks": {"time": 1, "source": 1},
        "modifications": "Stacked source fluxes into a forward-model input store.",
    },
    naming_metadata={"target_kind": "directory"},
)
```

### Move a finished file into managed storage

When the output is a single finished file in scratch space, use
`add_file(..., operation="move")`.

```python
finished_path = scratch_dir / "mhd_pollution_event.txt"
finished_path.write_text("finished fake model output\n", encoding="utf-8")

moved_output = catalog.add_file(
    finished_path,
    record_type="verification_games_obs",
    operation="move",
    metadata={
        "title": "MHD pollution event intermediate",
        "product": "modelled_pollution_event",
        "species": "co2_o2",
        "domain": "EUROPE",
        "year": "202101",
        "keywords": ["pollution_events", "intermediate"],
        "provenance": "derived",
        "format": "text",
        "processing_stage": "raw_forward_model_output",
        "inputs": [registered_stage.id],
        "modifications": "Computed footprint dot flux for one site-month before baseline correction.",
        "site": "MHD",
    },
)
print(finished_path.exists(), moved_output.path())
```

### Let a writer create the artifact

Use writer-backed `add_artifact(...)` when ogcat should plan the target path
and domain code should create the artifact during the catalog operation.

```python
normalised_flux_modifications = (
    "Normalized the raw CO2 agriculture flux to a single CF-style flux variable "
    "and preserved coordinate metadata for a small documentation example."
)

normalised_flux_metadata = {
    "title": "Tiny normalized CO2 agriculture flux",
    "product": "EDGAR",
    "species": "co2",
    "domain": "EUROPE",
    "year": 2021,
    "keywords": ["agriculture", "normalized_total_flux"],
    "provenance": "derived",
    "format": "netcdf",
    "processing_stage": "normalized_total_flux",
    "inputs": [raw_record.id],
    "modifications": normalised_flux_modifications,
}

normalised_flux_target = catalog.root / catalog.spec.files_root / "flux" / "derived" / "tiny-normalized-flux.nc"
normalised_flux_plan = catalog.plan_artifact_storage(
    record_type="derived_flux",
    locator=ArtifactLocator.from_path(
        normalised_flux_target,
        relative_path="data/flux/derived/tiny-normalized-flux.nc",
    ),
    target_kind="file",
    write_mode="write",
    metadata=normalised_flux_metadata,
)


def write_normalised_flux(source, target: Path) -> dict[str, object]:
    """Write a tiny NetCDF-shaped flux artifact and return derived metadata."""
    write_example_flux_netcdf_or_placeholder(target)
    return {
        "reader_hint": "xarray.open_dataset",
        "source_record_ids": source.metadata["source_record_ids"],
        "source_paths": source.metadata["source_paths"],
        "output_variables": ["flux"],
        "sizes": {"time": 1, "lat": 2, "lon": 3},
        "units": "kg m-2 s-1",
        "processing_stage": "normalized_total_flux",
        "history": source.metadata["modifications"],
        "modifications": source.metadata["modifications"],
    }


normalised_flux_record = catalog.add_artifact(
    record_type="derived_flux",
    storage_plan=normalised_flux_plan,
    metadata=normalised_flux_metadata,
    source=memory_source(
        {"stage": "normalized_total_flux"},
        kind="normalised_flux_payload",
        metadata={
            "source_record_ids": [raw_record.id],
            "source_paths": [str(raw_record.locator.value)],
            "modifications": normalised_flux_modifications,
        },
    ),
    artifact_writer=source_writer(
        write_normalised_flux,
        target_kind="file",
        source_kind="normalised_flux_payload",
    ),
)
print(normalised_flux_record.path())
```

## Raw and derived metadata conventions

Use user metadata for fields that users search and select on:

| Field | Suggested use |
| --- | --- |
| `provenance` | Use `"raw"` for original or externally supplied data and `"derived"` for processed outputs. |
| `inputs` | Store input record ids, external labels, or both. Keep values JSON-compatible. |
| `processing_stage` | Name the workflow stage, such as `downloaded`, `normalized_total_flux`, or `forward_model_input`. |
| `format` | Store the normalised format users search for, such as `netcdf`, `zarr`, `zip`, or `text`. |
| `modifications` | Store a short, human-readable processing note that explains what changed and why. For NetCDF outputs, also append the same idea to the dataset `history` attribute. |
| `reader_hint` | Prefer this in derived metadata when it describes how to reopen the artifact, such as `xarray.open_dataset` or `xarray.open_zarr`. |

Use derived metadata for facts extracted from or produced alongside the
artifact:

```python
derived_metadata = {
    "reader_hint": "xarray.open_dataset",
    "source_record_ids": [raw_record.id],
    "source_paths": [str(raw_record.locator.value)],
    "output_variables": ["flux"],
    "sizes": {"time": 1, "lat": 1, "lon": 1},
    "chunks": {"time": 1, "lat": 1, "lon": 1},
    "modifications": "Regridded source fluxes and filled missing values with 0.0.",
}
```

For multi-input products, named source paths are often easier to audit months
later than a bare list:

```python
scenario_source_paths = {
    "primary_o2": "/hpc/shared/atmos/verification-games/data/raw/PARIS_ATEN_O2.nc",
    "FF": str(raw_record.locator.value),
    "GPP": "/hpc/shared/atmos/some_flux_collection/sib4_gpp_2021.nc",
    "TER": "/hpc/shared/atmos/some_flux_collection/sib4_ter_2021.nc",
    "ocean": "/hpc/shared/atmos/some_flux_collection/cesm_scaled_ocean_2021.nc",
}
```

For general field-discovery and search-containment rules, see
[Metadata and validation](../concepts/metadata-and-validation.md),
[Search and record sets](../api/search.rst), and [CLI](../cli.md).

When you are done with the temporary example catalog:

```python
work.cleanup()
```
