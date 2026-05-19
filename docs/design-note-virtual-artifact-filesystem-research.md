# Virtual Artifact Filesystem Research Notes

These notes collect the research context for issue #108 before drafting the ADR.
They are intentionally descriptive rather than decisive: the ADR should make the
actual architecture decision after these concepts have been reviewed.

## Inputs Reviewed

- GitHub issue #108, "Plan filesystem-like artifact interfaces and structured operator pipelines".
- GitHub issue #109, "Make managed collections first-class operation targets".
- Current `ogcat` code around `CatalogRecord`, `ArtifactLocator`, `StoragePlan`,
  `OperationSource`, `ArtifactWriter`/materializer helpers, `OperationContext`, collections, replicas,
  secondary artifacts, and plugins.
- User-supplied notes:
  - `/Users/bm13805/Desktop/temp/chatgpt_unix_filesystem_review.md`
  - `/Users/bm13805/Desktop/temp/chatgpt_file_system_ideas_5_may_2026.md`
  - `/Users/bm13805/Downloads/ChatGPT-Intake_package_architecture.md`
  - `/Users/bm13805/Downloads/ChatGPT-Irods_Architecture_Overview.md`
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

`OperationSource`, `ArtifactWriter`/materializer helpers, and
`OperationContext` are the existing write-side boundary. Materializers write
data into a planned artifact target and can return descriptor facts or register
rollback actions.

Collections are not a storage target today. They are explicit classification
metadata layered over directory-like locators. This is a useful direction to
preserve: collection-ness should not become `target_kind="collection"`.

Replicas and secondary artifacts currently exist as local symlink-oriented views
or add-operation side effects. In iRODS terminology these are not replicas:
they are view links or secondary artifacts that point at the primary path. They
point toward a future model where records can own multiple artifact descriptors:
primary data, view links, previews, logs, manifests, exact copies, caches, and
derived outputs.

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

Extended mapping:

| Unix/Linux idea | ogcat analogue | Design pressure |
| --- | --- | --- |
| VFS | virtual artifact layer | Dispatch by locator kind, representation, interface claim, and plugin capability. |
| inode | future `Artifact` descriptor | Current `CatalogRecord` is doing both logical-record and artifact-object work. |
| dentry/pathname | record lookup, search row, generated view entry, future `ogcat://` path | Names are views over identities, not identities themselves. |
| open file description | runtime `Handle` | Holds reader/session/cache/options; never persisted. |
| file descriptor | operation-local handle token | Useful for future APIs; Python can return objects directly. |
| `stat`/`lstat`/`statx` | cheap artifact facts and facets | Existence, size, mtime, checksums, suffixes, capabilities, stale state. |
| mount table | storage profiles and namespace mounts | `relative_path` should mean "under this mounted storage profile". |
| `ioctl` | namespaced plugin options | Escape hatch only for typed, versioned plugin-specific controls. |

File-type analogy:

| Unix file type | ogcat analogue | Where the analogy fails |
| --- | --- | --- |
| Regular file | byte artifact, NetCDF file, CSV, archive file | Remote stores may lack atomic rename, stable mtimes, or local path semantics. |
| Directory | directory artifact, collection root, namespace view | Object-store prefixes are not true directories; listing may be partial or expensive. |
| Symbolic link | locator alias, template link, generated view link | Can dangle, loop, escape roots, or break when mounts move. |
| Hard link | multiple names for one artifact identity | Cross-device and remote semantics fail; prefer artifact identity or aliases. |
| Device file | service/store endpoint behind a driver | Reads can have side effects; auth, rate limits, and driver versions matter. |
| FIFO/pipe | operation-scoped stream between stages | Blocking, cancellation, partial consumption, and cleanup are runtime concerns. |
| Socket | remote API/session endpoint | Store endpoint description, not live connection state. |

The analogy is most useful when it reveals a missing layer. For example, if a
future artifact acts like a symlink, ogcat needs link resolution and dangling
target behavior. If it acts like a replica, ogcat needs copy state, checksums,
freshness, and reconciliation. If it acts like a mount, ogcat needs a resolver
context rather than a baked absolute path.

## iRODS And Replica Research Summary

