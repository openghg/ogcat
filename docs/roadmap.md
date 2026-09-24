# Roadmap

Planning snapshot updated from a review of `main` at `7eef31a` on 2026-09-24. This page
distinguishes available behavior from proposed work. The [long-term plan](ogcat_long_term_plan.md)
and [design notes](adr/index.md) record broader design history; their issue lists are not a
current implementation checklist.

## Available Today

`ogcat` has managed file ingest, local and URI references, collection reference
records, named record schemas, lightweight metadata validation, search, generated
views, audit events, and record delete/restore/purge operations. Records can hold
artifact descriptors with claims and facets. A capability registry and small
standard-library reader, writer, and converter examples exist, but `Catalog`
does not yet provide an integrated artifact read API.

`Catalog.add_artifacts()` processes items one at a time and keeps earlier
commits if a later item fails. `Catalog.add_collection()` records an existing
collection locator and classification metadata; it does not append members to
a managed collection or attach collection capability claims automatically.

## Recent Correctness Fixes

The review led to three safeguards for existing catalogs and files:

1. Rendered storage paths reject `.` and `..` segments and paths that resolve
   outside the selected managed root. Tests cover primary storage and template
   replicas. Concurrent filesystem changes after planning still need separate
   write-time protection if catalogs must resist hostile local processes.
2. Purge removes artifacts only when the record has a known managed write mode.
   Record-only references, including older records without a storage mode, do
   not grant ogcat ownership of a pre-existing file.
3. `Catalog.create()` and `ogcat init` reject an existing `catalog.json`,
   preserving its specification and records. Spec migration remains separate.

## Near-Term Feature Slices

### Persist Writer Results (#117)

Writers can materialize data, but the current writer protocol returns `None`;
produced claims and facets have no standard merge path into the persisted
artifact descriptor. Define a small structured result while preserving
existing `None`-returning writers. Specify how returned locators, claims,
facets, and derived metadata combine with a storage plan and hook changes.
Reject conflicting or invalid results before commit, and test rollback and
audit behavior after a writer failure.

### Open Artifacts Through a Requested Interface (#118)

Use the existing capability registry for an explicit, narrow catalog read
operation. First decide how `Catalog.create()` and `Catalog.open()` retain or
receive plugin capabilities; today they retain hooks but not the supplied
registry's capabilities. Select a data artifact and a caller-requested
interface, report missing or ambiguous matches clearly, and define
context-managed lifetime and cleanup for opened handles. Start with local
path-backed readers. Keep URI and unsupported locator behavior explicit.

Existing `add_collection()` records have classification metadata but no
collection claims/facets. Either add a documented compatibility bridge or
leave them outside collection-reader dispatch until those facts are persisted.
An end-to-end managed write/read example also depends on the writer-result
slice above. The [Unix-inspired model](adr/0002-virtual-artifact-filesystem-domain-model.md)
keeps opened handles runtime-only and gives them an explicit owner. Writes can
continue through `ArtifactWriter` and structured results; a public write
handle needs staging, replacement, and lease semantics before it is specified.

### Import Existing Data In Batches

A scan/import workflow would help users catalog existing directories without
copying each file manually. Start with sequential reference creation through
the existing add API. Before adding a CLI command, define source identity and
duplicate policy, deterministic dry-run output, and what happens when one item
fails or a run resumes. Do not describe the current `add_artifacts()` method
as atomic batch ingest.

### Update Managed Collections

Appending members to one managed collection needs more than the current
create-only writer contract. After writer results are defined, choose one
authoritative home for the member manifest: a bounded descriptor facet or a
separate manifest artifact. Define member identity, metadata recomputation,
rollback of newly written members, and a concurrency contract spanning the
read, write, and record update. A lock around individual TinyDB calls does not
cover that whole operation. See the [collection update ideas](ideas.md).

## Later Or Optional

Review add-operation orchestration when the writer-result contract is defined.
`CatalogApplication.add_file()` currently coordinates closures and a mutable
planning cache, while several materialization types forward into one
`StoragePlan`. A focused refactor could make that plan the authoritative
target and write policy, with an optional writer beside it. Preserve hook
order, hook-replaced locators, audit phases, and rollback behavior.

Grouped search views may help monthly file series, but need defined grouping,
ordering, and missing-key behavior before an API is chosen. External manager
bindings, remote storage policy, pipelines, and new backends should be driven
by concrete workflows while keeping core storage and search independent of
domain-specific logic.
