# Architecture

`ogcat` is a small, spec-driven catalog for managed artifacts, with managed local files as the
current MVP. The current implementation is deliberately narrow: it creates a catalog on disk,
stores records in a lightweight database, ingests files by copy or move, derives a small amount of
metadata when possible, and exposes search through both Python and CLI.

## Current Architecture

The main pieces are:

- `CatalogSpec`: the serialisable catalog definition stored in `catalog.json`
- `Catalog`: the user-facing API for creating, opening, adding, searching, and resolving record paths
- `CatalogRepository`: a protocol for record storage
- `TinyDbCatalogRepository`: the current repository implementation
- `CatalogRecord`: the stored record model
- naming helpers: template rendering for managed storage paths
- extractors: optional post-ingest metadata extraction
- operation runners: internal coordinators for validated catalog operation lifecycles
- CLI: a thin wrapper around the catalog API

The catalog root is self-describing:

```text
<catalog-root>/
  catalog.json
  db.json
  data/
    files/
    objects/
```

`catalog.json` defines how the catalog behaves. `db.json` stores records.
`data/files/` holds human-readable template replicas and template-primary
artifacts. `data/objects/` holds UUID primary objects for default managed
ingest.

## First Pass Artifact Generalisation

The current record model now separates two ideas that were previously collapsed into one stored
path:

- `record_type`: what the catalogued thing is, such as `managed_file`
- `locator`: how that thing is located, such as a local `path` or a future URI

This is intentionally small. The goal is not to introduce a full abstraction framework, only to
stop the internal model from assuming every record is a copied or moved local file forever.

For the current MVP:

- `add_file()` still performs managed ingest by copy or move
- managed file records store a `path` locator for the primary artifact, which
  defaults to a UUID-backed path under `data/objects/`
- schema templates create derived symlink replicas and generated views rather
  than defining the primary path by default
- compatibility fields `stored_abspath` and `stored_relpath` remain present for today's APIs and
  CLI
- `Catalog.path()` resolves only path-backed records and returns `None` for records that are
  missing or not path-backed

This leaves room for later work on managed directory-like stores, external references, and
pre-allocated transform targets without forcing those features into the first pass.

## Why Hide TinyDB Behind a Repository Abstraction

TinyDB is the current storage backend, but the rest of the package depends on a repository protocol rather than on TinyDB APIs directly. This keeps the catalog logic focused on records and search semantics instead of persistence details.

The abstraction is useful even in the current small codebase:

- it keeps `Catalog` independent from backend-specific query and update code
- it makes tests simpler because record operations are expressed in terms of the protocol
- it leaves room for backend changes later without rewriting the public catalog API

This is not a claim that multiple backends already exist. It only means the package boundary is already in place.

## Transaction Boundaries And Rollback

Catalog writes use a lightweight unit-of-work helper for multi-step operations such as managed
file ingest. With the current TinyDB backend this is a best-effort rollback mechanism based on
compensating actions: staged records can be deleted and owned copied files can be removed if a later
step fails. It is not a true database transaction and should not be described as ACID.

Each unit of work exposes an `operation_id` so future audit logging or hooks can correlate staged
record writes, storage activity, and cleanup. Stronger backends can map the same conceptual API to
native transaction support later.

## Operation Runners

`Catalog` remains the public API and setup layer: it validates user inputs, selects schemas, opens
transactions, and prepares operation-specific request objects. Internal operation runners own the
ordered lifecycle after that setup.

The current concrete runner is `AddOperationRunner`. It implements the add lifecycle for
`add_file()` and `add_artifact()`: validation, hook dispatch, locator resolution, storage planning,
artifact writing or reference skipping, derived metadata collection, record staging, commit, audit,
and rollback. `OperationRunner` is the generic internal command interface with a single `run()`
method, so future operation families can be represented without overloading add-specific names.

Shared catalog services such as hook management, audit emission, validation, and record building are
passed through `OperationServices`. Operation-specific data lives in request dataclasses such as
`AddOperationRequest`. This keeps the public catalog surface narrow while making the internal
contract explicit enough for future runners, such as an artifact update runner, to reuse the same
services without sharing add-only request fields.

## Why Templates and Metadata Live in `catalog.json`

The default schema, optional named record schemas, default operation, and field resolution order
are stored in `catalog.json` so a catalog remains self-describing on disk.

That choice has a few practical benefits:

- a catalog can be opened without separate application configuration
- stored records can be interpreted in the context of the catalog that produced them
- schema-level metadata field descriptions travel with the catalog instead of being hard-coded elsewhere

Schemas are intentionally lightweight. Required metadata fields are checked at ingest, but there
is no deep type validation or domain-specific schema language in the catalog core.

## User Metadata and Derived Metadata

Each record separates metadata into distinct areas:

- top-level reserved fields for catalog bookkeeping
- `user_metadata` for metadata supplied at ingest time
- `derived_metadata` for metadata extracted from the stored file after ingest

This separation matters for both clarity and search behavior. Unqualified field lookup resolves in this order:

1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

If a caller wants an exact nested location, dotted paths can bypass flattened lookup, for example `user_metadata.product.family.name` or `derived_metadata.netcdf.dims.time`.

The current derived metadata layer is intentionally small. For netCDF files, if `xarray` is installed, the extractor records a compact summary of dimensions, variables, coordinates, and selected attributes.

## Current Limitations

The present architecture is intentionally constrained.

- richer locator handling is still future work; today the generalisation is intentionally minimal
- TinyDB is the only supported backend
- metadata validation is intentionally shallow and limited to required-field presence
- search supports exact equality, contains, and regex matching only
- there are no reader hooks, manager APIs, or import or scan workflows yet
- extractor support is limited and should not be described as a general reader framework

These limitations are deliberate. The package is currently a compact catalog core, not a full data management system.
