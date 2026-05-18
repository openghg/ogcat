# Architecture

`ogcat` is a small, spec-driven catalog for managed artifacts, with managed local files as the
current MVP. The current implementation is deliberately narrow: it creates a catalog on disk,
stores records in a lightweight database, ingests files by copy or move, derives a small amount of
metadata when possible, and exposes search through both Python and CLI.

## Current Architecture

The main pieces now sit in four rough layers. These are working boundaries, not
a formal framework:

- **Presentation/API:** `Catalog`, `CatalogRecordSet`, and the CLI expose user
  workflows. They should keep public argument handling and user-facing
  compatibility behavior visible, while delegating operation choreography.
- **Application/orchestration:** `CatalogApplication`, `AddOperationRunner`,
  operation request objects, hooks, audit emission, and units of work coordinate
  complete operations. This layer owns sequencing, rollback boundaries, and
  runner dependencies.
- **Domain/policy:** storage planning, materialisation targets, naming,
  validation, cheap classification, replica-view planning, secondary artifact
  policy, and future collection-artifact policy own catalog rules.
- **Data/infrastructure:** `CatalogRepository`, `TinyDbCatalogRepository`,
  storage adapters, filesystem/fsspec helpers, and bundled writers own
  persistence and side effects.

The most important responsibility split is that `Catalog` is the public facade,
not the operation engine. `Catalog` selects the public operation and validates
public inputs. Application services and runners decide how to execute the
operation. Domain planning modules decide where artifacts and secondary
artifacts belong. Repositories and storage adapters perform persistence and
filesystem-like effects.

Key concrete types are:

- `CatalogSpec`: the serialisable catalog definition stored in `catalog.json`
- `Catalog`: the public API facade for creating, opening, adding, searching, and resolving record paths
- `CatalogApplication`: the internal application service that builds add-operation requests
- `AddOperationRunner`: the current lifecycle runner for `add_file()` and `add_artifact()`
- `StoragePlan` and materialisation targets: explicit storage/write decisions for primary artifacts
- `SecondaryArtifactOperation`: ordered follow-up operations such as template-link symlinks
- `CatalogRepository`: a protocol for record storage
- `TinyDbCatalogRepository`: the current repository implementation
- `CatalogRecord`: the stored record model

Related refactor tracking:

- [#84](https://github.com/openghg/ogcat/issues/84): umbrella `Catalog` facade and operation-boundary refactor
- [#87](https://github.com/openghg/ogcat/issues/87): application orchestration extraction
- [#88](https://github.com/openghg/ogcat/issues/88): template symlinks as secondary artifact operations
- [#89](https://github.com/openghg/ogcat/issues/89): replica module and interface-test cleanup
- [#92](https://github.com/openghg/ogcat/issues/92): naming-template protected fields and internal identifiers
- [#70](https://github.com/openghg/ogcat/issues/70): collection artifacts for directory-backed datasets

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

`Catalog` remains the public API facade: it validates user inputs, selects schemas, and delegates
add-operation orchestration to the internal `CatalogApplication` service. The application service
prepares operation-specific request objects, wires managed-file storage planning to copy/move
writers, and invokes the operation runner. This keeps the public API methods focused on the API
contract rather than the add lifecycle details.

The current concrete runner is `AddOperationRunner`. It implements the add lifecycle for
`add_file()` and `add_artifact()`: validation, hook dispatch, locator resolution, storage planning,
artifact writing or reference skipping, derived metadata collection, record staging, required
secondary artifact operations, commit, audit, and rollback. `OperationRunner` is the generic
internal command interface with a single `run()` method, so future operation families can be
represented without overloading add-specific names.

Shared catalog services such as hook management, audit emission, validation, and record building are
passed through `OperationServices`. Operation-specific data lives in request dataclasses such as
`AddOperationRequest`. This keeps the public catalog surface narrow while making the internal
contract explicit enough for future runners, such as an artifact update runner, to reuse the same
services without sharing add-only request fields.

Writer materialisation is represented explicitly by an internal materialisation intent and target.
A primary artifact plan answers where the artifact belongs, and exposes that decision as a
materialisation target. The materialisation intent answers how bytes or directories are produced
there, if at all. That distinction is important for future directory-backed collection artifacts
and `.zarr`-style outputs because `Catalog` should not branch on file versus directory versus
collection.

Secondary artifacts, such as the optional default human-readable template symlink for UUID primary
storage, are modeled as ordered secondary operations. Any selected secondary operations run after
the primary record is staged and has an id, but before commit, so their filesystem effects and
record metadata updates remain part of the same rollback boundary.

## Collection Artifacts And Directory Targets

Collection artifacts are logical records for directory-backed datasets, such as a directory of
NetCDF files or a future generated `.zarr` store. Architecturally, collection-ness should not be a
third physical storage target kind. Physical targets remain file-like or directory-like; collection
semantics live in domain policy and derived classification metadata.

That means the existing operation model should still apply:

- the primary artifact plan chooses the canonical file or directory locator
- the materialisation intent decides whether a writer produces that target, skips writing for a
  record-only reference, or delegates to a future directory writer
- collection policy records cheap metadata such as member pattern, member format, and reader hints
- secondary artifact operations remain separate follow-up work after the primary record is staged

Keeping collection semantics above storage adapters avoids making `Catalog` branch on special
directory cases and keeps remote or user-managed collections possible without scanning member files
by default.

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
