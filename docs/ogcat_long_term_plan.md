# ogcat long-term implementation plan

Status: draft for Codex-driven development  
Audience: ogcat maintainers, OpenGHG maintainers, future plugin authors  
Primary goal: evolve ogcat from a lightweight catalog MVP into a practical, extensible data-catalog and artifact-management layer that can eventually replace or orchestrate much of OpenGHG's storage/object-store logic without importing OpenGHG's domain-specific behaviour into ogcat core.

---

## 1. Executive summary

ogcat should remain simple for the common case:

> "I have a file or object, I want ogcat to place or reference it, store useful metadata, and let me search it later."

The long-term direction is more ambitious:

> "ogcat should provide generic cataloging, artifact tracking, locator management, validation, operation orchestration, logging, and extensibility primitives. Domain packages such as OpenGHG should add record schemas, data schemas, parsers, standardisers, transformers, export formats, and storage adapters as plugins."

The plan below uses four priority levels.

- **Priority 1**: harden the current TinyDB-backed MVP without over-building it. Add the abstractions that prevent redesign later: records/artifacts/locators, schema validation, hooks, lightweight unit-of-work/journal semantics, fsspec-backed storage, structured logging, better CLI, docs, and tests.
- **Priority 2**: make plugins real. Add entry-point discovery, operation pipelines, batch ingest, richer example projects, and a first OpenGHG plugin/adaptor layer.
- **Priority 3**: prepare for multi-user and larger-scale operation. Add a stronger backend path, server-managed transactions, richer search/indexing, permissions, durable event logs, and worker execution.
- **Priority 4**: enterprise-grade polish and ecosystem integration. Add UI/server features, policy engines, provenance graphs, publishing workflows, and interoperability with systems such as iRODS, RO-Crate, DataLad, Intake, and institutional object stores.

The immediate target is not to turn TinyDB into Postgres. The immediate target is to make the core concepts correct enough that a Postgres/server backend can implement the same interfaces later.

---

## 2. Background and design pressure

### 2.1 Problems inherited from OpenGHG-style storage

OpenGHG has shown that a metadata-first "object store" can be powerful, especially for real scientific datasets that need to be found by semantic attributes rather than by filesystem location alone. The same experience also exposes several problems that ogcat should avoid:

- domain-specific parsers and storage rules make simple or unusual data awkward to add;
- metadata keys, formatting, schema, and validation tend to become dispersed across parser/storage code;
- object-store and metastore logic can become tightly coupled to domain logic;
- logging is often per-user rather than per-catalog, making multi-user debugging difficult;
- storage operations are hard to reason about when a catalog record is written but the artifact write fails, or vice versa;
- adding new data types can require invasive changes instead of registering a schema and parser/transform hooks;
- exports such as ObsPack-style bundles are workflow outputs, not primitive storage behaviours, but they need catalog awareness;
- concurrency and durability assumptions are usually implicit;
- object-store interfaces are useful but historically not formalised as clean backend/protocol contracts.

### 2.2 What ogcat should provide

ogcat core should provide generic machinery:

- catalog records;
- artifacts attached to records;
- locators for local paths, fsspec URLs, external URIs, and deferred/planned storage locations;
- metadata dictionaries plus optional schemas;
- validation;
- metadata extraction from self-describing files where generic extraction is reasonable;
- operation records and operation journals;
- hook/plugin interfaces;
- structured catalog-level logging;
- storage planning and path generation;
- backend abstractions;
- CLI and Python API parity;
- documentation and examples.

### 2.3 What ogcat core should not provide

ogcat core should not encode OpenGHG-specific concepts such as:

- surface/flux/model/input/output data-type semantics;
- OpenGHG-specific `metakey` formatting;
- species/site/network-specific validation;
- ObsPack-specific export rules;
- ICOS/OpenGHG retrieval semantics;
- atmospheric inversion-specific schemas;
- domain-specific NetCDF/Zarr scientific schemas beyond generic metadata discovery.

Those belong in plugins.

---

## 3. Core architecture target

The following conceptual architecture should guide all priorities.

```text
                     CLI / Python API / future server
                                |
                          Catalog service
                                |
       ----------------------------------------------------
       |                    |                 |            |
   Backend API          Storage API       Hook API      Logging
       |                    |                 |            |
 TinyDB / SQLite /      fsspec / local /  plugins /     event log /
 Postgres later         external URI      schemas       audit log
       |
 CatalogRecord + Artifact + Locator + Operation + Journal
```

### 3.1 Essential domain-neutral concepts

#### `CatalogRecord`

A logical catalog entry.

Suggested fields:

- `id`: stable, generated by ogcat unless supplied.
- `kind`: broad record kind, e.g. `dataset`, `document`, `run`, `bundle`, `collection`, `external`.
- `schema_name`: optional record schema name.
- `schema_version`: optional schema version.
- `title`: optional human-friendly title.
- `description`: optional text.
- `metadata`: flexible dictionary.
- `tags`: list of strings.
- `artifacts`: list of artifact descriptors.
- `created_at`, `updated_at`.
- `created_by`, `updated_by`.
- `version`: integer or semantic string for optimistic update checks.
- `status`: `active`, `planned`, `failed`, `deprecated`, `deleted`.
- `lineage`: optional references to source records and operations.

#### `Artifact`

A physical or virtual object associated with a record.

Suggested fields:

- `artifact_id`: stable within record.
- `role`: e.g. `primary`, `metadata`, `preview`, `log`, `script`, `config`, `derived`.
- `locator`: a locator object.
- `media_type`: e.g. `application/netcdf`, `application/x-zarr`, `text/plain`.
- `format`: human-oriented format tag, e.g. `netcdf`, `zarr`, `csv`, `pdf`, `directory`.
- `size_bytes`: optional.
- `hashes`: optional dict, e.g. `{"sha256": "..."}`.
- `created_at`.
- `metadata`: artifact-level metadata.
- `status`: `planned`, `available`, `missing`, `failed`, `external`.

#### `Locator`

A serialisable description of where an artifact lives or will live.

Suggested locator types:

- `local_path`: path on a local or shared filesystem.
- `fsspec_url`: URL plus storage options reference.
- `external_uri`: URI not managed by ogcat, e.g. ICOS URI, DOI landing page, FTP URL.
- `planned_path`: path planned by ogcat but not yet written.
- `openghg_object`: compatibility locator for OpenGHG object-store-backed data.
- `inline`: only for very small metadata-like payloads; use sparingly.
- `bundle_member`: member within an archive or bundle.

A locator should be serialisable and should not require importing a domain plugin to understand its basic shape.

#### `Operation`

A catalogued action that may produce, transform, validate, export, or update records/artifacts.

Suggested fields:

- `operation_id`.
- `operation_type`: `add`, `ingest`, `extract_metadata`, `validate`, `transform`, `export`, `delete`, `repair`.
- `status`: `planned`, `running`, `succeeded`, `failed`, `rolled_back`.
- `inputs`: record IDs, locators, or external parameters.
- `outputs`: created/updated record IDs and artifact IDs.
- `parameters`: serialisable dict.
- `started_at`, `ended_at`.
- `created_by`.
- `log_ref`: link to event log/journal entries.
- `error`: structured error summary if failed.

---

## 4. Priority levels

## Priority 1: harden the MVP and create stable extension seams

