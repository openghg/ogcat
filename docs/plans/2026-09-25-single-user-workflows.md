# Single-user workflow implementation record

Status: active implementation plan, started 2026-09-25. The
[roadmap](../roadmap.md) gives the shorter priority order. This page records
the reasons for changes and the compatibility checks; it is not an additional
public API specification.

## Evidence and constraints

- BP1's footprint catalog has 229 collection records covering 14,821 existing
  monthly files. It selects a series by metadata, but consumers expand a broad
  glob and open files before slicing time. A MHD series matches 144 files while
  a 2021 request needs 12. The older backup held 14,325 per-file references.
- BP1's `games_catalog` has 226 mixed managed and reference records. The
  separate result-output catalog had 1,232 references and was updated on
  2026-09-25. Runs already compute outside ogcat and register results afterward.
- These BP1 paths are on GPFS following the Rocky Linux move. The earlier
  assumption that BP1 uses NFS was wrong. Filesystem behavior on any other
  NFS deployment must be checked separately before changing the database.
- One writer at a time is the target operating model. HPC tasks must not hold
  a catalog lock or unit of work during computation. Catalog mutations should
  happen in short, serial registration steps.
- Existing users rely on `add_file`, `add_artifact`, `add_reference`,
  `add_collection`, record search/path access, UUID primary storage, optional
  human-readable template links, and delete/restore/purge. Preserve them.

## Decisions

1. **Keep TinyDB for this delivery.** It has the zero-setup property wanted for
   single-user catalogs. Add read-only access and an explicit single-writer
   operational contract. The library does not yet enforce that contract:
   concurrent writable processes can lose records, and a reader can see a
   partial in-place JSON write. A transaction-only lock would miss record
   reads before the transaction, spec updates, and direct repository access;
   long-lived instances also cache TinyDB state. Do not infer SQLite safety
   from a local-disk test on NFS or GPFS. Revisit the backend and a complete
   writer boundary only after testing the target filesystem.
2. **Make lookup and registration ordinary operations.** Promote existing
   `Catalog.get_one()` for unambiguous selection. Add local collection member
   enumeration and CLI commands for the existing reference and collection
   methods. Leave file-format parsing and exact data time slicing with the
   scientific caller.
3. **Keep ownership explicit.** A reference records an external path that ogcat
   must not delete. A managed move owns the resulting object and its readable
   view. Defer adoption of an already published managed path; it requires a
   demonstrated workflow plus containment and completion checks before purge
   can own it.
4. **Keep hooks compatible while reducing their role.** Direct method inputs
   and caller-side validation are the default for new workflows. The add path
   must validate again after its final mutating hook. Use one authoritative
   storage plan rather than repeated plan and intent wrappers.
5. **Retire speculative plans.** The earlier long-term backlog, loose ideas,
   and filesystem research live under [archive](archive/index.md). ADR 0002 is
   marked superseded as a project direction; it remains available for history.

## Work sequence and acceptance

### Read and select

- Open an existing catalog in a genuinely read-only mode without creating
  directories, logs, or write handles; reject mutations before hooks or files
  are touched. Reopen after another process writes; TinyDB readers do not
  promise live cross-process consistency.
- Expand only a local collection's canonical classification pattern to sorted
  existing paths. Reject unsupported URI/urlpath member enumeration, missing
  roots, unsafe paths, and non-collection records. A NAME-specific helper may
  select months from filename dates before xarray opens data; core ogcat does
  not interpret scientific filename dates.
- Promote `get_one()` to downstream examples and expose a strict CLI lookup.
  Zero or multiple matches are errors. BP1 MHD/EUROPE/co2 has both UKV and UMG
  series, so selection needs an explicit meteorology model or date policy.

### Register and inspect

- CLI parity: add a reference, add a collection, enumerate members, emit raw
  locators, and show the readable template link beside the canonical path.
  Keep machine-readable stdout predictable.
- Allow a finished file or directory store to move into managed storage while
  carrying caller-supplied validation metadata. Avoid requiring users to
  synthesize `StoragePlan` and naming fields for that common operation.