iRODS separates logical namespace objects from physical storage resources.
Users operate on collections and data objects; the catalog tracks physical
replicas of data objects on resources. A replica is an independently
materialized physical instance of the same logical data object, normally with
state, resource, path, checksum, size, and freshness information.

Important iRODS ideas for ogcat:

- Logical identity and physical location are separate.
- A data object can have multiple physical replicas.
- Storage resources can be real storage backends or coordinating resources.
- Compound cache/archive resources model fast cache plus deep storage.
- Replication resources coordinate copies across children.
- Replica status matters. The supplied notes mention states such as good,
  intermediate, write-locked, and stale.
- Finalization is catalog-atomic, but physical writes across resources are not a
  global distributed transaction. Replication and tiering behave more like
  state machines with retry and reconciliation.

ogcat equivalents and gaps:

| iRODS concept | Current ogcat analogue | Gap |
| --- | --- | --- |
| Data object | `CatalogRecord` plus primary `ArtifactLocator` | No first-class artifact descriptor or multi-copy state. |
| Collection | `add_collection()` record with directory/prefix locator | No logical member catalog or collection namespace. |
| Replica | None yet for exact copies; symlink views are only links | Need checksum/freshness/resource/copy state. |
| Resource | Storage root plus adapter hint | No resource hierarchy, health, policy, or cache/archive coordinator. |
| Vault | `data/objects/` primary storage tree | Not necessarily archive storage and not a storage resource model. |
| Rule engine | hooks, materializers, secondary artifacts | Python-local, synchronous, and not a policy engine. |
| Federation | explicit remote locators only | No zones, trust, ACL sync, or remote catalog protocol. |

The ADR should avoid using "replica" for current symlink-based behavior without
qualification. Today these are better described as `template_link`,
`view_link`, or symlink view artifacts. Future true replicas should be exact
copies or exact logical manifests with durability and freshness semantics.

## Links, Replicas, Caches, And Deep Storage

Links and replicas solve different problems and should not share one model.

`view_link`
: A namespace convenience entry resolving to another locator or artifact. A
  symlink-style view link is cheap, can dangle, and is host/mount dependent. It
  does not prove that another physical copy exists.

`alias`
: A second logical name for the same artifact identity. This is closer to a
  hard-link analogy, but should be modeled by shared artifact identity rather
  than local filesystem hard links.

`replica`
: An independently materialized exact copy of artifact content, usually on a
  different resource. It needs a source artifact, target resource, checksum or
  manifest, copy state, freshness state, and reconciliation history.

`cache_copy`
: A replica-like copy managed for access speed rather than authority. It needs
  cache policy, eviction/refresh behavior, and a way to distinguish stale but
  usable copies from invalid copies.

`archive_copy`
: A durable copy in deep storage. It needs retention, restore, staging, and
  asynchronous transfer state. It may not be immediately openable as a normal
  artifact handle.

`derived_artifact`
: A transformed output. It may be reproducible from another artifact, but it is
  not a replica unless the invariant says it is byte-for-byte or
  manifest-for-manifest identical.

Possible copy states for future discussion:

- `available`: copy is present and believed current.
- `stale`: copy exists but no longer matches the current leader.
- `building`: copy is being created.
- `restoring`: deep-storage copy is being staged for local use.
- `missing`: catalog says a copy should exist, but it cannot be found.
- `unverified`: copy exists, but exactness has not been proven.
- `failed`: creation, verification, or refresh failed.

The "primary vs. secondary artifact" distinction should not carry replication
semantics. Replication should use a separate `leader` or `write_leader`
concept. Read routing should be separate again: the preferred read location may
be a local exact copy, a geographically or administratively accessible cache, or
the leader if no better copy exists.

## Mounts And Relative Locator Resolution

Mounts are central to carrying the filesystem analogy beyond local paths.
Current `ArtifactLocator.relative_path` should be preserved as a useful seed for
a higher-level resolver model.

The future model could store a locator as:

```text
Locator(
    kind="path" | "urlpath" | "uri" | "opaque" | "ogcat" | "query" | "service",
    value=<root-relative value or external endpoint>,
    mount_id=<optional storage/mount profile>,
    relative_path=<path under the mounted root>,
)
```

