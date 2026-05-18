# Virtual Artifact Filesystem Research Notes

These notes collect the research context for issue #108 before drafting the ADR.
They are intentionally descriptive rather than decisive: the ADR should make the
actual architecture decision after these concepts have been reviewed.

## Inputs Reviewed

- GitHub issue #108, "Plan filesystem-like artifact interfaces and structured operator pipelines".
- GitHub issue #109, "Make managed collections first-class operation targets".
- Current `ogcat` code around `CatalogRecord`, `ArtifactLocator`, `StoragePlan`,
  `OperationSource`, `ArtifactWriter`, `OperationContext`, collections, replicas,
  secondary artifacts, and plugins.
- User-supplied notes:
  - `/Users/bm13805/Desktop/temp/chatgpt_unix_filesystem_review.md`
  - `/Users/bm13805/Desktop/temp/chatgpt_file_system_ideas_5_may_2026.md`
  - `/Users/bm13805/Downloads/ChatGPT-Intake_package_architecture.md`
- Local Intake source checkout at `/Users/bm13805/Documents/intake`.
- Unix filesystem concepts from the supplied TLPI copy, used only as an analogy:
  descriptors, open file descriptions, inodes, `stat`, directories, links,
  mounts, VFS, device files, pipes, and `ioctl`.

## Current ogcat Shape

`CatalogRecord` is currently the persisted logical entry. It has one primary
`ArtifactLocator`, compatibility path fields, and three metadata namespaces:
`user_metadata`, `derived_metadata`, and `naming_metadata`.

`ArtifactLocator` is intentionally small: `kind`, `value`, and optional
`relative_path`. Path and URL-path locators have storage behavior today; URI and
opaque locators are primarily reference forms.

Storage and materialization are currently shaped around `StoragePlan` and
`target_kind`, with `target_kind` limited to `file` and `directory`.
`StoragePlan` describes where an artifact should be written or referenced and
whether ogcat owns it.

`OperationSource`, `ArtifactWriter`, and `OperationContext` are the existing
write-side boundary. Writers materialize data into an `ArtifactLocator` and can
mutate `context.derived_metadata` or register rollback actions.

Collections are not a storage target today. They are explicit classification
metadata layered over directory-like locators. This is a useful direction to
preserve: collection-ness should not become `target_kind="collection"`.

Replicas and secondary artifacts currently exist as local symlink-oriented views
or add-operation side effects. They point toward a future model where records can
own multiple artifact descriptors: primary data, view links, previews, logs,
manifests, and derived outputs.

## Unix Filesystem Analogy

The useful Unix lesson is dependency inversion through small capability
interfaces, not a literal POSIX clone.

Important separations:

- Pathname is separate from filesystem object.
- Persistent object metadata is separate from runtime open handle.
- Logical byte offsets and metadata are separate from physical placement.
- Programs depend on descriptors and common operations rather than concrete disk
  implementations.

Mapping to ogcat:

- Pathname or logical name maps to record lookup, artifact role, or future
  `ogcat://` resolver.
- Inode-like object maps roughly to `CatalogRecord` plus `Artifact`.
- File descriptor maps to a runtime artifact handle. Handles should never be
  persisted.
- `stat` maps to cheap artifact inspection and facets.
- VFS maps to storage adapters plus reader/accessor registries.
- Device-specific `ioctl` maps to namespaced plugin-specific options, not an
  excuse for unstructured core APIs.

Cautions:

- Object-store prefixes are not POSIX directories. They may lack stable parent
  directories, link counts, timestamps, atomic rename, or complete listing
  semantics.
- "Everything is a file" should be read as "many resources can be accessed
  through common capabilities". It is not a claim that every resource supports
  every operation.
- ogcat should support structured data interfaces, not only byte streams.

## Intake Take2 Research Summary

Intake Take2's strongest design move is splitting old-style drivers into:

- `BaseData`: external/source data description, such as CSV, HDF5, Zarr,
  SQL query, service, catalog file, STAC JSON, or a literal value.
- `BaseReader`: a recipe that materializes a `BaseData` instance into a runtime
  Python object.
- `output_instance`: a string naming the runtime output type, such as
  `pandas:DataFrame`, `xarray:Dataset`, or `intake.readers.entry:Catalog`.
- `BaseConverter`: a reader-like conversion step from one runtime output type to
  another.
- `Pipeline`: an ordered sequence of readers/converters that is itself a reader.
- Output converters: side-effecting converters that write data and return a new
  `BaseData` description.
- `DataDescription` and `ReaderDescription`: serializable catalog-time forms.

This is relevant to ogcat, but ogcat should not copy Intake directly. Intake is
optimized for "given a thing, produce a Python object." ogcat should be
optimized for "given an artifact or planned artifact, record identity, metadata,
provenance, location, validation, and operations."

Recommended mapping:

| Intake concept | ogcat target concept |
| --- | --- |
| `BaseData` | `DataTypeClaim` plus artifact locator/representation |
| `BaseReader` | plugin reader/accessor capability |
| `output_instance` | runtime handle/data model produced by a reader |
| `BaseConverter` | typed converter operation |
| output converter returning `BaseData` | writer/converter producing a new artifact descriptor |
| `DataDescription` | serializable data-type/access recipe metadata |
| `ReaderDescription` | serializable reader recipe or operation input |
| Intake catalog | possible artifact/interface, not core dependency |

Security and durability cautions:

- Intake's dynamic import/template mechanisms are powerful but too permissive
  for durable ogcat core metadata.
