# ADR 0002: Virtual Artifact Filesystem Domain Model

- **Status**: Proposed
- **Date**: 2026-05-18
- **Related issues**: [openghg/ogcat#108](https://github.com/openghg/ogcat/issues/108), [openghg/ogcat#109](https://github.com/openghg/ogcat/issues/109)
- **Research note**: [Virtual Artifact Filesystem Research Notes](../design-note-virtual-artifact-filesystem-research.md)

## Context

`ogcat` currently presents a catalog record as the main persistent object.
Each `CatalogRecord` has one primary `ArtifactLocator`, compatibility path
fields, and metadata namespaces for user, derived, and naming metadata.
`add_file()` handles managed local-file ingest. `add_reference()` records an
existing locator. `add_collection()` records a directory-like logical dataset.
`add_artifact()` exposes a lower-level operation path with storage plans and
artifact writers.

This works for the current local catalog model, but issue #108 asks for a more
explicit domain model: `ogcat` should behave like a virtual artifact
filesystem. Logical catalog objects should be separate from physical storage,
and artifacts should expose typed access capabilities through readers, writers,
and converters. The analogy is Unix-like design, not POSIX compatibility:
dependency inversion through stable interfaces is the important lesson.

The target model also needs to align with two external design references:

- iRODS separates logical data objects and collections from physical replicas
  on storage resources. It is a useful reference for resources, replicas,
  cache/archive policy, and catalog-managed state.
- Intake Take2 separates data descriptions, readers, converters, pipelines, and
  output writers. It is a useful reference for typed access and composition,
  but Intake should remain an optional integration rather than a core
  dependency.

The design must keep personal use simple. TinyDB plus local files on one
machine should continue to provide useful search, storage organization, logging,
provenance, and a low-ceremony path or urlpath that users can pass to ordinary
libraries such as xarray or pandas.

## Requirements

- Preserve existing single-locator use through `record.locator` or a clear
  compatibility shortcut.
- Separate logical catalog identity from physical storage location.
- Let one record own multiple artifacts: data, preview, manifest, log, view
  link, replica, cache copy, archive copy, or derived output.
- Keep `record_type` as logical/schema metadata, not an I/O dispatch key.
- Dispatch reads and writes through artifact claims, interfaces, and registered
  capabilities.
- Treat collections as capabilities layered over directory, prefix, archive, or
  manifest storage shapes. Do not add `target_kind="collection"`.
- Keep core dependency-light. Scientific readers, Intake integration, fsspec
  caching policy, HPC staging, and OpenGHG-specific behavior should be plugins
  or optional bundled capabilities.
- Allow plugins or application code to define domain-specific exactness,
  versioning, validation, and source identity rules.
- Plan for permissions, locks, leases, and basic isolation, while acknowledging
  that TinyDB and NFS-backed filesystems cannot provide strong multi-user
  guarantees by themselves.

## Decision

Adopt a virtual artifact filesystem domain model.

The core persistent model should distinguish records, artifacts, locators,
claims, facets, resources, operations, and runtime handles. The names below are
target vocabulary for implementation and documentation; exact class names may
evolve during implementation.

`CatalogRecord`
: Logical catalog entry. Owns record identity, schema/search metadata,
  lifecycle state, provenance links, and one or more artifact descriptors.
  `record_type` remains a logical/schema concept.

`Artifact`
: Persistent descriptor for one concrete or virtual thing associated with a
  record. Artifacts include data artifacts, auxiliary artifacts, view links,
  manifests, previews, logs, derived artifacts, replicas, cache copies, and
  archive copies.

`Locator`
: Serializable description of where an artifact is or can be resolved. Locators
  may be path, urlpath, URI, opaque identifier, service endpoint, query, or a
  future `ogcat://` reference. Target locators should support
  `(mount_id, relative_path)` semantics so the same catalog identity can resolve
  to a local path, SSH/fsspec urlpath, object-store urlpath, or staged location
  depending on runtime context.

`StorageResource`
: Named storage or access profile with adapter, root, capabilities, credentials
  profile, health, and policy metadata. Resources are the natural place to
  model cache, archive, and HPC staging policy.

`DataTypeClaim`
: Intake-aligned claim about the external/source form, such as CSV, HDF5,
  NetCDF, Zarr, zip, SQL query, service, catalog, or collection pattern. Claims
  carry evidence and confidence.

`Representation`
: Storage or encoding shape: file, directory, prefix, stream, archive,
  compressed container, Zarr store, NetCDF file, text file, or collection
  layout.

`InterfaceClaim`
: Capability contract exposed by an artifact: bytes, text lines, directory
  listing, archive members, table, xarray dataset, zarr group, manifest,
  collection, or catalog.

`Facet`
: Namespaced structured facts about an artifact, claim, interface, or
  operation. Examples include size, checksum, suffixes, NetCDF dimensions, Zarr
  metadata, table schema, collection pattern, expected member count, member
  manifest, reader hints, validation results, and stale evidence.

`Reader`
: Capability that opens an artifact through a declared interface and returns a
  runtime handle or object.

`Writer`
: Capability that materializes runtime data, source handles, or operation inputs
  into artifacts and returns structured artifact result metadata.

`Converter`
: Capability that maps one runtime interface to another, optionally
  materializing a new artifact.

`Handle`
: Runtime opened object for one artifact through one reader/interface. Handles
  are never persisted. A handle may hold a read or write lease. It is scoped to
  an operation or explicit user-owned context.

`Operation`
: Execution envelope for typed pipelines. Operations coordinate reader,
  writer, and converter invocation; authorization; lock/lease acquisition;
  validation; staging; rollback; audit; and provenance.

## Artifact Roles And Replication

Do not overload `primary`.

For backward compatibility, `record.locator` may remain the shortcut to the
record's current data artifact locator when such a locator exists. It should not
also mean write leader, preferred read location, or replica authority.

Use separate concepts:

- `data_artifact`: artifact that application code or plugins treat as "the
  data" for a record.
- `auxiliary_artifact`: supporting artifact such as preview, log, manifest, or
  report.
- `view_link`: symlink-like or alias-like namespace convenience. Current
  symlink "replicas" should be described this way.
- `leader` or `write_leader`: artifact copy used as the authority for writes or
  freshness comparison.
- `preferred_read_location`: access-routing result. This may choose a local
  copy, nearby HPC cache, geographically convenient copy, or the leader.
- `replica`: independently materialized exact copy of artifact content.
- `cache_copy`: copy managed for access speed and allowed to become stale under
  a declared policy.
- `archive_copy`: durable or deep-storage copy that may require restore before
  normal readers can open it.
- `derived_artifact`: transformed output linked by provenance, not a replica
  unless a domain-specific invariant says it is exact.

Core should provide reasonable exactness defaults: file digest for single-file
artifacts and a member manifest with checksums for directory, prefix, archive,
or collection artifacts. Plugins may define stronger or domain-specific
invariants. Source-provided version identifiers and URIs are important facts
for remote sources, but metadata alone is usually too weak to prove replica
exactness.

Replicas in this model are primarily for performance and access management, not
for replacing backups or turning `ogcat` into a distributed database.

## Collections

The word "collection" has two plausible meanings. The ADR reserves room for
both:

- Artifact-level collection: one artifact whose members form a logical dataset,
  such as a directory, prefix, archive member tree, or manifest of files.
- Record-level collection: a catalog subset, table, or view of `CatalogRecord`
  objects, possibly across mounted catalogs.

This ADR decides only the artifact-level model. Artifact-level collections are
directory, prefix, archive, or manifest-shaped artifacts with collection claims,
interfaces, and facets. A plain directory is not a collection unless explicitly
claimed as one.

Issue #121 and PR #122 clarified this boundary for managed directory artifacts:
a `.zarr` directory ingested through `add_file()` is one directory-backed data
artifact, not a collection. A directory of NetCDF members opened with a glob is
a collection only when the artifact carries explicit collection claims/facets
such as member pattern, member format, member suffixes, and reader hint. Storage
shape (`directory`) must remain separate from data type (`zarr`, `netcdf`) and
access interface (`collection`, `zarr-group`, `xarray-dataset`).

Managed collection writes should be supported by the general artifact operation
model. Issue #109 should be the first concrete pressure test: archive members
can be extracted into managed directory or prefix storage and recorded as one
logical collection artifact.

## Capability Registry And Pipelines

Readers, writers, and converters form a typed pipes-and-filters architecture.
The pipe is a runtime interface, not necessarily a byte stream.

Example:

```text
zip artifact
  -> Reader[archive-members]
  -> Writer[managed directory + collection facets]
  -> Reader[xarray.Dataset]
  -> Converter[domain boundary-condition dataset]
  -> Writer[Zarr store artifact]
```

Capabilities should be registered through a plugin-facing registry. Core can
ship or vendor useful capabilities, but they should use the same registration
route as external plugins.

Core responsibilities:

- Artifact descriptor, locator, claim, facet, resource, operation, permission,
  and lock vocabulary.
- Operation orchestration, rollback, audit, provenance stubs, and handle
  lifecycle.
- Minimal dependency-light interfaces such as bytes, text, directory listing,
  archive members, and manifest when feasible.
- Clear errors when a reader/plugin is missing.

Plugin or optional-extra responsibilities:

- NetCDF, HDF5, Zarr, xarray, pandas, SQL, Intake, and OpenGHG-specific readers.
- CAMS archive extraction and boundary-condition transforms.
- fsspec caching policy beyond simple urlpath support.
- HPC staging, deep-storage restore, async replication, reconciliation, and
  external storage integrations.
- Domain validation and source-specific versioning rules.

Implementation clarification for #119: the first capability registry slice is a
registration and lookup layer only. Selection starts from explicit caller
requests for input and output claims or interfaces and matches those requests
against `ArtifactDescriptor` claims and facets. It must not dispatch from
`record_type`. If one artifact advertises several interfaces, such as bytes,
text, table, and JSON, selection should report ambiguity until the caller asks
for a specific enough interface. Public read-handle lifecycle APIs remain #118,
and catalog merge of writer-produced artifact claims/facets remains #117.
Bundled examples such as bytes, text-with-encoding, CSV/table, JSON, and an
emoticon-to-emoji converter should register through the same plugin-style route
as external capabilities. Because the bundled implementations are local-path
backed, their descriptors declare a path locator facet; selection should reject
remote or otherwise non-path descriptors before runtime. A text-to-text
converter can be selected for a CSV-like artifact only when the caller requests
text input and text output; it should not satisfy requests for CSV/table output
claims.

## Handle Lifetime

Operations should own cleanup for handles they open. Internally, this should
look like an `ExitStack`: readers and converters open resources, writers and
materializing converters consume them, and handles close in reverse order when
the operation completes or fails.

The model must not require eager data loading. Lazy handles are allowed, but
ownership has to be explicit:

- Operation-owned handles close when the operation scope exits.
- User-owned handles are returned through an explicit context-managed API and
  live until the user closes that context.
- A lazy object that depends on open files, remote sessions, or fsspec
  filesystems must not escape an operation accidentally.

## Permissions

Permissions belong in the virtual catalog model. They should be expressed as
actions over catalog, record, artifact, resource, and operation scopes. Initial
actions may include `read_metadata`, `read_artifact`, `write_metadata`,
`write_artifact`, `create_artifact`, `replicate_artifact`, `restore_artifact`,
`delete_artifact`, `admin_record`, and `admin_catalog`.

POSIX owner/group/world fields can inspire a local convenience model, but core
vocabulary should support named principals, groups, and ACL-like grants.

Permissions are advisory when users can bypass `ogcat` and read the underlying
path directly. They become enforceable only when access flows through `ogcat`,
a server, or a storage backend that enforces the policy.

## Locks And Isolation

The model should plan for locks and leases now.

Lock scopes:

- Catalog lock for migrations and administrative changes.
- Record lock for metadata updates, artifact membership changes, and leader
  selection.
- Artifact lock for replacement, cache refresh, replica state transitions, and
  destructive operations.
- Locator/resource lock for physical target paths, directories, cache
  locations, and staging areas.
- Handle lease for open read or write access.

Lock modes:

- Shared/read lease.
- Exclusive/write lease.
- Intent lock for planned writes before side effects begin.
- Expiring lease with heartbeat for crashed processes or abandoned workers.

The baseline isolation target is read-committed catalog state. Readers should
not observe uncommitted metadata or partially registered artifacts. Writers
should not overwrite another committed update without holding the appropriate
lock or detecting a version conflict.

Backend guarantees vary. TinyDB may only support global catalog locking,
optimistic version checks, or a sidecar lock registry. File locks are not a
portable answer on HPC/NFS systems. Strong multi-user isolation likely requires
a server application or backend lock coordinator.

For materializing writers, prefer write-to-staging, validate, then commit
catalog state and promote the locator. Partially written artifact state should
not become committed catalog state.

## Options Considered

### Option 1: Keep the current one-record, one-locator model

This is simple and works for local files, but it overloads `CatalogRecord` with
record identity, artifact identity, physical location, and access dispatch. It
does not naturally support previews, manifests, cache copies, archive copies,
replicas, mounted catalogs, or typed reader/converter pipelines.

This option is rejected as the long-term model.

### Option 2: Make ogcat a POSIX-like filesystem

This would maximize familiarity, but it is the wrong abstraction. Object stores,
remote services, SQL queries, archives, fsspec urlpaths, and scientific data
interfaces do not all support POSIX semantics. POSIX paths should remain a
useful access form, not the identity model.

This option is rejected.

### Option 3: Clone iRODS concepts directly

iRODS has already solved many data-management problems around resources,
replicas, policy, and federation. Directly adopting its full model would make
personal/local use too heavy and would push `ogcat` toward server-first
operation too early.

This option is rejected, but iRODS remains the main reference for resource,
replica, cache/archive, and policy concepts.

### Option 4: Virtual artifact filesystem with plugin capabilities

This option separates logical records from artifacts, separates artifacts from
locators, and separates persisted claims from runtime handles. It keeps local
use simple while leaving room for resources, replicas, mounts, permissions,
locks, and typed pipelines.

This ADR chooses Option 4.

## Consequences

Positive consequences:

- The domain model can describe multiple artifacts per record without changing
  `record_type` into an I/O dispatch key.
- Current `record.locator` workflows can remain easy while becoming a
  compatibility shortcut rather than the whole model.
- Collection support can grow without adding a fake storage target kind.
- Replication, caching, deep storage, and view links have distinct vocabulary.
- Plugins can provide scientific readers and writers without becoming core
  dependencies.
- Operation-owned handle cleanup gives composed xarray/fsspec workflows a
  clearer lifecycle.
- Permissions and locks are designed into the model before a server backend is
  introduced.

Negative consequences:

- The persistent schema becomes more complex.
- Implementation must preserve simple local-file ergonomics despite the richer
  model.
- Some concepts, such as resources, permissions, locks, and replicas, will have
  weaker local-backend guarantees until stronger backends exist.
- Plugin capability names, versions, and trust boundaries need careful design.

## Implementation Guidance

Do not implement the full model in one change. Split follow-up work as issue
slices from #108:

1. First-class artifact descriptor target model.
2. Claim and facet schemas for data type, representation, interface, and
   evidence/confidence.
3. Reader, writer, and converter capability registry.
4. Managed collections as operation targets, including #109.
5. Structured writer result model.
6. Read-side handles and accessors.
7. Intake plugin design spike.
8. Migration and compatibility for one-locator catalogs.
9. Virtual namespace and `ogcat://` resolver exploration.
10. Replica, link, cache, and archive-copy vocabulary.
11. Mount-relative locator and storage-resource model.
12. Operation-owned handle lifecycle and composed pipeline cleanup.
13. Permissions and access-control vocabulary.
14. Locking, leases, and basic isolation model.
15. Documentation simplification around one artifact operation model.

Early implementation should preserve existing APIs and add compatibility
bridges rather than forcing immediate catalog migration.

## Acceptance Scenarios

- Existing single-file artifacts still work through `record.locator`.
- A record with a physical data artifact exposes a path or urlpath usable by
  plain library calls when permissions and locator resolution allow it.
- A record can own a data artifact plus preview, log, and manifest artifacts.
- A symlink/template view is modeled as a `view_link`, not as an exact replica.
- A directory reference is not a collection unless explicitly claimed.
- Managed zip to NetCDF collection writes one logical collection artifact.
- One artifact can claim multiple interfaces, such as bytes, NetCDF, and xarray
  dataset.
- Readers dispatch by artifact claims/interfaces, not by `record_type`.
- Suffix-only detection is represented as inferred evidence, not validated
  truth.
- Optional Intake, xarray, and pandas integrations can be absent without
  breaking core catalog use.
- A cache copy on another HPC machine can be tracked separately from the leader
  and marked stale or unavailable.
- A deep-storage archive copy can require restore before normal readers can
  open it.
- A mount-relative locator can resolve to a local POSIX path on one machine and
  an fsspec/SSH urlpath on another without rewriting catalog identity.
- A composed xarray workflow closes operation-owned datasets and files after the
  materializing writer finishes.
- A lazy user-facing handle can be returned only with explicit ownership and
  cleanup semantics.
- Concurrent writers to the same record or artifact either serialize through a
  lock/lease or detect a version conflict before commit.

## Deferred Questions

- Should artifacts be persisted inline on `CatalogRecord`, in a separate
  artifact registry, or first prototyped under namespaced `derived_metadata`?
- Should `primary` remain a compatibility term, become a data-artifact role, or
  be replaced with a clearer term such as `data_artifact`?
- What is canonical for artifact identity: artifact ID, record ID plus role,
  locator, checksum, virtual path, or a combination?
- What exact persistent schema should represent mount-relative locators and
  storage resources?
- Which permissions are required for the first implementation, and which are
  reserved for a server backend?
- What lock and isolation guarantees can the TinyDB backend honestly make?
- What terminology should distinguish artifact-level collections from
  record-level catalog subsets or views?
- How should capability registration authenticate or trust plugins?
