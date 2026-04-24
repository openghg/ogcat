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
- CLI: a thin wrapper around the catalog API

The catalog root is self-describing:

```text
<catalog-root>/
  catalog.json
  db.json
  files/
```

`catalog.json` defines how the catalog behaves. `db.json` stores records. `files/` is the managed storage root for ingested files.

## First Pass Artifact Generalisation

The current record model now separates two ideas that were previously collapsed into one stored
path:

- `record_type`: what the catalogued thing is, such as `managed_file`
- `locator`: how that thing is located, such as a local `path` or a future URI

This is intentionally small. The goal is not to introduce a full abstraction framework, only to
stop the internal model from assuming every record is a copied or moved local file forever.

For the current MVP:

- `add_file()` still performs managed ingest by copy or move
- managed file records store a `path` locator
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

## Why Templates and Metadata Live in `catalog.json`

The naming templates, default operation, field resolution order, and descriptive metadata field definitions are stored in `catalog.json` so a catalog remains self-describing on disk.

That choice has a few practical benefits:

- a catalog can be opened without separate application configuration
- stored records can be interpreted in the context of the catalog that produced them
- metadata field descriptions travel with the catalog instead of being hard-coded elsewhere

`metadata_fields` are descriptive only today. They document important fields and examples, but they are not enforced as strict schemas.

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
- metadata field descriptions are not typed schemas and are not validated on ingest
- search supports exact equality, contains, and regex matching only
- there are no reader hooks, manager APIs, or import or scan workflows yet
- extractor support is limited and should not be described as a general reader framework

These limitations are deliberate. The package is currently a compact catalog core, not a full data management system.