Priority 1 is the work Codex should be able to start immediately. Keep changes incremental. Do not implement a server. Do not require Postgres. Do not make OpenGHG a hard dependency. Do not require async in the core. Build the smallest APIs that make later backends and plugins possible.

### P1 target outcomes

By the end of Priority 1:

1. ogcat has a documented record/artifact/locator model.
2. TinyDB remains the default backend, but sits behind a backend protocol.
3. basic validation uses Pydantic or a similarly explicit schema layer.
4. fsspec is the storage abstraction for local and remote filesystems, but local-path usage remains simple.
5. adding a record from memory or file uses a unit-of-work style operation that can clean up after partial failure.
6. hooks exist at stable points in add/ingest/search/show/storage flows.
7. logs are catalog-level, structured, queryable enough for debugging, and filterable by user/session/operation.
8. CLI and Python API stay in parity for core operations.
9. examples are reorganised into small, reliable demonstrations.
10. Sphinx docs can be built and include at least one tutorial-style example.

---

### Issue P1-01: Write the architecture decision record for ogcat core concepts

Labels: `priority:1`, `area:architecture`, `kind:docs`

#### Goal

Create a concise architecture decision record that defines the canonical vocabulary for ogcat.

#### Rationale

Before adding transactions, hooks, fsspec, validation, or plugins, the repository needs a shared vocabulary. This prevents every feature from inventing its own meaning of "object", "record", "artifact", "metadata", "locator", "operation", and "store".

#### Tasks

- Add `docs/adr/0001-core-concepts.md` or equivalent.
- Define:
  - catalog;
  - backend;
  - record;
  - artifact;
  - locator;
  - metadata;
  - schema;
  - hook;
  - operation;
  - unit of work;
  - journal;
  - event log.
- Include a "non-goals" section:
  - ogcat core does not know OpenGHG data types;
  - ogcat core does not implement a general database;
  - TinyDB backend is not ACID;
  - hooks must not make core depend on plugin packages.
- Add a short diagram.
- Add a "compatibility target" note explaining that OpenGHG object-store logic should eventually sit behind adapters/plugins.

#### Acceptance criteria

- The ADR is linked from the README.
- The ADR is short enough to read before contributing.
- All subsequent P1 issues use this vocabulary.

#### Codex prompt

```text
Create docs/adr/0001-core-concepts.md for ogcat. Define catalog, backend, record, artifact, locator, metadata, schema, hook, operation, unit of work, journal, and event log. Keep the document domain-neutral, but include a short note that OpenGHG-specific parsers/schemas/storage adapters should live in plugins rather than ogcat core.
```

---

### Issue P1-02: Introduce explicit Pydantic models for records, artifacts, and locators

Labels: `priority:1`, `area:model`, `kind:feature`

#### Goal

Replace implicit dictionaries at the API boundary with explicit model classes while preserving flexible metadata.

#### Design

Create model classes such as:

- `CatalogRecord`
- `Artifact`
- `Locator`
- concrete locator variants:
  - `LocalPathLocator`
  - `FSSpecLocator`
  - `ExternalURILocator`
  - `PlannedPathLocator`

Avoid over-validating user metadata. The record model should validate ogcat's own structural fields and allow `metadata: dict[str, Any]`.

#### Tasks

- Add a model module, for example `src/ogcat/models.py`.
- Use Pydantic models or dataclasses plus Pydantic `TypeAdapter`.
- Preserve JSON-serialisable output.
- Add `model_version` fields where needed.
- Add conversion helpers:
  - `CatalogRecord.from_mapping(...)`
  - `record.to_mapping()` or `record.model_dump(...)`
  - `normalise_locator(...)`
- Keep backwards compatibility with existing TinyDB documents as much as practical.
- Add migration notes if the existing record shape changes.

#### Acceptance criteria

- Existing tests still pass or are updated for the new canonical shape.
- A record can be round-tripped through JSON.
- A record can be stored and loaded through the TinyDB backend.
- Metadata remains flexible.
- CLI output remains understandable.

#### Testing

- Unit tests for valid and invalid record structures.
- Round-trip tests for local, fsspec, external, and planned locators.
- Regression tests for existing sample catalog entries.

#### Codex prompt

```text
Add explicit ogcat model classes for CatalogRecord, Artifact, and Locator variants. Keep metadata flexible, but validate ogcat's structural fields. Update the TinyDB repository code to store and load these models. Add round-trip tests and keep the current CLI behaviour working.
```

---

### Issue P1-03: Define a backend protocol and keep TinyDB as the default backend

Labels: `priority:1`, `area:backend`, `kind:feature`

#### Goal

Make the current TinyDB implementation one backend behind a small protocol so SQLite/Postgres/server backends can be added later without rewriting the public API.

#### Proposed protocol

```python
class CatalogBackend(Protocol):
    def add_record(self, record: CatalogRecord) -> CatalogRecord: ...
    def get_record(self, record_id: str) -> CatalogRecord | None: ...
    def update_record(self, record: CatalogRecord) -> CatalogRecord: ...
    def delete_record(self, record_id: str) -> None: ...
    def search(self, query: CatalogQuery) -> list[CatalogRecord]: ...
    def list_fields(self) -> FieldSummary: ...
```

Keep query support intentionally modest in P1.

#### Tasks

- Create `src/ogcat/backends/base.py`.
- Move TinyDB-specific logic to `src/ogcat/backends/tinydb.py`.
- Keep any existing `Catalog` facade thin.
- Define a `CatalogQuery` model or simple query object.
- Make backend choice configurable later, but default to TinyDB now.
- Do not expose TinyDB types in the public API.

#### Acceptance criteria

- Existing code can still open a local TinyDB-backed catalog with minimal changes.
- Tests can run backend contract tests against TinyDB.
- No public API requires direct TinyDB imports.

#### Testing

- Add backend contract tests.
- Use temporary directories for isolated catalog files.
- Include tests for:
  - add;
  - get;
  - search;
  - update;
  - delete/tombstone if supported;
  - field summary.

#### Codex prompt

```text
Refactor ogcat's TinyDB storage behind a CatalogBackend protocol. Preserve the existing simple Catalog API, but isolate TinyDB-specific code. Add backend contract tests that can later be reused for SQLite or Postgres backends.
```

---

### Issue P1-04: Implement a lightweight unit-of-work and operation journal

Labels: `priority:1`, `area:durability`, `kind:feature`

#### Goal

Provide recoverable add/ingest/update operations even though TinyDB does not provide real transactions.

#### Rationale

A common failure mode:

1. create a catalog record with a planned artifact locator;
2. try to write data to the planned location;
3. write fails;
4. the catalog now contains a misleading record unless cleanup runs reliably.

ogcat needs a unit-of-work abstraction that coordinates catalog writes, artifact writes, hooks, and rollback actions. In TinyDB mode this is a best-effort transaction simulation with a durable journal, not ACID.

#### Proposed API

```python
with catalog.unit_of_work(operation_type="add", user_id=user_id) as uow:
    record = uow.plan_record(...)
    artifact = uow.plan_artifact(...)
    uow.write_artifact(artifact, data)
    uow.add_record(record)
```

#### Journal states

- `started`
- `planned`
- `artifact_write_started`
- `artifact_write_succeeded`
- `catalog_write_started`
- `catalog_write_succeeded`
- `hooks_started`
- `committed`
- `rollback_started`
- `rolled_back`
- `failed`

