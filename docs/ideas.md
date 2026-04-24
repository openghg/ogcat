# Ideas

This note collects possible next-step ideas that build on the current artifact
locator work without committing the core package to them yet.

## Grouped Search Results

Current search returns one record per matching artifact. That is the right base
behavior for precise file-level lookup, but it can be noisy for datasets that
are naturally monthly file series.

Possible extension:

- keep one record per file in storage
- add a helper that groups search results by selected keys such as:
  - `site`
  - `inlet`
  - `model`
  - `met_model`
  - `domain`
  - `species`
- return summary fields such as:
  - `record_count`
  - `start_date_min`
  - `start_date_max`
  - `years`
  - `months`
  - possible gap information later

Why it makes sense:

- preserves the current simple storage model
- avoids losing per-file fidelity
- improves usability for monthly-series datasets such as footprints

## Collection Records

Another direction is to represent one logical collection instead of one record
per file.

Example:

- one record for `/group/chem/acrg/LPDM/fp_NAME/EUROPE/MHD-10magl/co2/`
- metadata could include:
  - `site=MHD`
  - `inlet=10m`
  - `model=NAME`
  - `met_model=UKV`
  - `domain=EUROPE`
  - `species=co2`
  - `file_count`
  - `start_date`
  - `end_date`
  - `file_pattern`

This would likely be a different `record_type`, such as:

- `external_collection`
- `managed_store`

Why it makes sense:

- better matches "one dataset, many files"
- leaves room for directory-backed stores and transform outputs

Tradeoff:

- collection records are convenient summaries, but they lose direct one-record
  per-file visibility unless paired with file-level records or derived indexes

## Non-Path Locators

The current model already leaves room for non-path locators such as URIs, but
support is intentionally thin.

Examples:

- `s3://bucket/path/to/data.zarr`
- `gs://bucket/path/to/file.nc`
- other opaque references that are not local filesystem paths

Possible next steps:

- keep `locator.kind` explicit, for example `path`, `uri`, or another small set
- preserve non-path values verbatim instead of passing them through `Path(...)`
- add lightweight helpers for path-backed versus non-path-backed behavior
- consider optional higher-level integrations with tools such as `fsspec` later

Why it makes sense:

- avoids locking the model to local files only
- supports cloud or remote references without forcing a heavy abstraction layer

## Reader Hints For Collections

For collection-like artifacts, it may later be useful to attach a lightweight
reader hint rather than a full plugin system.

Examples:

- `artifact_type="netcdf_monthly_series"`
- `reader_hint="xarray.open_mfdataset"`

This should stay advisory rather than executable application state. The catalog
can describe what something is, while higher-level code decides how to open it.
