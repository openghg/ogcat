# Current Architecture Report

This report describes the architecture after the #84 refactor train through
#92. It is a snapshot, not a claim that the structure is final. The main design
direction is to keep `Catalog`, `CatalogRecordSet`, and the CLI as public
presentation surfaces while moving orchestration, domain policy, and persistence
behind clearer internal interfaces.

## Layer Map

```text
flowchart TD
  CLI["CLI presentation"]
  Catalog["Catalog facade"]
  RecordSet["CatalogRecordSet"]
  App["CatalogApplication"]
  Runner["AddOperationRunner"]
  Hooks["HookManager / HookDispatcher"]
  UOW["UnitOfWork"]
  Planning["Storage planning / naming / classification"]
  Materialization["Materialization targets / writers"]
  Secondaries["Secondary artifact operations"]
  Repository["CatalogRepository protocol"]
  TinyDB["TinyDbCatalogRepository"]
  Storage["Storage adapters"]
  Audit["Audit sink"]

  CLI --> Catalog
  Catalog --> RecordSet
  Catalog --> App
  App --> Runner
  Runner --> Hooks
  Runner --> UOW
  Runner --> Planning
  Runner --> Materialization
  Runner --> Secondaries
  Runner --> Repository
  Runner --> Audit
  Materialization --> Storage
  Repository --> TinyDB
```

| Layer | Components | Main responsibility |
|-------|------------|---------------------|
| Presentation/API | `Catalog`, `CatalogRecordSet`, CLI | Public Python and command-line workflows, argument coercion, user-facing compatibility |
| Application/orchestration | `CatalogApplication`, `AddOperationRunner`, operation requests/services, hooks, unit of work | Operation sequencing, rollback boundaries, hook dispatch, audit coordination |
| Domain/policy | naming, storage planning, materialization intent, classification, validation, replica planning, secondary artifacts | Catalog rules that are independent of the storage backend |
| Data/infrastructure | repositories, TinyDB implementation, storage adapters, writers, audit sink | Persistence and filesystem or fsspec side effects |

## Primary Add Flow

```text
sequenceDiagram
  participant User
  participant Catalog
  participant App as CatalogApplication
  participant Runner as AddOperationRunner
  participant Hooks
  participant Planner as Storage planner
  participant Writer
  participant Repo as Repository
  participant Secondary as Secondary artifacts
  participant Audit

  User->>Catalog: add_file/add_artifact
  Catalog->>Catalog: validate public arguments
  Catalog->>App: delegate request construction
  App->>Runner: AddOperationRequest
  Runner->>Hooks: before/after metadata validation
  Runner->>Planner: plan primary locator
  Runner->>Hooks: resolve locator
  Runner->>Writer: write/copy/move if required
  Runner->>Hooks: extract metadata
  Runner->>Repo: stage CatalogRecord
  Runner->>Secondary: materialize ordered follow-up artifacts
  Runner->>Hooks: after_record_write / before_commit / after_commit
  Runner->>Audit: operation events
```

## CRC Cards

| Class or module | Role | Collaborators |
|-----------------|------|---------------|
| `Catalog` | Public facade for create/open, add, reference, collection, search, metadata update, path resolution, spec updates, and audit access. | `CatalogSpec`, `CatalogApplication`, `CatalogRepository`, `HookManager`, `JsonlAuditSink`, `UnitOfWork`, `CatalogRecordSet`. |
| `CatalogRecordSet` | Public result container for searches and record lists, including display helpers and optional dataframe conversion. | `CatalogRecord`, search output, CLI. |
| CLI (`cli.py`) | Command-line presentation layer: parse arguments, call `Catalog`, render tables and machine-readable output. | `Catalog`, `CatalogRecordSet`, `SearchQuery`, `RecordSchema`. |
| `CatalogSpec` / `RecordSchema` | Self-describing catalog configuration and schema defaults stored in `catalog.json`. | `Catalog`, validation, CLI. |
| `CatalogRecord` / `ArtifactLocator` | Persisted record model and locator abstraction. Keeps compatibility path fields while the locator model becomes primary. | repository, storage planning, search, record sets, validation. |
| `CatalogApplication` | Internal application service that turns public add requests into runner requests, chooses copy/move writers, and schedules secondary artifacts. | `Catalog`, `AddOperationRunner`, storage planning, writers, `TemplateLinkSecondaryArtifact`. |
| `AddOperationRunner` | Lifecycle coordinator for add operations: validation, hooks, storage planning, writing, record staging, secondary artifacts, commit, rollback, audit. | `AddOperationRequest`, `OperationServices`, `HookDispatcher`, `UnitOfWork`, writers, repository, audit sink. |
| `OperationServices` | Bundle of runner dependencies and callbacks. | `Catalog`, hooks, repository, validation, audit. |
| `OperationContext` | Mutable operation-scoped context shared with hooks and writers. | hooks, writers, runner, storage plans, rollback registrar. |
| Hook protocols / `HookDispatcher` | Structural plugin extension points for lifecycle phases. | `OperationContext`, `ValidationReport`, `HookManager`, runner. |
| `UnitOfWork` | Best-effort rollback coordinator for staged record writes and side-effect cleanup. | repository, writers, secondary artifacts, hooks. |
| `CatalogRepository` | Backend protocol for record persistence and search. | `Catalog`, runner, `TinyDbCatalogRepository`, `CatalogRecord`. |
| `TinyDbCatalogRepository` | Current TinyDB-backed repository implementation. | TinyDB, search predicates, `CatalogRecord`. |
| `SearchQuery` and search helpers | Backend-neutral search terms and in-memory matching semantics. | repository, `CatalogRecordSet`, CLI. |
| Naming module | Template rendering, generated naming context, public versus internal template-field policy. | storage planning, template replicas, replica views. |
| Storage planning | Primary locator policy for UUID, template, and user-provided targets. | naming, locators, storage roots, `StoragePlan`. |
| Materialization module | Internal representation of how planned targets become write targets. | operation runner, storage plans, writers. |
| Storage adapters | Local and fsspec-like target operations. | writers, storage planning, artifact locators. |
| Writers | Copy, move, unzip, function, and memory/path writer helpers. | `OperationContext`, `OperationSource`, `ArtifactLocator`, storage helpers. |
| Classification | Cheap artifact and collection classification metadata. | `Catalog.add_collection`, record metadata, docs/examples. |
| `SecondaryArtifactOperation` | Ordered post-record operations inside the add rollback boundary. | runner, `UnitOfWork`, `CatalogRecord`. |
| `TemplateLinkSecondaryArtifact` / `template_replicas` | Required human-readable symlink for UUID primary managed files. | naming, transactions, replica link helpers, runner. |
| `replicas` | Generated local symlink view planning and application for existing records. | `Catalog.plan_view`, `CatalogRecord`, replica context, link helpers. |
| Audit sink and events | Structured operation logging and CLI failure correlation. | runner, `Catalog`, CLI, `UnitOfWork`. |