- Route completed outputs to one registrar process per catalog, which opens a
  writable catalog only while registering. The workflow registrar owns its
  run/attempt/output identity and retry behavior. Generic core idempotency and
  adoption remain deferred. This is an operating rule until a full writer
  boundary is implemented. Keep worker computation and validation outside
  catalog transactions.

### Simplify and harden

- Make the add runner's validation and final target explicit. Preserve current
  public methods, hook order, audit phases, rollback, and UUID/template view
  behavior while removing redundant internal planning layers.
- Keep delete, restore, and purge. Test that reference records are never
  treated as ownership of bytes; address inbound references before promising
  that purge preserves all active paths.
- Add repair or reconciliation for interrupted managed writes and readable
  links before claiming crash recovery. A single writer does not make
  in-place TinyDB writes or multi-step file operations atomic.

## Change record

Record completed changes here with their reason and validation. This table is
updated as implementation lands; a proposal above is not evidence of delivery.

| Change | Why | Validation / limit |
| --- | --- | --- |
| Read-only catalog and CLI inspection | Prevent read commands from requiring database, audit, or directory write access on a shared catalog. Reject mutators before hooks or bytes are touched. | Read-only repository/catalog/CLI regression tests; reopen after external writes. Concurrent reads during TinyDB writes are not guaranteed. |
| Local collection member enumeration and NAME month selection | The BP1 collection catalog reflects users' series-level model, but opening all 144 monthly files for a 12-month request wastes I/O. Keep scientific date rules in the footprint example. | Collection safety tests and MHD-like tests selecting 12 of 144 files, plus missing/duplicate/ambiguous series checks. Remote members remain unsupported. |
| Rerunnable footprint importer | Repeating an import should refresh month summaries rather than add another record for the same series. | Mounted and listing-backed rerun tests cover a newly discovered month and ambiguous existing duplicates. An old URI record stays a URI if the source later becomes mounted. |
| CLI parity for registration and lookup | Avoid requiring Python for common reference, collection, strict selection, locator, and readable-view operations. | CLI command tests and documented help output. |
| Managed completed-output metadata | Preserve caller validation facts when moving a finished file without constructing a `StoragePlan`. | `add_file` regression test covers a move, normalized values, and caller precedence over generic extraction. |
| Add-path simplification and late validation | Remove an internal intent conversion and mutable plan cache; reject hooks changing the target or invalidating schema after bytes have been written. | Lifecycle/hook/writer tests check hook order, rollback, late schema/target/template changes. Managed primary planning still runs twice to preserve hook phase order. |
| Remove unused orchestration abstractions | The generic runner base, materialisation intent/target/plan wrappers, and duplicate application adapters added extension points without a second implementation or workflow that used them. Keep concrete add and record-lifecycle coordinators and one concrete `StoragePlan`. | Existing public API and lifecycle tests remain the compatibility boundary. Workflow registrars still own identity and retry policy; core idempotency, adoption, and public handles remain deferred. |
| Retired plans and refreshed docs | Keep speculative filesystem/handle ideas accessible without presenting them as current scope. | Archived plans under `docs/plans/archive`, ADR status and roadmap updated; documentation build before PR. |

## Validation evidence

- Full ogcat pytest suite, Ruff on changed Python files, and configured
  Pyright checker passed on 2026-09-25. The offline Sphinx HTML build passed
  with `-W`; online intersphinx inventories were unavailable from this host.
- Forty focused Verification Games tests passed in the local checkout for
  forward-model, production, baseline, and flux-stage behavior. That checkout
  was behind its origin by 84 commits; these are compatibility checks, not a
  production run on BP1.
- The NAME example tests cover two 144-member MHD series with distinct met
  models, twelve selected months, missing/duplicate months, and reruns against
  mounted and listing-backed roots. No remote data were copied or changed.
- The orchestration cleanup passed the full ogcat suite, Ruff, Pyright, and an
  offline Sphinx build with warnings treated as errors. It changes internal
  maintainer modules only; the documented public API and operation ordering are
  unchanged.

## Deliberately deferred

Generic idempotency, managed-path adoption, read/write handles, converter
pipelines, distributed leases, ACLs, remote member listing, a server, and a
SQLite migration have no demonstrated need in the three inspected BP1
catalogs. Workflow registrars own identity and retry policy for now. Preserve
the ability to add shared machinery when a real workflow and deployment
contract require it.
