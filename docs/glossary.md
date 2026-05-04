# Glossary

This page defines the terms used across the current API docs and the
longer-term implementation plan. The plan includes future concepts; the
definitions here distinguish the current meaning from planned extensions.

Catalog
: An on-disk collection of catalog configuration, repository state, managed
  artifacts, and records. Today this is a local directory backed by TinyDB.

Record
: The catalog entry describing one catalogued item. In the current API this is
  a `CatalogRecord` with top-level fields, a locator, user metadata, derived
  metadata, and naming metadata.

Artifact
: The file, directory, URI, or opaque external reference being catalogued. The
  current data model stores one primary locator per record; the long-term plan
  discusses multiple artifact descriptors per record.

Locator
: A serialisable description of where an artifact lives. Current locator kinds
  include path-backed, URI-backed, and opaque references.

Managed file
: A local artifact copied or moved into the catalog-managed files area by
  `Catalog.add_file`.

Reference record
: A record added with `Catalog.add_artifact` whose artifact may be external,
  already materialised, written by an artifact writer, or represented by an
  opaque locator.

Metadata
: JSON-compatible descriptive data attached to records. `user_metadata` comes
  from callers, `derived_metadata` comes from hooks or writers, and
  `naming_metadata` is used for storage template rendering.

Schema
: A `RecordSchema` describing expected metadata fields and optional storage
  naming templates for a record type. Schema validation is intentionally
  permissive unless fields are marked required or strict validation is used.

Hook
: A structural protocol method implemented by a plugin object and dispatched by
  `HookManager` during catalog operations.

Plugin
: A group of hooks or related extension objects registered with a catalog,
  usually through `PluginRegistry`.

Operation context
: The mutable `OperationContext` object passed to hooks and artifact writers.
  It carries source information, planned locators, metadata, warnings, and the
  rollback registrar for the active operation.

Artifact writer
: Any object satisfying the `ArtifactWriter` protocol. Writers materialise
  artifact data from an `OperationSource` into a target `ArtifactLocator`.

Operation source
: The `OperationSource` description passed to artifact writers. It can carry a
  local path, non-path descriptor, source metadata, or in-memory payload.

Unit of work
: The current best-effort transaction helper. It stages catalog changes,
  registers rollback actions, commits on success, and runs cleanup callbacks on
  failure. It is not an ACID database transaction.

Journal
: A planned durable record of operation state transitions. The current
  implementation has rollback actions, but not yet a durable operation journal.

Event log
: A planned catalog-level structured log for operation debugging and audit
  trails. This is distinct from the future journal, which tracks recoverable
  operation state.

Storage profile
: A planned named configuration for resolving non-local storage adapters and
  credentials without storing secrets in records.