#### Rollback actions

Examples:

- remove created file;
- remove temporary file;
- remove record by ID;
- restore previous record version;
- mark record as failed if destructive cleanup is unsafe;
- run plugin rollback hook.

#### Tasks

- Add `OperationJournal` interface.
- Store journal entries as JSON Lines in a catalog-controlled `logs/` or `.ogcat/journal/` directory.
- Implement `UnitOfWork`.
- Ensure operations have unique `operation_id`.
- Add cleanup behaviour for failed file writes and failed catalog writes.
- Add a `catalog recover` or `catalog doctor` command to inspect incomplete journal entries.
- Make rollback idempotent.
- Never silently delete data unless ogcat created it and the rollback action says it is safe.

#### Acceptance criteria

- If artifact writing fails after a record is planned but before commit, no active record remains.
- If catalog writing fails after an artifact was created, the artifact is removed or marked orphaned according to the rollback policy.
- Incomplete operations can be inspected from the CLI.
- Journal entries include enough information to debug failures.

#### Testing

- Simulate exceptions at each operation phase.
- Verify rollback actions run in reverse order.
- Verify rollback can be called twice safely.
- Verify journal state transitions.
- Include tests using temporary local files.

#### Codex prompt

```text
Implement a lightweight UnitOfWork and OperationJournal for ogcat. This is not a real TinyDB transaction system; it is a recoverable operation protocol with JSONL journal entries and idempotent rollback actions. Cover add-from-memory and add-from-file style workflows with tests that inject failures at each phase.
```

---

### Issue P1-05: Add structured catalog-level logging

Labels: `priority:1`, `area:logging`, `kind:feature`

#### Goal

Replace or supplement per-user ad hoc logs with catalog-level structured event logs.

#### Rationale

In a shared catalog, the maintainer should be able to answer:

- what did this user try to do?
- which record/artifact did it touch?
- which operation failed?
- was the failure in metadata extraction, validation, storage, backend write, hook execution, or export?
- what traceback or error type occurred?
- which plugin was involved?
- which version of ogcat/plugin was running?

#### Logging design

Use a structured event model. Avoid only writing free-text lines.

Suggested fields:

- `timestamp`
- `level`
- `event`
- `message`
- `operation_id`
- `record_id`
- `artifact_id`
- `user_id`
- `session_id`
- `catalog_path`
- `backend`
- `plugin`
- `hook`
- `command`
- `cwd`
- `hostname`
- `pid`
- `process_name`
- `python_version`
- `ogcat_version`
- `exception_type`
- `exception_message`
- `traceback`
- `extra`

#### Storage

Priority 1 should support:

- JSON Lines event log in catalog state directory;
- optional console output;
- optional per-user log for local development if desired, but catalog-level logs are primary for shared catalogs.

Potential layout:

```text
catalog-root/
  .ogcat/
    catalog.json
    journal/
      operations-YYYYMM.jsonl
    logs/
      events-YYYYMM.jsonl
```

#### CLI

Add a log inspection command:

```bash
ogcat logs --user USER
ogcat logs --record RECORD_ID
ogcat logs --operation OPERATION_ID
ogcat logs --level error
ogcat logs --since 2026-04-01
```

#### Tasks

- Add logging configuration module.
- Add event model.
- Add JSONL event writer.
- Add logging context helpers.
- Bind `operation_id`, `user_id`, `session_id`, `catalog_path`.
- Log every unit-of-work start/success/failure/rollback.
- Log hook start/success/failure.
- Log validation failures.
- Log storage failures.
- Add CLI filtering.

#### Acceptance criteria

- A failed add operation produces a structured error event.
- Logs can be filtered by user ID.
- Logs can be filtered by operation ID.
- Tests do not depend on exact traceback formatting.
- The logging system works without requiring structlog, but can use structlog internally if it remains lightweight.

#### Testing

- Unit tests for event serialisation.
- CLI tests for `ogcat logs`.
- Failure-injection tests from P1-04 should assert key log events exist.

#### Codex prompt

```text
Add catalog-level structured logging to ogcat. Write JSONL event logs under the catalog state directory, bind user/session/operation context, and add an `ogcat logs` command that filters by user, record, operation, level, and time. Integrate with UnitOfWork and hook execution.
```

---

### Issue P1-06: Add fsspec-based storage primitives without making simple local use harder

Labels: `priority:1`, `area:storage`, `kind:feature`

#### Goal

Use fsspec as the generic filesystem abstraction while preserving simple local path workflows.

#### Design principles

- Local files should remain trivial.
- Remote protocols should not require core code changes.
- Storage options should not be stored unsafely in records.
- Locators should be descriptive and serialisable.
- P1 should not promise robust concurrent remote writes.

#### Proposed components

- `StorageManager`
- `PathPlanner`
- `ArtifactMaterializer`
- `WriterCapability`
- `ArtifactReader`
- `FSSpecLocator`
- `StorageProfile`

#### Storage profile

A storage profile is a named configuration resolved outside the record:

```yaml
profiles:
  default:
    protocol: file
    root: ./data
  hpc-work:
    protocol: file
    root: /user/work/$USER/ogcat
  s3-project:
    protocol: s3
    root: s3://bucket/prefix
    storage_options_env: OGCAT_S3_OPTIONS
```

The record stores `profile_name` and relative path, or a resolved URL if appropriate. Avoid storing secrets.

#### Tasks

- Add fsspec as an optional or core dependency depending on packaging preference.
- Add storage profiles.
- Add path planning:
  - deterministic path templates;
  - collision handling;
  - optional hash suffix;
  - extension handling.
- Add atomic-ish local writes:
  - write to temporary path;
  - fsync/close where appropriate;
  - rename into place when possible.
- Add generic artifact metadata:
  - size;
  - modified time;
  - hashes where requested.
- Add tests using local filesystem.
- Add optional tests for memory filesystem.

#### Acceptance criteria

- `catalog.add_file(path)` can copy/register a file using local fsspec.
- `catalog.add_bytes(...)` or equivalent can write data from memory to a planned path.
- Failed writes cooperate with the unit-of-work rollback protocol.
- Existing simple path-based add/search workflows remain simple.

#### Testing

- Local filesystem write/read tests.
- Memory filesystem tests if useful.
- Failure injection for partial writes.
- Path-planning tests.

#### Codex prompt

```text
Add fsspec-based storage primitives to ogcat while keeping local paths simple. Implement storage profiles, path planning, safe local write via temporary file and rename where possible, locator serialisation, and integration with UnitOfWork rollback.
```

---

### Issue P1-07: Add schema validation with permissive metadata by default

Labels: `priority:1`, `area:schema`, `kind:feature`

#### Goal

Support rich domain schemas without making the default "just add some metadata" use case painful.

#### Design

There are two levels of validation:

1. **Core structural validation**: required for all records. This validates ogcat-owned fields.
2. **Optional record schema validation**: selected by `schema_name` and `schema_version`. This validates domain metadata.

In P1, schema registration can be simple and in-process.

#### Proposed API

```python
catalog.register_schema("paper", PaperSchema, version="1")
catalog.validate(record)
catalog.add(..., schema_name="paper")
```

#### Schema policy options

