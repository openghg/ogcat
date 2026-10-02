# Roadmap

Planning snapshot from a review of local Verification Games and BP1 catalogs on
2026-09-25, updated as the first implementation lands. This page separates existing
behavior from proposed work. The [implementation record](plans/2026-09-25-single-user-workflows.md)
tracks decisions and delivered changes. The [archived long-term plan](plans/archive/ogcat_long_term_plan.md)
and [virtual artifact filesystem ADR](adr/0002-virtual-artifact-filesystem-domain-model.md)
preserve earlier ideas; their issue lists are not the implementation order.

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
records a collection root and classification metadata; `Catalog.member_paths()`
enumerates current local glob matches, without storing or appending members.
The importer calculates member counts and first/last months,
but does not persist its parsed member list. Those summaries do not guarantee
continuous coverage. `Catalog.add_reference()` does not verify that a local
target exists. Applications must validate completed output before registration.

The CLI exposes managed `add`, reference and collection registration, strict
single-result search, member enumeration, locator output, readable template
links, and delete/restore/purge. `Catalog.open(read_only=True)` supports
read-only inspection; reopen a reader after an external write. Use
`with Catalog.open(...) as catalog:` or `catalog.close()` to release its
database handle. The CLI closes catalogs at command exit. A managed
`add_file()` may carry caller-supplied derived metadata.

## Next Work, In Order

### 1. Enforce And Recover The Single-Writer Contract

Treat one process at a time as the catalog mutation contract. HPC workers write
outputs and completion manifests; one registrar validates and registers them.
The current result-output scripts can each open the same TinyDB catalog, so
sequential timestamps are not evidence of serialization. For now, a single
registrar process must serialize writes and open a fresh writable catalog for
each registration session. This is an operating rule, not a lock guarantee.
Before accepting concurrent writers, test target-filesystem lock semantics and
cover every read-modify-write path and TinyDB refresh in a guarded writer session.

Keep TinyDB for zero-setup single-user use while evaluating storage robustness.
Its JSON storage writes in place, and the current `UnitOfWork` holds in-memory
compensating actions rather than a durable transaction. Define recovery for an
interrupted write, a managed object without a record, and a record without its
readable link. Protect `catalog.json` updates with replacement and recovery.
Do not select SQLite from the assumption that local-disk guarantees carry over
to NFS or GPFS; test the actual deployment filesystem first.

Read-only opening now avoids write handles, directory creation, and audit
emission and rejects catalog mutation before hooks or files are touched. Since
TinyDB writes its JSON in place, a reader running during a write may still
observe an incomplete document; coordinate readers or reopen after writes.

### 2. Make Finished-Output Registration A Short Operation

Support the two existing ownership choices directly:

- `add_reference(path)` records a completed external path without taking
  ownership. The caller verifies existence, completeness, and readability.
- `add_file(path, operation="move", derived_metadata=...)` moves a completed
  file or directory store into managed storage with caller validation facts.

For now, the workflow registrar owns stable run/attempt/output identity and
defines `skip`, `update`, and `error` behavior for a repeated registration.
Core idempotency, reconciliation of partial multi-output runs, and adoption of
an already published path under the managed root remain deferred until a real
registrar demonstrates the required semantics. Current Verification Games Zarr
helpers can still leave an unregistered object if they move a directory before
registration. Leave checksum policy to the caller or a small optional helper;
checksumming a large directory store should not be mandatory. Do not call the
current `add_artifacts()` atomic batch ingest.

### 3. Use Collection Selection In Consumers

The CLI now has `reference`, `collection`, `members`, `locator`, strict
`search --one`, and readable view output. Keep local-path verification and
explicit URI/urlpath behavior distinct; a reference record does not guarantee
its target exists.

Promote the existing strict `Catalog.get_one()` lookup and expose its
zero-or-multiple-match errors in a CLI lookup. Do not make a job silently choose
`results.ids[0]` or a moving `latest` record. On BP1, an MHD/EUROPE/co2 search
can return both UKV and UMG footprint collections; a notebook selects the first
result. A reproducible run should resolve and record an exact record ID,
locator, and relevant input version before launch. For local
collection references, `member_paths()` expands the stored relative pattern
and returns sorted current paths. The NAME example selects inclusive `YYYYMM`
months **before** xarray opens files and rejects absent or duplicate months.
The footprint importer now reuses a matching root and canonical pattern on
`--append`, refreshes its month summaries, and rejects ambiguous existing
records. Consumers such as Verification Games still need to call the selector
and pin the chosen record ID; the existing scripts can open a broad glob.
Leave exact within-file time slicing to xarray or OpenGHG. A stored
`month_start`/`month_end` range cannot prove which months exist. URI-only
collections need a reachable mount or a separate listing adapter.

### 4. Continue Simplifying The Add Path And Containing Hooks

Preserve public add methods, hook names, UUID objects, template links, and
deletion behavior. The cleanup removed the generic runner interface, the
unused materialisation intent/target/plan wrappers, and duplicate application
adapters. `StoragePlan` is now the sole concrete primary plan. Add operations
propose it once after validation, then adjust it for an explicit locator hook
redirect before writing. Naming-input changes belong before validation;
locator hooks no longer cause a second planning pass. Continue reducing
internal layers only when the result makes control flow clearer. A plan remains
a proposal and must be rechecked when used.

Callers should normally compute metadata and validate their output before
calling an add method. Keep hooks as a compatibility extension. The add runner
now rejects late changes to schema requirements, storage target, and template
primary name. Further work should test observable results, rollback, ownership,
and interrupted-operation repair.

## Defer Until A Real Workflow Requires Them

- Writer results (#117): add a structured result only when a real writer
  produces a locator or facts unavailable from its plan and caller metadata.
- Artifact read handles (#118): add a context-managed local reader only when
  callers need resource ownership beyond `record.path()` and ordinary library
  calls. The existing catalog context manages the database handle; it does not
  open artifact data. A public write handle needs a durable publish and
  replacement contract.
- Core idempotency and managed-path adoption: leave identity and retry policy
  with each workflow registrar until observed workflows justify shared semantics.
- Managed collection append, typed pipelines, converter routing, mount-relative
  locators, replica/cache/deep-store state machines, ACLs, and leases: retain
  these as research, with a named user workflow and failure that simpler
  registration and lookup cannot handle before implementation.

The earlier [storage and ownership safeguards](architecture.md) remain part of
the current baseline: rendered paths reject obvious escapes, purge only treats
known managed write modes as owned, and creating a catalog rejects an existing
`catalog.json`. A concurrent filesystem change after path planning still needs
write-time protection.