Under this model, the same artifact can resolve differently depending on where
the catalog is opened:

- On the original HPC server, `mount_id="hpc-a-objects"` plus
  `relative_path="objects/ab/cd/file.nc"` can resolve to a local POSIX path.
- On a laptop, the same mount can resolve to an `ssh://...` fsspec urlpath.
- In a workflow runner, it can resolve to an object-store urlpath or staged
  scratch directory.

This is not federation by itself. It is mount-relative locator resolution:
artifact identity remains catalog-local, while storage access is interpreted
through the current resolver context.

`relative_path` should become relative to a `mount_id` or storage-resource
profile. Today it is relative to a location inside ogcat-managed storage; the
target model should lift that idea into an explicit resolver layer.

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
  through declared readers, writer capabilities, converters, and operation
  materializers. Owns validation, rollback, audit, and provenance.

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

## Design Clarifications From Discussion

These points reflect follow-up decisions or strong preferences to carry into
the ADR.

- Do not overload `primary`. For replication, use `leader` or `write_leader`.
  For read routing, use a separate preferred-location policy that can choose a
  local copy, accessible HPC cache, geographically near copy, or leader.
- `Artifact` remains broad: a concrete or virtual thing associated with a
  record. The model still needs roles or subtypes to distinguish data-bearing
  artifacts from auxiliary artifacts such as previews, logs, manifests, view
  links, and derived outputs.
- A useful role vocabulary may include `data_artifact`, `auxiliary_artifact`,
  `view_link`, `manifest`, `preview`, `log`, `derived_artifact`, `replica`,
  `cache_copy`, and `archive_copy`. Application code or plugins may interpret
  which artifact is "the data" when a record owns several artifacts.
- If a physical file or urlpath exists, ogcat should expose a low-ceremony path
  or path-like value. Existing patterns such as `xr.open_dataset(record.locator.value)`
  should remain easy, and a future convenience property may be warranted. Users
  should not be worse off than they would be with ordinary filesystem paths.
- Current symlink "replicas" should be called `view_link` or symlink view
  artifacts, not replicas.
- Replica exactness should have reasonable core defaults, such as a file digest
  for single files or a manifest with member checksums for directories and
  archive-like collections. Plugins must be able to define stronger or
  domain-specific exactness rules.
- Metadata alone is a weak replica invariant, but source-provided identity and
  version metadata matters for remote sources. For example, a remote service may
  provide URIs or version identifiers that distinguish exact downloads while a
  plugin presents a stable logical data source.
- Replicas in ogcat are primarily about performance and access, not replacing
  backups or building a distributed database. The immediate problem is avoiding
  unmanaged duplication across HPC servers, local caches, and temporary working
  copies.
- Deep storage and archive copies should be modeled explicitly, but probably as
  storage-resource policy and artifact state rather than ordinary always-open
  replicas. A deep-storage copy may require a restore or staging operation
  before normal readers can access it.
- Caches, deep-storage copies, and HPC copies can be artifact descriptors; the
  decision to create them belongs to storage-resource policy, explicit
  operations, or plugins.
- `relative_path` should be relative to a `mount_id` or storage-resource
  profile, not implicitly tied to one local absolute root.
- Pipelines should not require eager data loading. Operation-owned handles
  should close when the operation scope ends unless ownership is explicitly
  returned or transferred to the user. User-owned context-managed handles can
  live as long as the user keeps their context open.
- Core should mainly provide interfaces, orchestration, durable metadata, and
  lifecycle semantics. Features that can be written as plugins should use the
  same registration path, even when ogcat vendors the most important plugins for
  basic use.
- Personal use must stay trivial: TinyDB plus local files on one machine should
  still provide search, storage organization, logging, provenance, and easy
  access to the underlying data path.
- iRODS should be the main reference for data-management concepts; Intake Take2
  should be the main reference for typed reader/converter/writer pipeline
  concepts, without making Intake a core dependency.
- "Collection" has two plausible meanings and the ADR should reserve terms for
  both: an artifact-level collection such as a directory/prefix/archive of data
  members, and a record-level collection such as a table, subset, or view of
  `CatalogRecord` objects, possibly across mounted catalogs.

## Collections

