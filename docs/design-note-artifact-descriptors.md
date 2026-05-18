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
  "claims": [
    {
      "kind": "interface",
      "name": "bytes",
      "namespace": "ogcat.core",
      "version": "1",
      "evidence": "validated",
      "confidence": "validated",
      "metadata": {}
    }
  ],
  "facets": [
    {
      "kind": "suffix",
      "name": "suffixes",
      "namespace": "ogcat.core",
      "version": "1",
      "evidence": "inferred",
      "confidence": "inferred",
      "metadata": {"suffixes": [".nc"]}
    }
  ]
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

## Claims And Facets

Claims and facets now have a small explicit JSON-compatible envelope. Claims
describe data type, representation, or interface facts. Facets describe
structured facts such as suffixes, size, mtime, checksum, archive member
summaries, manifests, or plugin-specific validation results. See
[Artifact Claims And Facets](design-note-artifact-claims-and-facets.md) for the
field definitions and compatibility rules.

The claim envelope is generic and open rather than a closed set of scientific
formats. Core recognizes ``data_type``, ``representation``, and ``interface``
claim kinds, but plugins may add namespaced claim and facet names without
changing core. ``ogcat.core`` is reserved for core names.

Evidence and confidence use the first ADR vocabulary: ``declared``,
``inferred``, ``probed``, ``validated``, ``stale``, and ``failed``. Suffix-only
detection should be represented as inferred evidence, not validated truth.

Existing raw dict claims and facets remain readable. Missing namespace,
version, evidence, confidence, and metadata are filled with defaults when the
shape is otherwise clear. Older facet payloads with extra top-level fields have
those fields folded into ``metadata``.

## Current Limits

`record_type` remains schema and search metadata. It is not an I/O dispatch key.

Only one current `data_artifact` descriptor is accepted in this slice. Replica
leadership remains deferred to later `leader` or `write_leader` fields instead
of overloading `primary`.

`claims` and `facets` are schema and validation metadata only in this slice.
This change does not implement reader/open behavior, the reader/writer/converter
registry, replicas, cache/archive policy, mount resolution, permissions, locks,
or Intake integration.

`derived_metadata.classification` remains the current home for cheap inferred
classification fields such as `artifact_kind`, `format`, `archive_format`,
`inner_format`, `collection_pattern`, `member_format`, `member_suffixes`, and
`reader_hint`. This slice does not replace that namespace or automatically
mirror those fields into facets.
