# Design Note: Artifact Descriptors

This note records the first implementation slice from ADR 0002's virtual
artifact filesystem model.

## Chosen Shape

`CatalogRecord` remains the logical catalog entry. It now has an inline
`artifacts` list whose items describe concrete or virtual artifacts associated
with the record. Records without a meaningful locator may have an empty list.

The first descriptor shape is intentionally small and JSON-compatible:

```json
{
  "id": "data",
  "role": "data_artifact",
  "locator": {
    "kind": "path",
    "value": "/abs/path/to/data.nc",
    "relative_path": "data/objects/ab/data.nc"
  },
  "state": "available",
  "relationship": {},
  "claims": [],
  "facets": []
}
```

The current first-slice roles are `data_artifact`, `auxiliary_artifact`,
`view_link`, `manifest`, `preview`, `log`, and `derived_artifact`. Roles are
stored as strings, not enforced as a closed enum, so future ADR roles such as
`replica`, `cache_copy`, and `archive_copy` can be persisted before their
behavior is implemented.

## Compatibility Locator

`record.locator` is still persisted and exposed as the compatibility shortcut
to the first `data_artifact` descriptor's locator. Existing code that passes
`record.locator.value` or `record.path()` to ordinary libraries keeps working.

When an older catalog record has no `artifacts` list, ogcat synthesizes a
`data_artifact` descriptor from the stored `locator`, or from legacy
`stored_abspath` and `stored_relpath` fields. Records without a meaningful
locator remain artifact-less until a real artifact is assigned.

## Why Inline

TinyDB stores catalog records as whole JSON documents, and this slice does not
introduce independent artifact CRUD, locks, permissions, replicas, or a reader
registry. Inline descriptors keep artifact membership transactionally tied to
the record with the current backend and avoid a migration to a second registry
before there is a real lifecycle that needs it.

Namespaced metadata was rejected for this slice because artifacts are now part
of the durable public model, not derived metadata or a plugin-specific
experiment.

## Current Limits

`record_type` remains schema and search metadata. It is not an I/O dispatch key.

Only one current `data_artifact` descriptor is accepted in this slice. Replica
leadership remains deferred to later `leader` or `write_leader` fields instead
of overloading `primary`.

`claims` and `facets` are placeholders for later typed reader, writer, and
converter work. This change does not implement replicas, cache/archive policy,
mount resolution, permissions, locks, or Intake integration.