- `off`: do not run optional schema validation.
- `warn`: validate and log warnings, but allow record.
- `strict`: reject invalid records.

Default should probably be `warn` for interactive CLI and `strict` for explicit `validate`.

#### Tasks

- Add schema registry.
- Add validation result model:
  - `valid`;
  - `warnings`;
  - `errors`;
  - `schema_name`;
  - `schema_version`.
- Add CLI:
  - `ogcat validate RECORD_ID`
  - `ogcat validate --all`
  - `ogcat validate --file path/to/record.json`
- Add Python API.
- Support Pydantic schemas.
- Keep JSON Schema export as a later enhancement unless simple.

#### Acceptance criteria

- A record can be added with no domain schema.
- A record can be validated against a registered schema.
- Invalid metadata can be rejected in strict mode.
- Validation failures appear in logs.
- Validation results are machine-readable.

#### Testing

- Schema registry tests.
- Strict/warn/off tests.
- CLI tests for validation.

#### Codex prompt

```text
Add optional schema validation for ogcat records. Core record structure should always be validated; domain metadata should be validated only when a schema is selected. Support warn/strict/off policy modes and add Python API plus CLI validation commands.
```

---

### Issue P1-08: Define hook points and a minimal hook execution engine

Labels: `priority:1`, `area:plugins`, `kind:feature`

#### Goal

Provide stable customisation points without implementing a full plugin system yet.

#### Hook points

Priority 1 should define hook names and call them in the core workflow:

- `before_record_validate`
- `after_record_validate`
- `before_metadata_extract`
- `after_metadata_extract`
- `before_path_plan`
- `after_path_plan`
- `before_artifact_write`
- `after_artifact_write`
- `before_record_commit`
- `after_record_commit`
- `on_operation_error`
- `on_operation_rollback`

#### Hook signature

Keep signatures explicit. Avoid passing too much mutable state.

```python
class HookContext(BaseModel):
    catalog_id: str | None
    operation_id: str
    user_id: str | None
    session_id: str
    dry_run: bool = False

class HookResult(BaseModel):
    metadata_updates: dict[str, Any] = {}
    warnings: list[str] = []
```

#### Tasks

- Add `HookManager`.
- Allow hooks to be registered programmatically.
- Hooks can:
  - add metadata;
  - emit warnings;
  - fail operation if configured as required.
- Hook execution must be logged.
- Hook failures must interact with unit-of-work rollback.
- Do not implement package entry-point auto-discovery in P1 unless it is trivial; reserve full plugin discovery for P2.

#### Acceptance criteria

- A test hook can add metadata during add.
- A failing required hook aborts the operation and triggers rollback.
- A failing optional hook logs a warning.
- Hook behaviour is covered by tests.

#### Codex prompt

```text
Add a minimal HookManager with explicit hook points around validation, metadata extraction, path planning, artifact writing, record commit, error handling, and rollback. Programmatic registration is enough for P1. Log hook execution and integrate required-hook failures with UnitOfWork rollback.
```

---

### Issue P1-09: Add generic metadata extraction for self-describing files

Labels: `priority:1`, `area:metadata`, `kind:feature`

#### Goal

Provide useful generic metadata extraction without importing domain-specific parser logic.

#### Scope

Priority 1 should support:

- generic filesystem metadata;
- generic NetCDF metadata if the optional dependency is installed;
- generic Zarr metadata if the optional dependency is installed;
- generic directory/file tree summaries for examples like `ls -R` dumps;
- basic text metadata such as suffix, encoding guess if cheap, size.

Do not attempt OpenGHG-specific standardisation here.

#### Extracted metadata examples

For NetCDF:

- dimensions;
- coordinates;
- variables;
- global attributes;
- variable attributes;
- time coverage if easily derived;
- units if present;
- conventions attribute if present.

For Zarr:

- group paths;
- arrays;
- dimensions/shape/chunks where discoverable;
- attributes;
- consolidated metadata flag if discoverable.

#### Tasks

- Define `MetadataExtractor` protocol.
- Add builtin extractors:
  - file metadata;
  - directory summary;
  - NetCDF optional extractor;
  - Zarr optional extractor.
- Add hook integration:
  - `before_metadata_extract`;
  - `after_metadata_extract`.
- Add CLI option:
  - `ogcat add path --extract-metadata`
  - `ogcat extract-metadata path --json`
- Keep failures non-fatal by default unless `--require-metadata`.

#### Acceptance criteria

- NetCDF extraction works when dependencies are installed.
- Missing optional dependency gives a clear error.
- Extracted metadata is nested under a clear namespace, e.g. `metadata["netcdf"]`, not mixed into user metadata.
- Metadata extraction failures are logged.

#### Testing

- Use tiny generated NetCDF/Zarr fixtures.
- Test optional dependency missing behaviour if feasible.
- Avoid large binary fixtures.

#### Codex prompt

```text
Add generic metadata extraction protocols and builtin extractors for file metadata, directory summaries, and optional NetCDF/Zarr metadata. Keep extracted metadata namespaced and do not add OpenGHG-specific standardisation. Add CLI and tests with tiny generated fixtures.
```

---

### Issue P1-10: Improve CLI/API parity and add `info`, `fields`, and machine-readable output

Labels: `priority:1`, `area:cli`, `kind:feature`

#### Goal

Make the CLI useful for real catalog inspection and scripting.

#### Commands

Existing or near-existing commands should be normalised:

```bash
ogcat add
ogcat search
ogcat show
ogcat path
```

Add:

```bash
ogcat info
ogcat fields
ogcat validate
ogcat logs
ogcat doctor
```

#### `info`

Curated catalog summary, not a raw dump of `catalog.json`.

Should show:

- catalog path;
- backend;
- number of records;
- number of artifacts;
- known schemas;
- storage profiles;
- recent failed operations;
- log/journal location;
- ogcat version.

#### `fields`

A summary of metadata keys seen in records.

Should show:

- field path, e.g. `metadata.site`;
- observed types;
- count;
- example values;
- schemas that define the field if known.

This helps users discover searchable metadata.

#### `search --json`

Machine-readable output for scripts.

#### ID selection

For commands requiring IDs (`show`, `path`, later `validate`):

- allow exact ID;
- allow unambiguous prefix;
- allow `--first` from search;
- allow `--format compact` to show copyable IDs;
- consider shell-friendly `ogcat search ... --ids`.

Avoid interactive selection in P1 unless cheap.

#### Tasks

- Add output formatting layer:
  - human table;
  - compact;
  - JSON.
- Add Python API equivalents:
  - `catalog.info()`;
  - `catalog.fields()`;
  - `catalog.validate(...)`;
  - `catalog.logs(...)`;
  - `catalog.doctor()`.
- Improve errors:
  - no matches;
  - multiple ID prefix matches;
  - invalid metadata query;
  - missing artifact path;
  - backend unavailable.
- Add CLI tests.

#### Acceptance criteria

- Every new CLI command has an API equivalent.
- JSON output is stable enough for scripts.
- Error messages tell the user what to do next.
- ID prefix selection works for unambiguous prefixes and fails cleanly otherwise.

#### Codex prompt

```text
Improve ogcat CLI/API parity. Add curated `info`, metadata `fields`, `validate`, `logs`, and `doctor` commands. Add JSON output for search/show/info/fields where useful. Support unambiguous ID prefixes and script-friendly ID output. Add CLI tests and clearer errors.
```

