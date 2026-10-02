# Single-user workflow implementation record

Status: core delivery on main, updated 2026-10-02; started 2026-09-25. The
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
   session contract. The October completion pass enforces it with a stable
   database-sidecar POSIX lock held until close. This boundary covers record
   reads before transactions, spec updates, and direct repository access; a
   transaction-only lock would miss them. Complete JSON replacement and
   uncached pathname reads avoid partial in-place writes and stale reader
   handles. Do not infer SQLite safety from a local-disk test on NFS or GPFS.
   Test deployment durability and recovery before revisiting the backend.
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
  are touched. Later repository queries read the current database pathname;
  earlier returned results remain snapshots. Release resources and the writer
  lock with a catalog context or `close()` before long computation or opening
  another writable session.
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
  adoption remain deferred. The writable-session lock now enforces exclusion;
  keep worker computation and validation outside writable catalog sessions.

### Simplify and harden

- Make the add runner's validation and final target explicit. Preserve current
  public methods, hook order, audit phases, rollback, and UUID/template view
  behavior while removing redundant internal planning layers.
- Keep delete, restore, and purge. Test that reference records are never
  treated as ownership of bytes. The completion pass checks dependencies from
  all retained records, including tombstones and symlinked directory paths,
  before removal; force cannot override the guard.
- Add repair or reconciliation for interrupted managed writes and readable
  links before claiming crash recovery. Atomic JSON replacement and one
  writer do not make multi-step file operations or cross-file commits atomic.

## Change record

Record completed changes here with their reason and validation. This table is
updated as implementation lands; a proposal above is not evidence of delivery.

| Change | Why | Validation / limit |
| --- | --- | --- |
| Read-only catalog and CLI inspection | Prevent read commands from requiring database, audit, or directory write access on a shared catalog. Reject mutators before hooks or bytes are touched. | Original read-only repository/catalog/CLI regressions. The completion pass below adds fresh pathname reads and atomic JSON publication. |
| Local collection member enumeration and NAME month selection | The BP1 collection catalog reflects users' series-level model, but opening all 144 monthly files for a 12-month request wastes I/O. Keep scientific date rules in the footprint example. | Collection safety tests and MHD-like tests selecting 12 of 144 files, plus missing/duplicate/ambiguous series checks. Remote members remain unsupported. |
| Rerunnable footprint importer | Repeating an import should refresh month summaries rather than add another record for the same series. | Mounted and listing-backed rerun tests cover a newly discovered month and ambiguous existing duplicates. An old URI record stays a URI if the source later becomes mounted. |
| CLI parity for registration and lookup | Avoid requiring Python for common reference, collection, strict selection, locator, and readable-view operations. | CLI command tests and documented help output. |
| Managed completed-output metadata | Preserve caller validation facts when moving a finished file without constructing a `StoragePlan`. | `add_file` regression test covers a move, normalized values, and caller precedence over generic extraction. |
| Add-path simplification and late validation (PR 134) | Remove an internal intent conversion and mutable plan cache; reject hooks changing the target or invalidating schema after bytes have been written. | Lifecycle/hook/writer tests check hook order, rollback, late schema/target/template changes. At this stage, managed primary planning still ran twice; the follow-up below removes that duplication. |
| Remove unused orchestration abstractions | The generic runner base, materialisation intent/target/plan wrappers, and duplicate application adapters added extension points without a second implementation or workflow that used them. Keep concrete add and record-lifecycle coordinators and one concrete `StoragePlan`. | Existing public API and lifecycle tests remain the compatibility boundary. Workflow registrars still own identity and retry policy; core idempotency, adoption, and public handles remain deferred. |
| Plan primary storage once | A locator hook runs after a destination is chosen. Repeating planning afterward can fail on changed naming inputs even when the accepted locator remains valid, and repeats collision selection. Keep the proposed plan and adjust it only for an explicit locator redirect. | Regression coverage checks one planning call, locator redirects and removal, preserved hook order, managed naming metadata, and late mutation rollback. Writers still validate the target before materialisation; this is not filesystem race protection. |
| Catalog resource lifetime and CLI cleanup | The documented reopen-after-registration workflow needs a way to release the old database handle. Add `Catalog.close()` and context management; CLI commands release catalogs on success and failure. | Resource tests cover actual handle closure, repeated close, exceptional exit, cached reads after close, source preservation on rejected writes, and transaction rollback before closing. Completed adds stay committed. At that delivery, this did not add locking or durable transactions; the October pass below adds the writer-session lock. |
| Retired plans and refreshed docs | Keep speculative filesystem/handle ideas accessible without presenting them as current scope. | Archived plans under `docs/plans/archive`, ADR status and roadmap updated; documentation build before PR. |