Collections should be modeled as capabilities layered over storage shape.

This section is about artifact-level collections: one artifact whose members
form a logical dataset. That is distinct from a record-level collection, which
would be a catalog subset or table of `CatalogRecord` objects. Record-level
collections are not a core target for the first ADR, but the terminology should
leave room for them because they fit naturally with catalog views and mounts.

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

## Pipes, Filters, And Handle Lifetimes

The target architecture can make a pipes-and-filters model explicit without
reducing every operation to bytes. The pipe is a typed runtime interface; the
filter is a reader, converter, writer, or domain operation.

Example typed pipeline:

```text
zip artifact
  -> Reader[archive-members]
  -> Writer[managed directory + collection facets]
  -> Reader[xarray.Dataset]
  -> Converter[domain boundary-condition dataset]
  -> Writer[Zarr store artifact]
```

Important lifecycle rule: handles are runtime objects, not metadata. If a
reader opens files, remote sessions, xarray datasets, fsspec filesystems,
temporary caches, or streams, the operation envelope should own their cleanup.
An `ExitStack`-like scope lets users write a simple context manager for a
single operation while composed operations close resources automatically.

Recommended operation lifecycle:

1. Resolve input artifact descriptors and locator context.
2. Validate declared claims, options, and plugin availability.
3. Plan output artifact targets and rollback actions.
4. Open reader handles inside an operation-owned context stack.
5. Run converters/writers while handles are still open.
6. Persist artifact descriptors, facets, and provenance.
7. Close handles in reverse order.
8. Run rollback actions if any stage fails.

Lazy handles can be useful, especially for xarray and fsspec, but they should
not silently escape a closed operation scope. The ADR should decide whether a
pipeline can return a user-owned context-managed handle, whether operations must
materialize outputs before closing, or whether both modes are allowed.

## Permissions And Access Control

Permissions fit naturally into the filesystem analogy, but ogcat should treat
them as virtual catalog policy rather than a promise that the backing filesystem
or object store enforces the same rules. POSIX mode bits and ACLs are useful
references, not a complete model.

Permission checks may need several scopes:

- Catalog-level permissions: who can open, search, administer, or mount a
  catalog.
- Record-level permissions: who can read metadata, edit metadata, delete a
  record, or create linked/derived records.
- Artifact-level permissions: who can read bytes, open structured interfaces,
  materialize new copies, replace leader content, create view links, or restore
  deep-storage copies.
- Resource-level permissions: who can write to a storage resource, stage from
  deep storage, create an HPC cache copy, or use a credential profile.
- Operation-level permissions: who can run a reader/writer/converter and with
  which plugin options.

The core vocabulary can start with capability-style permissions such as
`read_metadata`, `read_artifact`, `write_metadata`, `write_artifact`,
`create_artifact`, `replicate_artifact`, `restore_artifact`, `delete_artifact`,
`admin_record`, and `admin_catalog`. POSIX-like owner/group/world fields may be
a convenient local backend, but named principals and groups are more flexible
for future server-backed catalogs.

Important limitation: if users can bypass ogcat and read the underlying path
directly, catalog permissions are advisory for that access path. That is still
useful for personal/local use, provenance, UI/API behavior, and server-backed
deployments, but it is not filesystem-level enforcement unless ogcat or the
storage backend controls the access path.

For simple personal use, default permissions should stay permissive and
low-ceremony. Permission machinery should not make TinyDB plus local files feel
heavier than normal filesystem use.

## Locks, Isolation, And Handle Analogy

Locks also fit the filesystem analogy, especially if runtime `Handle` objects
are treated like file-descriptor analogues. The design should plan for locks
even if early backends can only provide coarse or advisory locking.

Useful lock scopes:

- Catalog lock: protects catalog-wide migrations or administrative changes.
- Record lock: protects metadata updates, artifact membership changes, and
  leader selection for a `CatalogRecord`.
- Artifact lock: protects writes, replacement, cache refresh, replica state
  transitions, and destructive artifact operations.
- Locator/resource lock: protects a physical target path, directory, cache
  location, or deep-storage staging area.
- Handle lock or lease: ties a read or write claim to an opened runtime handle
  or operation context.

Useful lock modes:

- Shared/read lock: many readers can inspect or open stable content.
- Exclusive/write lock: one writer can update record state or materialize a
  target.
- Intent lock: an operation declares it plans to write a record, artifact, or
  resource before it opens handles or performs side effects.
- Lease with expiry/heartbeat: a lock token can be reclaimed after a crashed
  process or worker.

Basic isolation target: read-committed catalog state. Readers should not observe
uncommitted metadata or partially registered artifacts. Writers should not
overwrite another committed update without detecting a version conflict or
holding the appropriate lock. Artifact bytes may still require staging and
commit/finalize semantics because physical writes are not necessarily atomic.

Backend caveats:

- TinyDB cannot provide true per-record isolation by itself. A local TinyDB
  backend may only support a global catalog file lock, optimistic version
  checks, or a sidecar lock registry.
- File locks are not a portable answer on HPC/NFS environments. They may be
  unavailable, advisory, or unreliable across machines.
- True multi-user concurrency likely needs a server application or backend that
  can coordinate lock tokens, transactions, leases, and conflict detection.
- Locks only protect writes that go through ogcat. External mutation of a path
  remains outside the catalog's control unless the storage resource enforces it.

The handle analogy suggests an API shape: opening an artifact for read can
acquire a shared lease; opening for write or replacement can acquire an
exclusive lease; the operation or user-owned context releases the lease when the
handle closes. For materializing writers, a safer pattern is write-to-staging,
validate, then commit catalog state and promote the locator. That keeps
partially written artifacts out of the committed catalog view.

## Domain Model Layers

Possible layer boundaries:

1. Catalog persistence: records, artifact descriptors, metadata namespaces,
   operation journal, and audit log.
2. Namespace and mount resolution: record lookup, generated views, `ogcat://`
   paths, mount table, and storage-profile resolution.
3. Locator/resource layer: path/urlpath/uri/opaque/query/service locators,
   resource health, credentials profile names, and storage capabilities.
4. Artifact facts layer: claims, facets, cheap inspection, checksums,
   manifests, stale evidence, and validation results.
5. Capability layer: reader, writer, converter, locator driver, and storage
   adapter registries.
6. Access policy layer: permissions, principals, groups, resource credentials,
   and operation authorization.
7. Concurrency layer: lock tokens, leases, version checks, isolation policy,
   and conflict detection.
8. Runtime handle layer: opened datasets, streams, fsspec files, service
   sessions, temporary caches, and cleanup scopes.
9. Operation layer: typed pipelines, rollback, provenance, materialization,
   validation, and reconciliation.
10. Presentation layer: `Catalog`, CLI, documentation examples, query views, and
   optional Intake adapters.

The layer split should keep core dependency-light. Core can know that an
artifact claims `xarray.Dataset` as an interface, but it should not import
xarray unless a plugin provides the reader.

## Class, Role, Collaborator Sketch

| Class/concept | Role | Main collaborators |
| --- | --- | --- |
| `CatalogRecord` | Logical catalog entry for search, schema, lifecycle, and provenance. | Owns `Artifact` descriptors; linked to operations and metadata schemas. |
| `Artifact` | Persistent descriptor for one concrete or virtual object owned by a record. | Has locators, roles, copy/link relationships, claims, facets, and state. |
| `Locator` | Serializable description of where an artifact is or can be resolved. | Resolved by mount/resource context and locator drivers. |
| `StorageResource` | Named storage profile, root, adapter, and capabilities. | Used by locators, storage plans, replicas, caches, and mount resolver. |
| `DataTypeClaim` | Intake-aligned source/external type assertion. | Informs readers, converters, validators, and documentation. |
| `Representation` | Storage/encoding shape of an artifact. | Connects locators to readers and writer capabilities. |
| `InterfaceClaim` | Capability contract an artifact can expose. | Dispatch key for reader/converter selection. |
| `Facet` | Namespaced fact with evidence and confidence. | Produced by inspectors, readers, materializers, validators, and plugins. |
| `PermissionPolicy` | Decides whether a principal can perform a catalog, record, artifact, resource, or operation action. | Uses principals, groups, record/artifact state, resource profiles, and plugin capability metadata. |
| `LockManager` | Coordinates shared/exclusive locks, leases, and version checks. | Used by operations, handles, storage resources, and catalog backends. |
| `Reader` | Opens an artifact through an interface and returns a runtime handle/object. | Uses locators, claims, facets, plugins, and operation context. |
| `WriterCapability` | Declares and implements typed output behavior. | Uses descriptors, runtime values/handles, claims, facets, and plugin options. |
| `OperationMaterializer` | Adapts a writer capability or one-off function into the catalog operation lifecycle. | Uses storage plans/resources, rollback registration, audit, and descriptor-result merge. |
| `Converter` | Transforms one runtime interface into another. | Composes readers and writer capabilities; may be lazy or materializing. |
| `Handle` | Runtime opened object scoped to an operation or user context. | Owned by operation lifecycle or user context; may hold read/write leases. |
| `Operation` | Execution envelope for typed pipelines. | Coordinates readers, writer capabilities, converters, materializers, rollback, audit, and provenance. |