---

### Issue P1-11: Add file locking and explicit concurrency caveats for TinyDB

Labels: `priority:1`, `area:concurrency`, `kind:feature`

#### Goal

Reduce accidental catalog corruption in simple shared-filesystem scenarios while being explicit about limitations.

#### Design

- Use file locking around TinyDB writes.
- Use optimistic version checks for record updates.
- Do not promise robust high-concurrency writes on NFS.
- Make the locking strategy backend-configurable.
- Add documentation warning about NFS and shared HPC filesystems.

#### Tasks

- Add lock wrapper around TinyDB write operations.
- Add timeout and clear error if lock cannot be acquired.
- Include process/user/host metadata in lock file if feasible.
- Add stale lock diagnostic command in `doctor`.
- Add record version increment on update.
- Add conflict error if expected version does not match.

#### Acceptance criteria

- Concurrent write tests do not corrupt the catalog in local temporary filesystem tests.
- Lock timeout errors are clear.
- Docs explicitly state limitations.

#### Testing

- Multiprocessing local test with several writers if practical.
- Unit tests for version conflict.
- Doctor test for stale-looking lock metadata.

#### Codex prompt

```text
Add a simple file-locking layer around TinyDB writes and optimistic record version checks. Be explicit in docs that this improves safety but is not a robust multi-user transaction system, especially on NFS/HPC filesystems. Add diagnostics to `ogcat doctor`.
```

---

### Issue P1-12: Reorganise examples into small, installable, reproducible examples

Labels: `priority:1`, `area:examples`, `kind:docs`

#### Goal

Make examples useful for users and maintainers without relying on sys.path hacks or large external data.

#### Example conventions

Each example should live under:

```text
examples/
  <example-name>/
    README.rst
    pyproject.toml             # optional, only if extra deps are needed
    data/
      README.md
      small-fixtures...
    scripts/
      run_example.py
    expected/
      README.md
```

Rules:

- assume ogcat is installed;
- no modifying `sys.path`;
- small fixtures only;
- generated data is acceptable;
- external downloads must be optional;
- examples should be runnable from a clean checkout;
- any resulting catalog should be written under a temporary or example-local ignored directory;
- examples should have one clear purpose.

#### P1 example set

1. **basic-file-catalog**
   - Add local files.
   - Search by metadata.
   - Show paths.
   - Use `info` and `fields`.

2. **metadata-extraction-netcdf**
   - Generate a tiny NetCDF file.
   - Extract generic metadata.
   - Search by extracted fields.

3. **transaction-failure-demo**
   - Demonstrate failed artifact write and rollback.
   - Show journal/log output.

4. **mini-bibdesk**
   - Catalog papers and attachments.
   - Parse `.bib` or `.ris` using optional dependencies.
   - Autofile PDFs and auxiliary material.
   - Keep this example small and separate from core.

#### Documentation format

Use Sphinx with either:

- `.rst` pages containing code blocks and command transcripts; or
- notebook-style examples rendered through a Jupyter/Sphinx integration.

Given the user's preference, `.rst` plus Jupyter execution directives can work, but keep examples simple enough that command-line scripts are the authoritative source.

#### Acceptance criteria

- At least two examples run in CI as smoke tests.
- Examples do not depend on private paths.
- Example data is small and license-compatible.
- README explains how to run examples after installing ogcat.

#### Codex prompt

```text
Reorganise ogcat examples into small installable examples. Remove sys.path hacks. Add basic-file-catalog, metadata-extraction-netcdf, transaction-failure-demo, and a minimal mini-bibdesk example. Keep data small, write generated catalogs to ignored output directories, and add smoke tests for selected examples.
```

---

### Issue P1-13: Establish documentation structure with Sphinx and API references

Labels: `priority:1`, `area:docs`, `kind:docs`

#### Goal

Create documentation that can explain ogcat to skeptical OpenGHG users and to new users with simpler needs.

#### Suggested docs layout

```text
docs/
  index.rst
  install.rst
  quickstart.rst
  concepts.rst
  cli.rst
  python-api.rst
  records-artifacts-locators.rst
  metadata-and-schemas.rst
  storage-profiles.rst
  hooks.rst
  transactions-and-logging.rst
  examples/
    basic-file-catalog.rst
    metadata-extraction-netcdf.rst
    mini-bibdesk.rst
  plugins/
    overview.rst
    openghg-plugin-sketch.rst
  adr/
    0001-core-concepts.md
```

#### Style

- Google-style docstrings in code.
- Sphinx autodoc or autosummary for API.
- Tutorials should be task-oriented.
- Reference pages should be terse and complete.
- Avoid Intake-style examples that look impressive but do not answer "how do I use this for my own files?"

#### Tasks

- Add Sphinx config if not present.
- Add docs dependencies as optional extra, e.g. `ogcat[docs]`.
- Add API reference generation.
- Add quickstart.
- Link examples.
- Add a "For OpenGHG developers" page explaining how ogcat differs from OpenGHG storage.

#### Acceptance criteria

- `uv run sphinx-build` succeeds locally/CI.
- Public classes/functions have Google-style docstrings.
- Quickstart covers:
  - create/open catalog;
  - add file;
  - add metadata;
  - search;
  - show path;
  - inspect fields;
  - validate if schema present.

#### Codex prompt

```text
Set up or tidy the ogcat Sphinx documentation. Add quickstart, concepts, CLI, Python API, storage profiles, hooks, transactions/logging, examples, and a page for OpenGHG developers. Use Google-style docstrings and ensure docs build in CI.
```

---

### Issue P1-14: Add packaging extras and development workflow hygiene

Labels: `priority:1`, `area:packaging`, `kind:maintenance`

#### Goal

Keep the core lightweight while making optional integrations discoverable.

#### Suggested extras

- `ogcat[netcdf]`: NetCDF/xarray/h5netcdf dependencies as chosen.
- `ogcat[zarr]`: zarr-related metadata extraction.
- `ogcat[fsspec]`: only if fsspec is not core.
- `ogcat[bib]`: `.bib`/`.ris` parser dependencies.
- `ogcat[docs]`: Sphinx and docs build dependencies.
- `ogcat[test]`: pytest and test helpers.
- `ogcat[dev]`: lint/test/docs tools.

#### Tasks

- Review `pyproject.toml`.
- Add optional dependencies.
- Add scripts/entry points.
- Add ruff/mypy/pytest config if appropriate.
- Add CI matrix if not present.
- Keep Python version policy explicit, probably Python 3.11+.

#### Acceptance criteria

- `uv sync --extra dev` works.
- `uv run pytest` works.
- `uv run ruff check` works if ruff is adopted.
- `uv run sphinx-build` works with docs extra.
- Core install does not pull heavy scientific packages unnecessarily.

#### Codex prompt

```text
Tidy ogcat packaging. Keep core lightweight, define optional extras for NetCDF, Zarr, bibliography parsing, docs, tests, and dev tooling. Ensure editable install plus tests/docs commands are documented and work.
```

---

### Issue P1-15: Add tests that exercise behaviour, not idiosyncratic implementation details

Labels: `priority:1`, `area:testing`, `kind:maintenance`

#### Goal

Create confidence in the architectural seams without freezing every incidental representation.