## Validation evidence

The entries below describe earlier deliveries. They do not establish compatibility
with the October writer-session contract; current verification is recorded separately.

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
- The single-pass planning and resource-lifetime follow-up passed 557 tests
  with four optional tests skipped, changed-file Ruff checks, and Pyright with
  no errors or warnings. Forty focused Verification Games tests also passed
  against the current editable ogcat checkout; no BP1 production data were
  modified. The offline Sphinx HTML build passed with warnings treated as
  errors. The revised getting-started and resource-lifetime examples were
  exercised against a temporary catalog, including composed registration and
  reopening a read-only view.

## Core completion pass — 2026-10-02

PR 136 is merged. Subsequent changes are reviewed and pushed directly to main,
as requested. Implementation uses sol-6.1 medium agents with separate review.

The completion boundary is a usable local catalog: create/open/close, register
managed files or external references/collections, find records and members,
edit metadata, and delete/restore/purge with clear ownership. Python and CLI
must cover those ordinary operations. It must reject competing writers and
missing/corrupt databases rather than silently losing existing records. Long
computations stay outside catalog sessions; this is not a distributed database.

Delivered core behavior and its rationale:

| Change | Why | Boundary |
| --- | --- | --- |
| Writable-session exclusion | A lock only around transactions misses earlier reads and direct repository writes. | Stable POSIX advisory sidecar lock until close; competing writable opens fail immediately. Test semantics on deployment filesystems. |
| Complete JSON publication | In-place truncation can lose records or expose partial documents. | Same-directory temporary files, complete serialization, file fsync, atomic replacement, preserved permission bits. No directory fsync, cross-file ACID, or crash recovery. |
| Safe create/open and fresh reads | A missing database must not silently become an empty catalog; a replaced inode must not leave readers stale. | Refuse existing spec/database on create and missing/corrupt database on open; legacy zero-byte databases remain accepted. Each read uses the current pathname with query caching disabled. |
| Visible rollback failures and terminal states | Silent cleanup failures hide incomplete work; finished transactions cannot safely stage more changes. | Try all actions; attach failures to the original error or raise an exception group. Reject new staged work, rollback registrations, and commits after completion. |
| Purge dependencies and failed-purge lifecycle | Removing owned data can break retained references, and a forced partial purge must not leave an active record. | Protect active and deleted references, including symlinked directory paths; force cannot bypass dependencies. Incomplete attempts retain a tombstone; restore rejects incomplete purge or removed artifacts. |
| CLI metadata editing | Ordinary updates should not require custom Python. | Merge, replace, remove, or edit derived metadata through validated existing methods; parse JSON objects containing `=` correctly. Primary and readable-link names remain stable. |
| Local integrity checks and backup guidance | Users need a bounded inspection and recovery procedure. | `Catalog.check()` and CLI inspect registered local paths and view links, without reading contents, remote targets, or orphan files. See [checking and backing up](../how-to/check-and-back-up.md). |

Validation on 2026-10-02: the integrated ogcat suite passed **628 tests** in
33.65 seconds, with four tests skipped for missing optional Zarr/NetCDF backends.
Full Ruff checks and formatting passed; configured Pyright reported no errors.
Persistence and purge changes passed independent review after blocker fixes;
CLI and transaction reviews had no remaining blockers. The offline Sphinx HTML
build passed with warnings treated as errors. Sixteen local Python cells from
the Verification Games recipe ran in order, including create/close/reopen and
final cleanup; its mounted BP1 cell was skipped and optional NetCDF opening
reported the missing backend. No BP1 production tests or writes were run.

The real Verification Games workflow check exposed overlapping writable catalog
instances, which now fail immediately. A separate compatibility branch is in
progress. The earlier forty-test runs do not show that the new contract leaves
those workflows unchanged. No BP1 production deployment or data changes are
part of this delivery.

Next work is targeted deployment-filesystem durability/recovery evidence and
consumer session adaptation. Optional managed-path adoption still needs a real
workflow, containment checks, and completion semantics. Extra abstractions,
backend migrations, and automatic destructive repair remain deferred.

## Deliberately deferred

Generic idempotency, managed-path adoption, artifact read/write handles, converter
pipelines, distributed leases, ACLs, remote member listing, a server, and a
SQLite migration have no demonstrated need in the three inspected BP1
catalogs. Workflow registrars own identity and retry policy for now. Preserve
the ability to add shared machinery when a real workflow and deployment
contract require it.
