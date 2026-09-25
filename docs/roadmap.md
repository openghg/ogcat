# Roadmap

Planning snapshot from `ogcat` at `3391150` and a read-only review of local
Verification Games and BP1 catalogs on 2026-09-25. This page separates existing
behavior from proposed work. The [long-term plan](ogcat_long_term_plan.md) and
[virtual artifact filesystem ADR](adr/0002-virtual-artifact-filesystem-domain-model.md)
preserve broader design ideas; their issue lists are not the implementation order.

## Observed Workflows

| Workflow | Evidence | What ogcat must make easy |
| --- | --- | --- |
| Find scientific inputs | Verification Games searches flux and observation metadata, then passes stored paths to xarray. Some current SLURM scripts instead pin literal UUID object paths. | Resolve a record ID or a unique metadata query to a path, then pin the choice in a run manifest. |
| Find footprint series and months | BP1's `/group/chem/acrg/fp_name_catalog` has 229 collection references covering 14,821 existing NAME files. Its earlier backup has 14,325 individual file references. The [footprint importer](../examples/catalog_acrg_name_footprints.py) stores roots and filename patterns. Verification Games selects a series, then passes its full glob to xarray before slicing time. One MHD series has 144 monthly files but only 12 for a 2021 request. | Rerunnable import, unambiguous series selection, and selection of relevant local member files before opening them. Keep the source tree. |
| Register finished outputs | BP1's `result_outputs_catalog` had 1,232 reference records and was updated on 2026-09-25. Verification Games creates a result bundle, runs computation outside ogcat, then registers completed files. The [verification games recipe](tutorials/verification-games-recipes.md) describes the same boundary. | A short, rerunnable registration step after validation, with one catalog writer. |
| Manage selected files | BP1's `games_catalog` has 226 records, including UUID-primary and template-primary managed files. `Catalog.add_file()` can copy or move files and create a readable template symlink beside a UUID-primary artifact. | Keep managed ownership, delete/restore/purge, and the readable view. Show both canonical and readable paths in the CLI. |

The BP1 counts are observations from those files on the review date, not tests or
promises about future contents. BP1's `/group/chem/acrg` is GPFS; the separate
main-server NFS concern requires its own storage tests.

## Available Today

`ogcat` provides managed file and directory-store ingest, local and URI
references, collection reference records, record schemas, metadata validation,
search, generated views, audit events, and delete/restore/purge. Records can hold
artifact descriptors with claims and facets. A capability registry and bundled
reader/writer examples exist, but `Catalog` does not dispatch reads through them.

`Catalog.add_artifacts()` commits items one by one. `Catalog.add_collection()`
records a collection root and classification metadata; it does not enumerate or
append members. The importer calculates member counts and first/last months,
but does not persist its parsed member list. Those summaries do not guarantee
continuous coverage. `Catalog.add_reference()` does not verify that a local
target exists. Applications must validate completed output before registration.

The CLI exposes managed `add`, search, record inspection, path output, and
delete/restore/purge. It does not expose `add_reference()` or
`add_collection()`. `ogcat add` prints the primary path but not the readable
template link.

## Next Work, In Order

### 1. State And Enforce The Single-Writer Contract

Treat one process at a time as the catalog mutation contract. HPC workers write
outputs and completion manifests; one registrar validates and registers them.
The current result-output scripts can each open the same TinyDB catalog, so
sequential timestamps are not evidence of serialization. Add a regression test
that makes simultaneous mutation fail clearly or route through one writer.

Keep TinyDB for zero-setup single-user use while evaluating storage robustness.
Its JSON storage writes in place, and the current `UnitOfWork` holds in-memory
compensating actions rather than a durable transaction. Define recovery for an
interrupted write, a managed object without a record, and a record without its
readable link. Protect `catalog.json` updates with replacement and recovery.
Do not select SQLite from the assumption that local-disk guarantees carry over
to NFS or GPFS; test the actual deployment filesystem first.

Add a genuinely read-only `Catalog.open()` mode: TinyDB supports read access,
but the current repository opens it for writing and creates directories. An
existing Verification Games audit hit `EROFS` while trying to inspect a shared
catalog. Read-only opening must not create an audit log or permit writes.

### 2. Make Finished-Output Registration A Short Operation

Support the two existing ownership choices directly:

- `add_reference(path)` records a completed external path without taking
  ownership. The caller verifies existence, completeness, and readability.
- `add_file(path, operation="move")` moves a completed file or directory store
  into managed storage. Allow caller-supplied derived metadata so callers do
  not construct a `StoragePlan` merely to preserve validation results.

Evaluate one narrow adoption operation for a completed path already published
under the catalog's managed root. It would assert containment and completion,
take explicit ownership without moving bytes, and register the record in a
short operation. This is distinct from `add_reference()`: purge may remove an
adopted object. Current Verification Games Zarr helpers move a directory
first, then synthesize a `StoragePlan` and naming metadata to register it;
failure between those steps leaves an unregistered object.

Use a stable run/attempt/output identity for rerunnable registration. Specify
`skip`, `update`, and `error` behavior for an existing identity and a way to
reconcile partial multi-output runs. Leave checksum policy to the caller or a
small optional helper; checksumming a large directory store should not be
mandatory. Do not call the current `add_artifacts()` atomic batch ingest.

### 3. Complete The Common CLI And Lookup API

Expose proposed `add-reference` and `add-collection` commands corresponding to
the existing Python methods. Support metadata, record type, and JSON output.
Keep local-path verification and explicit URI/urlpath behavior distinct. Show
the readable template link in human `add` and `show` output; preserve
`record.path()` as the canonical path. Provide raw locator output for URI
records, which `search --paths` currently skips.

Promote the existing strict `Catalog.get_one()` lookup and expose its
zero-or-multiple-match errors in a CLI lookup. Do not make a job silently choose
`results.ids[0]` or a moving `latest` record. On BP1, an MHD/EUROPE/co2 search
can return both UKV and UMG footprint collections; a notebook selects the first
result. A reproducible run should resolve and record an exact record ID,
locator, and relevant input version before launch. For local
collection references, add a small member-path operation that expands the
stored relative pattern and returns sorted current paths. A NAME-specific
adapter can parse the existing `YYYYMM` filename suffix and select months
overlapping a requested range **before** xarray opens files. Report no matching
collection, ambiguous collections, missing roots, absent requested months, and
duplicate month files explicitly. Include boundary months and leave exact
within-file time slicing to xarray or OpenGHG. A stored `month_start`/`month_end`
range alone cannot prove which months exist. URI-only collections need a
reachable mount or a separate listing adapter; local selection must not pretend
to enumerate them. Make the footprint importer rerunnable instead of appending
duplicate series on every run. A proposed `Catalog.member_paths(record_id)` and
`ogcat members ID` should return paths, leaving NAME month parsing in its
small domain adapter. Use the collection classification pattern as the single
source: the current importer also copies it into user metadata, and different
consumers read different copies.

### 4. Simplify The Add Path And Contain Hooks

Preserve public add methods, hook names, UUID objects, template links, and
deletion behavior. Internally use one normalized add request, one authoritative
`StoragePlan`, an optional writer, and an optional view-link action. Remove the
mutable cached plan in `CatalogApplication.add_file()` and the conversions
through materialization intent/target/plan. Share primary planning with
`plan_artifact_storage()`; a plan remains a proposal and must be rechecked when
used.

Callers should normally compute metadata and validate their output before
calling an add method. Keep hooks as a compatibility extension. Revalidate
metadata after the final mutating hook, reject late changes to naming inputs
after writing, and check the final target at write time. Replace tests that
assert runner delegation with tests for observable results, hook compatibility,
rollback, ownership, and interrupted-operation repair.

## Defer Until A Real Workflow Requires Them

- Writer results (#117): add a structured result only when a real writer
  produces a locator or facts unavailable from its plan and caller metadata.
- Catalog read handles (#118): add a context-managed local reader only when
  callers need resource ownership beyond `record.path()` and ordinary library
  calls. A public write handle needs a durable publish and replacement contract.
- Managed collection append, typed pipelines, converter routing, mount-relative
  locators, replica/cache/deep-store state machines, ACLs, and leases: retain
  these as research, with a named user workflow and failure that simpler
  registration and lookup cannot handle before implementation.

The earlier [storage and ownership safeguards](architecture.md) remain part of
the current baseline: rendered paths reject obvious escapes, purge only treats
known managed write modes as owned, and creating a catalog rejects an existing
`catalog.json`. A concurrent filesystem change after path planning still needs
write-time protection.