#### Test layers

1. **Model tests**
   - record/artifact/locator validation;
   - JSON round trips;
   - schema validation.

2. **Backend contract tests**
   - add/get/update/search/delete/list fields;
   - can run against TinyDB and future backends.

3. **Unit-of-work tests**
   - failure injection;
   - rollback;
   - journal state.

4. **Storage tests**
   - path planning;
   - local write/read;
   - fsspec memory filesystem if used;
   - cleanup.

5. **Hook tests**
   - metadata update hook;
   - required hook failure;
   - optional hook warning.

6. **CLI tests**
   - `add`;
   - `search`;
   - `show`;
   - `path`;
   - `info`;
   - `fields`;
   - `validate`;
   - `logs`;
   - `doctor`.

7. **Example smoke tests**
   - a small subset only;
   - avoid making examples the main test mechanism.

#### Avoid

- asserting exact pretty-table whitespace unless testing formatting;
- overfitting tests to one metadata key convention;
- requiring heavy dependencies for core tests;
- large binary fixtures;
- relying on live external services.

#### Acceptance criteria

- Test suite documents intended behaviour.
- Optional tests are marked.
- Future backend implementations can reuse contract tests.

#### Codex prompt

```text
Create a layered testing plan and implement the first batch of tests for models, TinyDB backend contract, UnitOfWork rollback, storage path planning, hooks, CLI output, and selected example smoke tests. Avoid overfitting to incidental formatting.
```

---

## Priority 2: make plugins, operations, and real examples first-class

Priority 2 should start once Priority 1 APIs exist and have tests. The goal is to prove that ogcat can host domain-specific logic without becoming domain-specific.

### P2 target outcomes

- Entry-point plugin discovery works.
- Plugins can register schemas, metadata extractors, operations, hooks, CLI subcommands if needed, and storage adapters.
- Operation pipelines support ingest/standardise/transform/export workflows.
- OpenGHG plugin prototype demonstrates replacing or orchestrating parts of OpenGHG storage logic.
- Examples become convincing enough for users outside ogcat development.
- Batch operations exist, but core remains synchronous and backend-neutral.

---

### Issue P2-01: Add entry-point based plugin discovery

Labels: `priority:2`, `area:plugins`, `kind:feature`

#### Goal

Allow packages to register ogcat extensions without modifying ogcat core.

#### Plugin capabilities

A plugin should be able to register:

- schemas;
- metadata extractors;
- hooks;
- path templates;
- operations;
- storage profiles or storage profile types;
- CLI command groups if needed;
- display formatters.

#### Proposed entry point

```toml
[project.entry-points."ogcat.plugins"]
openghg = "ogcat_openghg.plugin:register"
papers = "ogcat_papers.plugin:register"
```

#### Acceptance criteria

- Plugin discovery can be disabled.
- Plugin load errors are clear and logged.
- Plugin registration is idempotent.
- Tests include a tiny fake plugin.

---

### Issue P2-02: Define operation pipelines for ingest, transform, validate, and export

Labels: `priority:2`, `area:operations`, `kind:feature`

#### Goal

Represent multi-step workflows without hardcoding OpenGHG pipelines.

#### Examples

- ingest file -> extract metadata -> validate -> write artifact -> create record;
- transform NetCDF -> validate output schema -> create derived record;
- export selected records -> create bundle artifact;
- parse bibliography -> autofile attachments -> create paper records.

#### Design

An operation pipeline should be inspectable and loggable:

```text
operation:
  name: ingest-netcdf
  steps:
    - extract-metadata
    - validate-record
    - validate-data-schema
    - write-artifact
    - commit-record
```

#### Acceptance criteria

- A pipeline can be dry-run.
- A pipeline can log each step.
- A pipeline can fail and rollback safely.
- Hooks can attach to step boundaries.

---

### Issue P2-03: Add an OpenGHG plugin prototype

Labels: `priority:2`, `area:openghg`, `kind:feature`

#### Goal

Demonstrate that OpenGHG-specific storage/domain logic can move out of core OpenGHG code and into an ogcat plugin or adapter.

#### Initial scope

- Register OpenGHG-like record schemas.
- Map OpenGHG metadata conventions to ogcat metadata.
- Add parser/standardiser operation stubs.
- Add ZarrStore-compatible locator/storage adapter sketch.
- Provide read-only adapter that can expose ogcat records through an ObjectStore-like interface.
- Keep OpenGHG as an optional dependency.

#### Non-goals

- Full replacement of OpenGHG storage in P2.
- Large migration tooling.
- ObsPack export completion.

#### Acceptance criteria

- The plugin can be installed separately.
- It can register schemas and hooks.
- It can ingest or register at least one tiny OpenGHG-like NetCDF/Zarr fixture.
- It can return an object/path through a minimal ObjectStore-like adapter.

---

### Issue P2-04: Add a read-only OpenGHG ObjectStore adapter backed by ogcat

Labels: `priority:2`, `area:openghg`, `kind:feature`

#### Goal

Create a compatibility layer so existing OpenGHG code can read data found via ogcat.

#### Design

Extract or emulate the useful parts of the OpenGHG `ObjectStore` interface:

- search by metadata;
- retrieve object locator/path;
- return metadata;
- expose object references.

Start read-only. Writes should go through ogcat operations.

#### Acceptance criteria

- Adapter can be used in a tiny compatibility test.
- Adapter does not require ogcat core to import OpenGHG.
- Adapter has clear limitations documented.

---

### Issue P2-05: Add richer batch ingest with synchronous execution

Labels: `priority:2`, `area:batch`, `kind:feature`

#### Goal

Support parsing a pre-existing data collection without requiring async/server infrastructure.

#### Design

- Accept manifests or path globs.
- Plan batch first.
- Dry-run shows intended records/artifacts.
- Execute sequentially by default.
- Optional local thread/process executor can be added behind an execution interface, but do not require it.

#### CLI

```bash
ogcat ingest manifest.yaml --dry-run
ogcat ingest manifest.yaml --continue-on-error
ogcat ingest /data/**/*.nc --schema openghg.surface --extract-metadata
```

#### Acceptance criteria

- Batch ingest can resume or skip already-added files.
- Failures are logged per operation.
- Summary output includes success/failure counts.
- Batch operation does not corrupt catalog on partial failure.

---

### Issue P2-06: Build the mini BibDesk clone as a serious example

Labels: `priority:2`, `area:examples`, `kind:example`

#### Goal

Demonstrate that ogcat is useful outside OpenGHG.

#### Features

- Parse `.bib` and `.ris` files using optional bibliography parser dependencies.
- Create `paper` records.
- Attach PDFs and auxiliary files.
- Autofile attachments using a path template.
- Search by author/year/title/tag.
- Export a simple bibliography listing.
- Keep example small enough to run locally.

#### Candidate optional libraries to evaluate

- `pybtex`
- `bibtexparser`
- `rispy`

#### Acceptance criteria

- Example can be run from a clean checkout.
- It uses plugin/hook/schema features rather than special core code.
- It has a tutorial page.

---

### Issue P2-07: Build OpenGHG/Fluxie-style format preparation example

Labels: `priority:2`, `area:examples`, `kind:example`

#### Goal

Show how ogcat can simplify "data must be in a particular format" workflows.

#### Example workflow