UML-style structural sketch:

```text
classDiagram
  class CatalogRecord {
    record_id
    record_type
    user_metadata
    derived_metadata
    lifecycle_state
  }
  class Artifact {
    artifact_id
    role
    relation
    state
    primary_compatibility
  }
  class Locator {
    kind
    value
    mount_id
    relative_path
  }
  class Claim {
    kind
    name
    evidence
    confidence
  }
  class Facet {
    namespace
    data
    evidence
    confidence
  }
  class StorageResource {
    resource_id
    adapter
    root
    capabilities
  }
  class Operation {
    operation_id
    inputs
    outputs
    provenance
  }
  class PermissionPolicy {
    authorize()
  }
  class LockManager {
    acquire()
    release()
    heartbeat()
  }
  class Handle {
    interface_name
    reader_name
    lock_token
    runtime_state
  }

  CatalogRecord "1" o-- "*" Artifact
  Artifact "1" o-- "*" Locator
  Artifact "1" o-- "*" Claim
  Artifact "1" o-- "*" Facet
  Locator --> StorageResource
  Operation --> "*" Artifact
  Operation --> PermissionPolicy
  Operation --> LockManager
  Handle --> Artifact
  Handle --> LockManager
```

Interface sketch for ADR discussion:

```python
class LocatorDriver(Protocol):
    def stat(self, locator: Locator, *, follow: bool = True) -> ArtifactStat: ...
    def list(self, locator: Locator) -> Iterable[DirectoryEntry]: ...
    def open_bytes(self, locator: Locator) -> ContextManager[BinaryIO]: ...


class Reader(Protocol):
    name: str
    input_interfaces: Sequence[str]
    output_interface: str

    def open(
        self,
        artifact: Artifact,
        *,
        context: OperationContext,
        options: Mapping[str, object],
    ) -> ContextManager[object]: ...


class Writer(Protocol):
    name: str
    input_interfaces: Sequence[str]
    output_representation: str

    def write(
        self,
        source: object,
        target: ArtifactTarget,
        *,
        context: OperationContext,
    ) -> ArtifactWriteResult: ...


class Converter(Protocol):
    name: str
    input_interface: str
    output_interface: str

    def convert(
        self,
        source: object,
        *,
        context: OperationContext,
        options: Mapping[str, object],
    ) -> ContextManager[object] | object: ...
```

## Data Flow Sketches

Single-file ingest as the future model:

```text
flowchart LR
  A["Local source path"] --> B["copy/move materializer"]
  B --> C["Primary artifact descriptor"]
  C --> D["record.locator compatibility field"]
  B --> E["facts: size, suffix, checksum, inferred claims"]
  E --> F["CatalogRecord"]
```

Managed zip-to-collection flow:

```text
flowchart LR
  A["Zip artifact"] --> B["archive reader"]
  B --> C["collection writer"]
  C --> D["Managed directory or prefix artifact"]
  D --> E["collection interface claim"]
  D --> F["manifest/pattern facets"]
  E --> G["xarray reader plugin"]
```

Remote/deep-storage cache flow:

```text
flowchart LR
  A["Archive/deep storage locator"] --> B["restore or cache operation"]
  B --> C["Local cache_copy artifact"]
  C --> D["available or stale state"]
  C --> E["reader dispatch"]
  A --> F["archive_copy state and checksum"]
```

