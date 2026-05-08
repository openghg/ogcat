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

Related CLI follow-up:

- current CLI search output is still record-oriented
- future work could add grouped or collapsed search views, especially for
  monthly file series
- output modes such as `--paths` may need explicit semantics for mixed
  path-backed and non-path-backed result sets

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

## Managed Collection Updates And Member Manifests

Archive-backed scientific datasets often start as many transport files that
should become one logical collection. For example, each annual ``.zip`` may
contain one NetCDF file, and all extracted NetCDF files should live in the same
managed directory.

Current behavior is best suited to:

- one record per extracted file; or
- one collection record written in a single operation by a custom writer.

It is not yet a good fit for incrementally appending files to one managed
directory while updating the same catalog record's metadata. That would require
new semantics beyond the current "target should be absent, writer materialises
it, record is inserted" flow.

Possible future directions:

- add an explicit update/upsert API for catalog records
- distinguish create-only writers from append/update writers
- keep the core update API small, with plugins defining the concrete semantics
  for a given artifact shape
- let a collection writer return a structured manifest of members, including:
  - source archive path
  - archive member name
  - stored relative path
  - year or other member-level keys
  - size and checksum
- store collection-level summaries such as ``file_count``, ``years``,
  ``time_coverage``, and ``member_glob``
- decide whether metadata updates replace, merge, or recompute derived
  collection metadata
- make rollback behavior explicit for append operations, since deleting a whole
  directory may be wrong once a collection already exists

This also points toward a future artifact descriptor model where one logical
record can have many concrete artifact members. Until then, a JSON-compatible
manifest in ``derived_metadata`` could be a useful prototype.

The core package probably should not define one universal meaning for
``update``. It can define the envelope: load an existing record, let a manager
materialise changes, update metadata, and register rollback. A plugin or manager
can then decide whether an update means append, replace, merge, rebuild, or
reject.

A small vendored example could be a generic directory collection manager. It
would treat a managed directory as a simple collection of files, roughly like a
list in memory:

- ``add`` writes one or more new files into the directory
- ``remove`` deletes selected members
- ``replace`` overwrites a selected member
- ``manifest`` returns the current member list and summary metadata

That would mirror the familiar Unix directory file type without committing the
whole model to directories forever. Later, a directory collection record could
be replaced or complemented by a record that points to other records, but a
plain managed-directory collection is likely the most useful first step.

## Archive Member Naming

When an archive is just transport packaging, naming should often use the
archive member rather than the archive file itself. A ``.zip`` named
``GCP-GridFEDv2023.1_2018.zip`` may contain
``GCP-GridFEDv2023.1_2018.nc``; the managed artifact should usually use the
``.nc`` name.

Current options:

- pass an explicit ``locator=ArtifactLocator.from_path(...)`` when planning
  storage
- use a locator-resolution hook to inspect the archive and adjust the planned
  locator before the storage plan is finalised
- write a domain-specific multi-archive writer that controls its directory
  layout and records extracted member metadata

Possible future improvements:

- add an archive-member source descriptor, separate from the physical source
  archive path
- expose naming context fields such as ``artifact_filename`` or
  ``source_member_filename`` without overloading ``original_filename``
- provide a builtin hook or planning helper for "single-member archive uses
  member filename"
- keep ``original_filename`` reserved for the physical source filename, so
  metadata and naming remain easier to reason about

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

## Follow-Up PR: Repository-Owned Search And Record Identity

This should be treated as a focused architectural cleanup PR.

Problem statement:

- the current repository interface is too thin
- `Catalog.search(...)` currently loads `repository.all()` and filters in Python
- that leaks search policy out of the backend layer and prevents backends such
  as TinyDB, SQLite, or MongoDB from owning query execution
- `CatalogRecord` currently requires `id`, even though record identity is really
  assigned by the repository/database layer
- `allocate_record_ids()` is a workaround for that mismatch and should likely be
  removed

Preferred direction:

- move search behind the repository interface
- adopt "option 1" for record identity:
  - `CatalogRecord.id` becomes optional before persistence
  - repository `insert(...)` / `insert_many(...)` assign ids
  - catalog-facing methods return persisted records with ids populated

Suggested scope:

- add a repository search method, likely reusing the current search inputs:
  - `where`
  - `contains`
  - `regex`
  - `ignore_case`
- provide a simple backend implementation for TinyDB
- update `Catalog.search(...)` to delegate to the repository instead of calling
  `all()` and filtering in Python
- remove `allocate_record_ids()` from the repository interface
- update `Catalog.add_file(...)`, `add_artifact(...)`, and `add_artifacts(...)`
  so they build records without ids and persist them through repository methods
  that return ids or persisted records
- keep the public `Catalog` API lightweight
- avoid introducing a large ORM-like abstraction layer

Questions to resolve in that PR:

- should repository `insert(...)` return the assigned id, or the full persisted
  `CatalogRecord`?
- what is the cleanest shape for batch insert return values?
- how should managed-file naming behave if templates want `{id}` before the
  record is persisted?
- should id-based naming remain supported, or should it become discouraged or
  removed from the default design?
- how much query expressiveness should the repository interface expose before it
  becomes too backend-specific?

Desired outcome:

- repository backends own both identity assignment and search execution
- `CatalogRecord` no longer needs caller-supplied ids
- the catalog layer becomes thinner and less tied to backend implementation
  details