- Register raw NetCDF/Zarr artifact.
- Extract metadata.
- Validate against an input schema.
- Run a transformation operation that writes a standardised artifact.
- Validate output.
- Search for datasets that are ready for Fluxie/OpenGHG-style use.

#### Acceptance criteria

- Uses tiny generated data.
- Keeps scientific domain logic in example/plugin code.
- Demonstrates provenance from raw to standardised record.

---

### Issue P2-08: Build inversion/ML experiment tracking example

Labels: `priority:2`, `area:examples`, `kind:example`

#### Goal

Demonstrate ogcat as a lightweight project catalog for runs, configs, outputs, scripts, and logs.

#### Record kinds

- `experiment`
- `run`
- `config`
- `output`
- `log`
- `script`
- `environment`

#### Features

- Register config files.
- Register SLURM scripts.
- Register log files.
- Register output datasets.
- Search runs by metadata.
- Link runs to input data records.
- Store path to external HPC output directory without moving data.

#### Acceptance criteria

- Example does not require actual HPC.
- Uses generated files.
- Demonstrates domain logic living outside core.

---

### Issue P2-09: Build EDGAR FTP/catalog front-end example

Labels: `priority:2`, `area:examples`, `kind:example`

#### Goal

Show ogcat as a searchable front-end to remote data that may not be downloaded immediately.

#### Features

- Catalog remote FTP/HTTP URLs as external locators.
- Store parsed metadata from filenames/directories.
- Allow user to search and then download/register selected files.
- Keep live network access optional; include a small static fixture manifest.

#### Acceptance criteria

- Works offline with a manifest fixture.
- Clearly distinguishes external locator from managed artifact.
- Demonstrates delayed materialisation.

---

### Issue P2-10: Build ICOS URI discovery example

Labels: `priority:2`, `area:examples`, `kind:example`

#### Goal

Show how ogcat can catalog external persistent URIs without downloading data.

#### Features

- Store ICOS metadata URI/access URI as external locators.
- Search by metadata.
- Provide operation hook for domain package to materialise data later.
- Keep ICOS-specific retrieval outside ogcat core.

#### Acceptance criteria

- Example uses small static metadata fixtures.
- No live credentials required.
- Demonstrates search/discovery rather than storage.

---

## Priority 3: scalable backends, server-managed transactions, indexing, and access control

Priority 3 is where ogcat moves from a robust local/shared-file catalog toward a service that can support multiple users more safely.

### P3 target outcomes

- SQLite backend for stronger local semantics.
- Postgres backend or server-managed catalog prototype.
- Real transaction management where backend supports it.
- Search indexes beyond TinyDB scans.
- Better permissions/access control.
- Worker execution for batch operations.
- More complete audit/provenance/event querying.

### P3 themes

#### P3-A: SQLite backend

SQLite is a natural stepping stone:

- still lightweight;
- single-file;
- real transactions;
- better indexing;
- no server required;
- easier migration path to SQLAlchemy/Postgres.

Consider storing flexible metadata as JSON and indexing selected fields.

#### P3-B: Postgres backend

Postgres should support:

- multi-user writes;
- row-level locking;
- JSONB metadata;
- migrations;
- stronger search;
- transaction-aware operation records;
- server deployment later.

Use SQLAlchemy or another mature layer only after the domain model is stable.

#### P3-C: Catalog server

A server can centralise:

- authentication;
- user identity;
- permissions;
- transaction boundaries;
- operation execution;
- logs;
- plugin loading;
- storage profile access.

Do not require server mode for simple users.

#### P3-D: Search/indexing

Add optional indexing:

- selected metadata fields;
- full-text search;
- artifact format/media type;
- temporal fields;
- geospatial/spatiotemporal fields later if needed by plugins.

Avoid making the core query language too ambitious. Plugins can expose domain-specific query helpers.

#### P3-E: Permissions/access control

Start with practical access control:

- catalog admin;
- writer;
- reader;
- operation runner;
- plugin manager.

At the record/artifact level:

- public/private/project visibility;
- read/write permission;
- external locator credentials handled by storage profile, not record metadata.

#### P3-F: Worker/batch execution

Add execution abstraction:

- local sequential executor;
- local process/thread executor;
- server worker;
- optional Celery/Dask/SLURM integration through plugins later.

Keep operation logs and journals consistent regardless of executor.

---

## Priority 4: enterprise-grade features and ecosystem interoperability

Priority 4 should remain directional until P1-P3 reveal real usage patterns.

### P4 target outcomes

- policy-based data lifecycle management;
- richer provenance graphs;
- publication/export workflows;
- UI or desktop/web front-end;
- multi-protocol access;
- stronger institutional integration.

### P4 inspirations

#### iRODS-like capabilities to borrow conceptually

Do not clone iRODS. Borrow concepts selectively:

- metadata-driven workflows;
- automated ingest;
- policy hooks;
- audit logs;
- storage tiering;
- indexing;
- publication workflows;
- provenance;
- multiple client protocols.

ogcat should be the lower-entry, Pythonic, project-friendly version for teams that do not want to deploy an enterprise data-management platform.

#### Interoperability targets

Evaluate later:

- RO-Crate for packaging metadata/provenance;
- DataLad/git-annex for dataset versioning and large-file references;
- Intake for data source descriptions;
- frictionless data packages for tabular data;
- OpenLineage-style event models for workflow lineage;
- S3-compatible object stores;
- institutional identity providers;
- iRODS bridges where sites already use iRODS.

---

## 5. Cross-cutting design decisions

### 5.1 Transactions and rollback

Use these terms carefully:

- **Unit of work**: ogcat-level operation boundary.
- **Journal**: durable record of operation state transitions.
- **Rollback action**: compensating action for a completed sub-step.
- **Transaction**: only use for real backend transactions where the backend supports them.

The [glossary](glossary.md) defines the shared vocabulary used by this plan and
the current API docs.

In TinyDB mode, say "transaction-like" or "recoverable operation", not ACID transaction.

### 5.2 Logging

Prefer catalog-level structured logs over per-user free-text logs.

P1 logs should be:

- JSONL;
- filterable;
- linked to operation IDs;
- suitable for support/debugging;
- safe enough not to dump secrets.

Later logs can be stored in SQL or a log service.

### 5.3 Metadata flexibility versus schema

Default stance:

- ogcat should accept flexible metadata;
- schemas are opt-in;
- schema validation should improve confidence, not block exploratory cataloging unless strict mode is requested;
- domain plugins should own domain schemas.

### 5.4 Hooks

Hooks are necessary, but dangerous if uncontrolled.

Rules:

- hook order is deterministic;
- hook failures are explicit;
- required versus optional hooks are distinguished;
- hooks are logged;
- hooks can participate in rollback;
- hooks should not mutate records invisibly unless the mutation is returned as a structured update.

### 5.5 Async and concurrency

Do not make the P1 core async.

Reasonable path:

- P1: synchronous operations; explicit operation boundaries; file locks for TinyDB.
- P2: batch operations and optional executor abstraction.
- P3: server/worker architecture if needed.
- P4: more advanced orchestration.

Avoid half-async APIs that make simple usage harder without solving real concurrency.

### 5.6 Human readability

Maintain human-readable catalog state where possible:

- JSON/JSONL for TinyDB and logs;
- clear paths;
- useful CLI output;
- metadata field inspection;
- no opaque binary catalog unless backend requires it.

