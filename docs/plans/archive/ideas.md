# Ideas

This note collects possible next-step ideas that build on the current artifact
locator work without committing the core package to them yet.

## Grouped Search Results

Current search returns one row per matching record. File-level catalogs can be
noisy for datasets that are naturally monthly file series.

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

Before adding this helper, define how it handles missing grouping keys,
stable ordering, date parsing, and deleted records. Gap detection also needs
an explicit expected cadence; a missing month cannot be inferred from a date
range alone.

Related CLI follow-up:

- current CLI search output is still record-oriented
- future work could add grouped or collapsed search views, especially for
  monthly file series
- output modes such as `--paths` may need explicit semantics for mixed
  path-backed and non-path-backed result sets

## Existing Collection References And Managed Collections

`Catalog.add_collection()` already represents one existing directory, URI, or
URL-path collection as a logical record. It stores a locator and cheap
classification metadata. It does not manage the collection's members or
persist collection claims and facets for capability-based reading.

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

Applications may use a domain-specific `record_type`, such as:

- `external_collection`

`managed_store` would need a separate managed write and update contract; the
record type alone would not make a collection managed.

Why one collection record can make sense:

- better matches "one dataset, many files"
- leaves room for directory-backed stores and transform outputs

Tradeoff:

- collection records are convenient summaries, but they lose direct one-record
  per-file visibility unless paired with file-level records or derived indexes
- current collection references are not yet managed collection updates or
  reader-dispatch-ready collection descriptors

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

- add an explicit artifact update operation; current metadata update methods
  do not append or replace artifact members
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

Artifact descriptors already exist. Before persisting a member manifest,
choose one authoritative representation: a bounded manifest facet on the
collection descriptor or a separate manifest artifact. Derived classification
metadata may summarize the collection but should not become a second source of
truth for its members.

An append operation also needs a concurrency contract spanning member writes,
manifest changes, and record updates. Locking only individual database calls
would leave that sequence exposed to concurrent updates. Structured writer
results and rollback behavior should be settled first.

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

Records already support non-path locators such as URIs. Runtime access through
readers and managers remains limited.

Examples:

- `s3://bucket/path/to/data.zarr`
- `gs://bucket/path/to/file.nc`
- other opaque references that are not local filesystem paths

Possible next steps:

- define reader behavior for URI and URL-path locators after local-path reads
- keep runtime access and credential policy in optional integrations
- preserve the distinction between a stored locator and an opened handle

Why extending runtime access makes sense:

- makes existing remote references usable through an appropriate reader
- keeps storage credentials and network behavior outside the core catalog model

## Reader Hints For Collections

`Catalog.add_collection()` already accepts a human-readable `reader_hint` and
stores it in derived classification metadata. For example, a caller can record
`reader_hint="xarray.open_mfdataset"` for a monthly NetCDF series.

The hint is advisory, not executable dispatch state. A future collection reader
needs explicit collection claims and facets, a requested interface, and a
registered capability. It should not select code solely from the hint text.
