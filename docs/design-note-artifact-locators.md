# Design Note: Artifact Locators

This note describes the first architectural step from "catalogued local files" toward
"catalogued artifacts".

## Chosen Shape

`CatalogRecord` now has two small additions:

- `record_type`: a short string describing what the record represents
- `locator`: a small nested block describing how that record is located

The locator shape is intentionally minimal:

```json
{
  "kind": "path",
  "value": "/abs/path/to/object",
  "relative_path": "data/objects/ab/abcdef.nc"
}
```

That is enough to support today's managed local files while leaving room for later locator kinds
such as URIs.

## Backward Compatibility

Managed local files remain the current default:

- `Catalog.add_file(...)` still copies or moves a local file into the catalog
- `stored_abspath` and `stored_relpath` are still written for managed local files
- `Catalog.path(record_id)` still works for managed local files

Older JSON records that do not yet include `record_type` or `locator` are upgraded on read by
deriving a path locator from `stored_abspath` and `stored_relpath`.

## Why Keep `stored_abspath` and `stored_relpath` For Now

For this pass, the locator becomes the more general concept, but the stored path fields remain as
compatibility shims for the current MVP. This keeps the code and CLI readable while avoiding a hard
break in record shape.

## New API Surface

`Catalog.add_file(...)` and `Catalog.add_artifact(...)` now share the same internal add-operation
lifecycle. `add_file(...)` is the local-file specialisation: it resolves a path locator, copies or
moves the source file, extracts generic metadata, and writes a record.

`add_artifact(...)` does not perform file operations. It registers a record with an explicit
`record_type` and `locator`. That keeps the current managed-file path simple while creating a small
entry point for future non-file records.

Artifact ids remain present on records and in CLI lookup, but they are now repository-generated.
With the TinyDB backend, these ids follow TinyDB's document-id sequence. `add_artifact(...)`
and `add_artifacts(...)` do not accept caller-supplied record ids.

## Intentionally Out Of Scope

This pass does not implement:

- reader APIs
- manager bindings
- full URI support
- managed directory-store ingest
- transform target allocation workflows

Those are future layers that can now be added on top of a record model that is no longer locked to
copied or moved local files only.