Mount-relative resolution:

```text
flowchart LR
  A["Artifact locator: mount_id + relative_path"] --> B["Resolver context"]
  B --> C["local POSIX path on HPC"]
  B --> D["ssh:// urlpath on laptop"]
  B --> E["object-store urlpath in workflow runner"]
```

## Core Versus Extras

Likely core responsibilities:

- Catalog records, artifact descriptors, locator schema, and compatibility
  `record.locator`.
- Claim/facet metadata structures, evidence/confidence vocabulary, and cheap
  inspection hooks.
- Storage plans, resources or mount profiles, and path/urlpath/uri/opaque
  locator basics.
- Operation lifecycle, rollback, audit, provenance stubs, and handle cleanup.
- Minimal bytes, text, directory listing, archive-member, and manifest
  interfaces when dependency-light implementations exist.

Likely plugin or optional-extra responsibilities:

- NetCDF, HDF5, Zarr, xarray, pandas, SQL, Intake, and OpenGHG-specific readers.
- CAMS archive extraction and boundary-condition transforms.
- fsspec caching policies beyond simple urlpath support.
- HPC staging, deep-storage restore, async replication, and reconciliation
  policies.
- iRODS/DataLad/S3/GCS/Azure adapters and any federation protocol.
- Domain validators that require scientific dependencies or domain credentials.

The core should be able to list, search, inspect, and preserve metadata for an
artifact whose reader plugin is absent. Opening through that missing interface
should fail clearly with a plugin/capability error, not corrupt the catalog.

## API Simplification Pressure Points

The current CAMS artifact workflow is useful because it exposes the missing
abstractions:

- The user manually plans directory storage for a managed collection.
- The archive extraction writer manually returns collection classification
  metadata.
- The xarray step manually opens the collection with a context manager.
- The processed output manually carries source locator and reader-hint metadata.

In the target model, `add_file()` can be understood as a convenience operation:
"create a primary artifact by copying/moving a local source through a default
writer, infer cheap claims/facets, and maintain `record.locator`
compatibility." It should not be a separate conceptual pathway.

`add_reference()` can be understood as: "create an artifact descriptor with an
external locator and declared or inferred claims; do not take storage ownership."

`add_collection()` can be understood as: "create a directory/prefix/archive
artifact with explicit collection/interface claims." It should support both
reference collections and managed collection writes, but collection-ness should
remain a claim/facet, not a storage target kind.

`add_artifact()` can remain the low-level escape hatch, while higher-level APIs
route through the same operation model. Documentation can then teach one mental
model instead of separate locator-management recipes.

## Likely Issue Sequence

These are planned issue slices. They should remain checklist items on #108 until
the ADR makes each slice concrete enough to open as a separate sub-issue.

1. ADR: virtual artifact filesystem vocabulary and boundaries.
2. First-class artifact descriptor target model.
3. Data type, representation, interface, and facet claim schemas.
4. Reader, writer-capability, and converter capability registry.
5. Managed collections as operation targets, including #109.
6. Structured writer-capability result model and operation merge.
7. Read-side artifact handles and accessors.
8. Intake plugin design spike.
9. Migration and compatibility plan for existing single-locator catalogs.
10. Virtual namespace and `ogcat://` resolver exploration.
11. Replica, link, cache, and archive-copy vocabulary.
12. Mount-relative locator and storage-resource model.
13. Operation-owned handle lifecycle and composed pipeline cleanup.
14. Documentation simplification for `add_file`, `add_reference`,
    `add_collection`, and `add_artifact`.
15. Permission and access-control vocabulary.
16. Locking, leases, and basic isolation model.

## ADR Questions To Resolve

- Should the first persistent step be inline `CatalogRecord.artifacts`, a
  separate artifact registry, or prototype metadata under `derived_metadata`?
- What is canonical for an artifact: record ID, artifact ID, locator, checksum,
  virtual path, or a combination?
- Should `primary` remain only a compatibility concept, become a data-artifact
  role, or be replaced with a clearer term such as `data_artifact`?
- How should ogcat expose the simplest usable path or urlpath for records that
  point to physical data, while still supporting plugins and multi-artifact
  records?