## Data Model Sketch

```text
classDiagram
  class CatalogSpec {
    catalog_name
    default_schema
    record_schemas
  }
  class RecordSchema {
    directory_template
    filename_template
    metadata_fields
  }
  class CatalogRecord {
    id
    catalog
    record_type
    locator
    user_metadata
    derived_metadata
    naming_metadata
  }
  class ArtifactLocator {
    kind
    value
    relative_path
  }
  class CatalogRepository {
    <<protocol>>
    insert(record)
    update(record)
    search(query)
  }

  CatalogSpec --> RecordSchema
  CatalogRecord --> ArtifactLocator
  CatalogRepository --> CatalogRecord
```

## Current Responsibility Hotspots

### `Catalog`

`Catalog` has moved toward a facade, but it still has several reasons to
change:

- public API argument validation and coercion
- catalog creation/opening and spec serialization
- schema and metadata mutation APIs
- audit event adaptation
- repository and runner dependency construction
- search and record-set construction
- compatibility helpers such as path resolution

This is the largest single-responsibility concern. The direction is correct,
but more extraction would make changes less risky.

### `CatalogApplication`

`CatalogApplication` is the right kind of orchestration boundary, but it still
depends on a concrete `Catalog` object and reaches through catalog helper
methods. A smaller services protocol would better satisfy dependency inversion.

### `OperationContext`

`OperationContext` is intentionally broad because hooks and writers need a
shared mutable object. That makes it flexible, but phase-specific access is not
expressed in the type system. Hook authors can see fields that may be invalid
or not meaningful in the current phase.

### Storage Planning

Storage planning is function-based and currently branches over UUID, template,
URL-path, local-root, and user-provided placement. This is readable today, but
new placement policies may make the module harder to change without strategy
objects or a planner protocol.

### Metadata And Naming

User metadata, derived metadata, classification metadata, naming metadata, and
top-level record fields are now distinct, but several modules still need to
know parts of the resolution policy. The #92 field policy helps by separating
human-readable naming fields from internal identifiers, but naming remains a
central domain rule that deserves careful tests.

## SOLID Review

| Principle | Current state |
|-----------|---------------|
| Single responsibility | Improving. `AddOperationRunner`, storage planning, secondary artifacts, and repository are now clearer. `Catalog` and `OperationContext` remain broad. |
| Open/closed | Mixed. Hook protocols, writer protocols, repository protocol, and storage adapters support extension. Storage placement still uses branching in functions. |
| Liskov substitution | Good where protocols are explicit: repository, hooks, writers, secondary artifact operations. Concrete code should keep depending on those protocols where possible. |
| Interface segregation | Good for hook phase protocols and repository. Less strong for `OperationContext` and `CatalogApplication`, which expose broad collaborators. |
| Dependency inversion | Repository is the strongest example. `CatalogApplication` depending on concrete `Catalog` is the main remaining inversion gap. |

## Suggested Improvements

1. Split `Catalog` internals into smaller services:
   schema/spec service, metadata update service, audit adapter, and operation
   service factory.

2. Replace `CatalogApplication(catalog: Catalog)` with explicit service
   protocols for the operations it needs. That would keep the application layer
   from depending on facade private methods.

3. Introduce storage planner strategies before adding more placement policies.
   `UuidPrimaryPlanner`, `TemplatePrimaryPlanner`, and `UserProvidedPlanner`
   could share a protocol while keeping current function wrappers for
   compatibility.

4. Consider phase-specific hook context views. The runtime can keep one mutable
   `OperationContext`, but protocols could expose narrower read/write surfaces
   for validation, locator resolution, writing, and post-write hooks.

5. Keep collection semantics above storage adapters. Storage targets should
   remain file-like or directory-like; collection behavior should stay in
   domain policy and classification metadata.

6. Move CLI display formatting into helper modules if command complexity grows.
   The CLI should remain presentation logic over the Python API, not a second
   orchestration layer.

7. Keep interface tests close to the owning module. Public `Catalog` tests
   should cover behavior smoke/regression cases; lower-level behavior should be
   asserted against storage planners, materializers, runners, repositories, and
   secondary artifact interfaces.