### 5.7 Secrets

Never store secrets in catalog records.

Use:

- environment variables;
- storage profile references;
- credential providers;
- future server-side secrets management.

---

## 6. Documentation and examples plan

### 6.1 Documentation audiences

1. **Simple user**
   - wants to catalog files and search them.
   - needs quickstart.

2. **Scientific/data workflow user**
   - wants metadata extraction, validation, transformations, and provenance.
   - needs tutorials and examples.

3. **Plugin author**
   - wants schemas, hooks, operations, storage profiles.
   - needs reference docs.

4. **OpenGHG developer**
   - wants to understand how ogcat could replace storage logic.
   - needs migration/adaptor notes.

5. **Maintainer/admin**
   - wants logs, recovery, locks, diagnostics.
   - needs operations docs.

### 6.2 Suggested tutorial sequence

1. Create a catalog.
2. Add a file.
3. Add metadata.
4. Search.
5. Inspect fields.
6. Show artifact path.
7. Add data from memory.
8. Recover from a failed operation.
9. Validate a record with a schema.
10. Extract NetCDF metadata.
11. Add a plugin hook.
12. Run a batch ingest.
13. Use the OpenGHG plugin prototype.

### 6.3 Examples repository decision

Keep examples in the main repo for now if they are small and CI-friendly.

Consider a parallel examples repo later if:

- examples require large data;
- examples require heavy domain dependencies;
- examples need live services;
- examples become more like tutorials than tests.

### 6.4 RST plus Jupyter directive

A practical docs pattern:

- canonical runnable code lives in `examples/<name>/scripts/`;
- docs pages include short snippets and command transcripts;
- optional Jupyter execution is used for richer examples;
- keep notebooks out of the critical docs path until the docs build is stable.

---

## 7. Testing strategy

### 7.1 Principles

- Test behaviour and contracts, not every incidental representation.
- Use small generated fixtures.
- Keep core tests independent of heavy optional dependencies.
- Use optional markers for NetCDF/Zarr/OpenGHG examples.
- Reuse backend contract tests for TinyDB, SQLite, and Postgres later.
- Use failure injection for durability features.
- Keep examples as smoke tests, not as the main specification.

### 7.2 Test fixtures

Suggested fixtures:

- tiny text file;
- tiny JSON file;
- tiny generated NetCDF file;
- tiny generated Zarr group;
- fake bibliography file;
- fake EDGAR manifest;
- fake ICOS metadata record;
- fake OpenGHG-like data record;
- fake SLURM script/log/config/output directory.

### 7.3 CI stages

Initial:

```text
lint
unit tests
CLI tests
docs build
selected example smoke tests
```

Later:

```text
optional scientific tests
plugin tests
backend contract tests for SQLite/Postgres
integration tests
```

---

## 8. Suggested GitHub issue generation format

The sections named `Issue P*-NN` are intended to be machine-splittable.

A simple script can:

1. split this document on headings matching `^### Issue `;
2. use the heading as the issue title;
3. parse the `Labels:` line;
4. use the rest of the section as the body;
5. call `gh issue create`.

Suggested labels:

```text
priority:1
priority:2
priority:3
priority:4
area:architecture
area:model
area:backend
area:durability
area:logging
area:storage
area:schema
area:plugins
area:metadata
area:cli
area:concurrency
area:examples
area:docs
area:packaging
area:testing
area:openghg
area:operations
area:batch
kind:feature
kind:docs
kind:maintenance
kind:example
```

---

## 9. Recommended immediate implementation order

For Priority 1, do this order:

1. P1-01 architecture decision record.
2. P1-02 record/artifact/locator models.
3. P1-03 backend protocol.
4. P1-10 CLI/API parity for `info` and `fields` if these are already nearly implemented.
5. P1-04 unit-of-work and journal.
6. P1-05 structured logging.
7. P1-06 fsspec storage primitives.
8. P1-07 schema validation.
9. P1-08 hook engine.
10. P1-09 metadata extraction.
11. P1-11 TinyDB locking and concurrency caveats.
12. P1-12 examples.
13. P1-13 docs.
14. P1-14 packaging extras.
15. P1-15 broad testing improvements.

Rationale:

- models and backend boundaries should come before durability features;
- unit-of-work and logging should come before complicated hooks;
- fsspec storage should integrate with unit-of-work from the start;
- docs and examples should be written once the basic APIs settle, but not postponed indefinitely.

---

## 10. Open questions

These should be resolved during P1/P2, not before starting.

1. Should fsspec be a hard dependency or an optional extra?
2. Should Pydantic models be public API objects or internal validation only?
3. What is the canonical on-disk TinyDB/catalog layout?
4. Should `delete` mean tombstone by default?
5. How much metadata field indexing should TinyDB mode attempt?
6. Should schemas be Pydantic-only initially, or should JSON Schema be a first-class input?
7. How much plugin capability should exist before OpenGHG integration starts?
8. Should examples stay in the main repo or move to an examples repo later?
9. What is the smallest useful ObjectStore-like interface to extract from OpenGHG?
10. How should user identity be determined in local/HPC use: OS user, explicit config, environment variable, or server identity later?
11. How should storage profiles handle credentials without leaking secrets into records/logs?
12. Should operation journals live in the catalog directory, next to the TinyDB file, or in a platform-specific state directory?
13. How should record IDs be generated: UUID, ULID, content-derived, slug-plus-suffix, or backend-specific?
14. Should path templates be pure format strings, small Python callables, or plugin-defined objects?
15. Should example data include generated fixtures only, or small checked-in binary files where useful?

---

## 11. Compact roadmap

### Priority 1: robust local catalog

- explicit models;
- backend protocol;
- TinyDB backend hardening;
- unit-of-work/journal;
- structured logging;
- fsspec storage;
- schema validation;
- hook points;
- generic metadata extraction;
- better CLI/API parity;
- examples and docs;
- targeted tests.

### Priority 2: plugins and domain workflows

- plugin discovery;
- operation pipelines;
- OpenGHG plugin prototype;
- read-only ObjectStore adapter;
- batch ingest;
- mini BibDesk example;
- OpenGHG/Fluxie-style example;
- inversion/ML tracking example;
- EDGAR and ICOS discovery examples.

### Priority 3: scale and multi-user safety

- SQLite backend;
- Postgres backend;
- server mode;
- transaction-aware operations;
- indexing/search;
- permissions;
- worker execution;
- richer audit/provenance queries.

### Priority 4: ecosystem and enterprise features

- policy-driven lifecycle;
- provenance graphs;
- publication/export workflows;
- UI/front-end;
- iRODS-style interoperability where useful;
- RO-Crate/DataLad/Intake-style bridges;
- institutional deployment patterns.

---

## 12. Definition of success

ogcat succeeds if:

- a user can create a useful catalog in minutes;
- a maintainer can debug failures from catalog-level logs;
- failed writes do not leave misleading active records;
- metadata remains flexible but can be validated when needed;
- domain packages can add schemas, parsers, transformations, and exports without patching ogcat core;
- OpenGHG can progressively move storage/object-store concerns into ogcat-backed adapters;
- future backends can implement stronger transactions and concurrency without changing the conceptual API;
- examples are good enough to explain the project to skeptical users.