- What invariant defines an exact replica by default: byte checksum, directory
  manifest, member checksums, size, or a type-specific validator?
- Are caches, deep-storage copies, and HPC copies modeled as artifact roles,
  storage-resource policies, operation outputs, or separate records linked by
  provenance?
- Should replica/cache/deep-storage work be synchronous in the add operation,
  asynchronous through a durable journal, or user-triggered reconciliation?
- What is the minimum storage resource model: named root, adapter, vault path
  template, credentials profile, health, and capabilities?
- Is federation in scope, or should ogcat only interoperate with iRODS, DataLad,
  S3, and similar systems through explicit locators and plugins?
- Should `DataTypeClaim` and `InterfaceClaim` be separate persisted structures,
  or should one claim type carry both source form and access capability?
- Which core storage shapes are required: `file`, `directory`, `prefix`,
  `archive`, `stream`, `service`, `inline`, or another set?
- How much reader/writer/converter recipe information is safe to persist in core
  metadata?
- Which confidence values are required: declared, inferred, probed, validated,
  stale, failed?
- How should plugin capability names be versioned and namespaced?
- What belongs in core versus optional plugins for bytes/text/directory/archive,
  NetCDF, Zarr, xarray, pandas, Intake, and OpenGHG-specific data?
- Should artifact-level collection descriptions require member manifests,
  pattern facets, or both?
- What terminology should distinguish artifact-level collections from
  record-level catalog subsets/views?
- When should pipelines stream through handles versus materialize intermediate
  artifacts?
- How should handle ownership transfer work when a pipeline returns a lazy
  user-owned handle?
- Which permission scopes and actions are core: catalog, record, artifact,
  resource, operation, reader, writer, converter?
- Are POSIX-like owner/group/world fields enough for local use, or should the
  core vocabulary start directly with principals, groups, and ACL-like grants?
- What isolation level should ogcat promise for each backend: best-effort,
  read-committed, optimistic conflict detection, or server-coordinated locks?
- Should handles acquire shared/exclusive leases by default, or should locks be
  explicit operation options?
- How are stale lock tokens detected and recovered after crashed local
  processes, failed jobs, or abandoned HPC workers?
- What driver error taxonomy is needed for unsupported, missing, stale,
  permission-denied, transient, and partial-write failures?
- How should operation provenance be represented before a full provenance graph
  exists?
- Which migration path is acceptable for existing catalogs with one primary
  `record.locator`?

## Acceptance Scenarios For ADR

- Existing single-file artifact still works through `record.locator`.
- A record can own primary data plus preview/log/manifest artifacts.
- A record with a physical data artifact exposes a path or urlpath usable by
  plain library calls when permissions and locator resolution allow it.
- A directory reference is not a collection unless explicitly claimed.
- Managed zip to NetCDF collection writes one logical collection artifact.
- One artifact can claim multiple interfaces, for example bytes plus NetCDF plus
  xarray dataset.
- Readers dispatch by claims/interfaces, not by `record_type`.
- Suffix-only detection is represented as inferred evidence, not validated truth.
- Optional Intake, xarray, and pandas integrations can be absent without breaking
  core catalog use.
- A symlink/template view is modeled as a link artifact, not an exact replica.
- A cache copy on another HPC machine can be tracked separately from the
  leader artifact and marked stale or unavailable.
- A deep-storage archive copy can require restore before normal readers can open
  it.
- A mount-relative locator can resolve to a local POSIX path on one machine and
  an fsspec/SSH urlpath on another without rewriting catalog identity.
- A composed xarray workflow closes datasets/files automatically after the
  materializing writer finishes.
- A lazy user-facing handle can be returned only with explicit ownership and
  cleanup semantics.
- Artifact-level collections and record-level catalog subsets are named
  distinctly enough that future mounted catalog views do not collide with
  directory/prefix/archive collection artifacts.
- Catalog, record, artifact, resource, and operation permission checks can be
  modeled even when a local backend treats them as advisory policy.
- A writer cannot publish partially materialized artifact state as committed
  catalog state.
- Concurrent writers to the same record or artifact either serialize through a
  lock/lease or detect a version conflict before commit.