- ogcat should use explicit trusted plugin capabilities.
- Core metadata should remain JSON-compatible, namespaced, and safe to inspect
  without importing pandas, xarray, Intake, or domain plugins.

## Target Vocabulary For ADR Discussion

The ADR should decide how much of this vocabulary becomes persistent schema now
versus prototype metadata first.

`CatalogRecord`
: Logical catalog entry. Owns catalog identity, domain/search metadata, schema
  association, lifecycle state, provenance links, and associated artifacts.
  `record_type` remains a schema/search concept, not an I/O dispatch key.

`Artifact`
: Concrete or virtual thing associated with a record: primary file, managed
  directory, external reference, preview, manifest, log, replica, template link,
  member, or derived output.

`Locator`
: Where an artifact is or will be: path, urlpath, URI, opaque identifier,
  query, service endpoint, or future `ogcat://` locator.

`DataTypeClaim`
: Intake-aligned external/source type claim: CSV, HDF5, NetCDF3, Zarr, zip,
  SQL query, service, catalog, collection pattern, etc. It should carry evidence
  and confidence.

`Representation`
: Storage or encoding shape: file, directory, prefix, stream, archive, compressed
  container, Zarr store, NetCDF file, text file, or collection layout.

`InterfaceClaim`
: Capability contract exposed by an artifact: bytes, text lines,
  directory-listing, archive-members, table, xarray-dataset, zarr-group,
  manifest, collection, or catalog.

`Facet`
: Namespaced structured facts used by a data type, representation, interface, or
  plugin: file stats, checksums, suffixes, NetCDF dimensions, Zarr metadata,
  table schema, collection pattern, expected member count, reader hints, or
  validation outputs.

`Reader`
: Capability that maps artifact plus claims/facets/options to a runtime handle or
  object.

`Writer`
: Capability that maps runtime data, source handles, or operation inputs into
  materialized artifacts plus claims/facets.

`Converter`
: Capability that maps one runtime data model to another, optionally
  materializing a new artifact.

`Handle`
: Runtime opened object for one artifact through one interface/reader. Handles
  are operation-scoped and never persisted.

`Operation`
: Execution envelope that consumes and produces records/artifacts/handles
  through declared readers, writers, and converters. Owns validation, rollback,
  audit, and provenance.

## Target Object Relationships

- Catalog has many records.
- Record owns one or more artifacts.
- Record normally has one active primary artifact.
- Artifact has one canonical locator once materialized or referenced.
- Artifact may have multiple data type claims, representations, interfaces, and
  facets.
- Interface may have many registered readers and writers.
- Operation consumes zero or more input artifacts/handles and produces zero or
  more output artifacts/records/facets.
- Handle belongs to exactly one opened artifact/interface/reader invocation.

Open question for the ADR: whether artifacts are persisted inline on records,
stored in a separate artifact registry, or prototyped first under
`derived_metadata["ogcat"]`.

## Collections

Collections should be modeled as capabilities layered over storage shape.

The storage shape should remain `directory`, `prefix`, `archive`, or similar.
The collection meaning should live in data type, representation, interface, and
facet metadata:

- Data type: `collection`, `netcdf-collection`, `csv-pattern`, etc.
- Representation: directory/prefix/archive members form a logical dataset.
- Interface: `collection`, `manifest`, `xarray-mfdataset`, or domain-specific
  collection interface.
- Facets: `collection_pattern`, `member_format`, `member_suffixes`, expected
  count, ordering, partition keys, member manifest, reader hints.

A plain directory reference should remain a directory unless explicitly claimed
as a collection. A managed collection output should be a directory or prefix
artifact with collection claims and facets.

## Likely Issue Sequence

These are planned issue slices. They should remain checklist items on #108 until
the ADR makes each slice concrete enough to open as a separate sub-issue.

1. ADR: virtual artifact filesystem vocabulary and boundaries.
2. First-class artifact descriptor target model.
3. Data type, representation, interface, and facet claim schemas.
4. Reader, writer, and converter capability registry.
5. Managed collections as operation targets, including #109.
6. Structured artifact writer result model.
7. Read-side artifact handles and accessors.
8. Intake plugin design spike.
9. Migration and compatibility plan for existing single-locator catalogs.
10. Virtual namespace and `ogcat://` resolver exploration.

## ADR Questions To Resolve

- Should the first persistent step be inline `CatalogRecord.artifacts`, a
  separate artifact registry, or prototype metadata under `derived_metadata`?
- Should `DataTypeClaim` and `InterfaceClaim` be separate persisted structures,
  or should one claim type carry both source form and access capability?
- How much reader/writer/converter recipe information is safe to persist in core
  metadata?
- Which confidence values are required: declared, inferred, probed, validated,
  stale, failed?
- How should plugin capability names be versioned and namespaced?
- What belongs in core versus optional plugins for bytes/text/directory/archive,
  NetCDF, Zarr, xarray, pandas, Intake, and OpenGHG-specific data?
- How should operation provenance be represented before a full provenance graph
  exists?
- Which migration path is acceptable for existing catalogs with one primary
  `record.locator`?

## Acceptance Scenarios For ADR

- Existing single-file artifact still works through `record.locator`.
- A record can own primary data plus preview/log/manifest artifacts.
- A directory reference is not a collection unless explicitly claimed.
- Managed zip to NetCDF collection writes one logical collection artifact.
- One artifact can claim multiple interfaces, for example bytes plus NetCDF plus
  xarray dataset.
- Readers dispatch by claims/interfaces, not by `record_type`.
- Suffix-only detection is represented as inferred evidence, not validated truth.
- Optional Intake, xarray, and pandas integrations can be absent without breaking
  core catalog use.
