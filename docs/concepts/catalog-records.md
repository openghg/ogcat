# Catalog records

A catalog record is the logical catalog entry used for search, schema
validation, and user metadata. Every record contains a fixed set of reserved
fields, zero or more artifact descriptors, and three metadata namespaces.
Ordinary records with a physical or referenced data artifact have a
``data_artifact`` descriptor.

For compatibility, the top-level ``locator`` remains the shortcut to the
record's data artifact. Code that calls ``record.locator`` or ``record.path()``
continues to work for ordinary single-artifact records.

## Reserved fields

| Field | Description |
|-------|-------------|
| ``id`` | Stable string identifier assigned at ingest time. |
| ``catalog`` | Name of the catalog that owns the record. |
| ``record_type`` | Logical schema/search type, e.g. ``managed_file`` or ``external_reference``. |
| ``locator`` | Compatibility shortcut to the data artifact locator (see [Locators and storage](locators-and-storage.md)). |
| ``artifacts`` | Inline descriptors for the data artifact plus optional auxiliary artifacts, view links, manifests, previews, logs, or derived artifacts. |
| ``storage_mode`` | How the artifact was stored, e.g. ``copy``, ``move``, or ``external``. |
| ``status`` | Record lifecycle status. ``active`` records are returned by normal search; ``deleted`` records are tombstones. |
| ``lifecycle_metadata`` | Internal lifecycle metadata such as delete/restore operation ids and timestamps. |
| ``original_filename`` | Source filename at ingest time. |
| ``suffixes`` | File suffix list derived from the source path. |
| ``time_added`` | ISO 8601 timestamp when the record was created. |

## Metadata namespaces

``user_metadata``
:   Key–value pairs supplied by the caller at ingest time.  Any
    JSON-compatible value is accepted. Common Python values such as
    `pathlib.Path`, `datetime.date`, `datetime.datetime`, tuples, sets, and
    NumPy scalar-like values are normalized before the record is validated and
    stored. This is the primary place to store domain metadata such as species,
    year, or instrument.

``derived_metadata``
:   Metadata added automatically during ingest by extractors and hooks.  For
    netCDF files this includes dimension names and sizes when ``xarray`` is
    installed. Derived metadata is normalized with the same JSON-compatible
    rules before storage. Do not rely on derived metadata being present for
    every file type. New records include a cheap
    ``derived_metadata.classification`` namespace with normalized artifact
    fields such as ``artifact_kind``, ``format``, ``archive_format``, and
    ``inner_format``. This classification is based on locators, path suffixes,
    local directory shape, and safe archive member names; it is separate from
    content metadata extraction.

Classification uses this initial vocabulary:

- ``artifact_kind``: ``file``, ``directory``, ``collection``, ``zarr_store``,
  ``archive``, ``remote_resource``, or ``opaque``.
- ``format``: ``collection``, ``netcdf``, ``zarr``, ``zip``, ``gzip``,
  ``tar``, ``text``, or ``unknown``.
- ``archive_format``: ``zip``, ``gzip``, or ``tar`` when the artifact is an
  archive or compressed file.
- ``inner_format``: the safely inferred inner format for simple suffix chains
  such as ``.nc.gz`` or local zip archives with one file.
- ``collection_pattern``: relative member pattern for explicit collection
  artifacts.
- ``member_format``: caller-supplied or suffix-inferred member format for
  collection artifacts.
- ``member_suffixes``: expected member suffix list for collection artifacts.
- ``reader_hint``: optional downstream reader hint such as
  ``xarray.open_mfdataset``.

``format`` is a normalized class, not the literal suffix. Text-like suffixes
such as ``.csv``, ``.json``, ``.md``, ``.tsv``, and ``.txt`` are intentionally
classified as the broad ``text`` format until ogcat needs a more detailed text
subtype vocabulary. Use the classification ``suffixes`` field or the record's
top-level ``suffixes`` field when you need to search or display the literal
suffix list.

Classification metadata remains in ``derived_metadata`` for now. It is not
automatically mirrored into artifact facets in the first claim/facet schema
slice, so existing classification search behavior and catalog compatibility are
preserved.

``naming_metadata``
:   Internal metadata used to evaluate directory and filename templates.  You
    do not normally need to read or set this directly. Fields such as
    ``artifact_uuid`` are storage-planning details and are not public naming
    template inputs; use explicit user metadata such as ``dataset_id`` for
    domain identifiers that should appear in human-readable paths.

## Artifact descriptors

``artifacts`` is a JSON-compatible list owned by the record. The
``data_artifact`` descriptor is the source for the top-level ``record.locator``
compatibility value. Existing locator-only records are upgraded on read by
synthesizing a ``data_artifact`` descriptor from the stored locator.

The first descriptor shape is deliberately small:

```json
{
  "id": "data",
  "role": "data_artifact",
  "locator": {"kind": "path", "value": "/abs/path/data.nc", "relative_path": "data/objects/ab/data.nc"},
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

Current first-slice roles are ``data_artifact``, ``auxiliary_artifact``,
``view_link``, ``manifest``, ``preview``, ``log``, and ``derived_artifact``.
Descriptor roles are stored as strings so future ADR or plugin-owned roles can
be persisted before their lifecycle behavior exists.

``claims`` describe artifact facts that future readers, writers, and converters
can use for dispatch. Core recognizes the claim kinds ``data_type``,
``representation``, and ``interface``. The vocabulary of claim names remains
open and namespaced. For example, one artifact can carry an ``interface`` claim
named ``bytes`` in the ``ogcat.core`` namespace, a ``data_type`` claim named
``netcdf`` in the ``org.unidata`` namespace, and an ``interface`` claim named
``xarray-dataset`` in the ``pydata.xarray`` namespace without importing any of
those optional reader libraries.

``facets`` describe structured facts such as suffixes, size, modification time,
checksums, archive member summaries, manifests, validation results, or
plugin-specific details. Claims and facets always normalize to this envelope:
``kind``, ``name``, ``namespace``, ``version``, ``evidence``, ``confidence``,
and ``metadata``. ``metadata`` is a JSON-compatible dictionary for the
structured payload.

The first evidence and confidence vocabulary is ``declared``, ``inferred``,
``probed``, ``validated``, ``stale``, and ``failed``. Suffix-only detection
should be recorded as inferred, for example with
``metadata={"source": "suffix", "suffixes": [".nc"]}``, not as validated
truth. Missing namespace, version, evidence, confidence, and metadata fields in
older raw dict payloads are filled with defaults when records are read. Invalid
claim and facet shapes fail during descriptor or record construction, including
repository load.

Claims and facets are not dispatch keys in this slice. Reader/open behavior and
reader/writer/converter registries are deferred.

A directory-backed artifact is not automatically a collection. For example, a
managed ``.zarr`` directory added with ``add_file()`` is one data artifact with
a directory representation and Zarr data type. A directory of ``.nc`` files is a
collection only when collection claims/facets such as member pattern, member
format, and member suffixes are explicitly attached. See
[Artifact Claims And Facets](../design-note-artifact-claims-and-facets.md) for
worked examples covering Zarr stores, NetCDF collections, CSV-like tables,
single NetCDF files, and grouped NetCDF/HDF5 files.

## Searching across namespaces

Normal search returns only active records. ``Catalog.delete(id)`` tombstones a
record by setting its status to ``deleted`` while keeping locators and artifact
descriptors attached for audit and restore workflows. Pass
``include_deleted=True`` to include tombstones in search results, or
``only_deleted=True`` to inspect just tombstoned records. Direct id lookup with
``Catalog.get(id)`` still returns tombstoned records.

When you search with an unqualified field name such as ``species``, ogcat
looks in this order:

1. top-level record fields (``id``, ``record_type``, …)
2. ``user_metadata``
3. ``derived_metadata``

Use an explicit dotted path to target a specific namespace:
``user_metadata.species``, ``derived_metadata.netcdf.dims.time``, or the
short aliases ``user.species`` and ``derived.netcdf.dims.time``.

Selected classification fields also have a flattened fallback after ordinary
``derived_metadata`` lookup, so searches such as ``where={"format": "zip"}``,
``where={"artifact_kind": "zarr_store"}``, and
``where={"artifact_kind": "collection", "member_format": "netcdf"}`` work for
classified records.
Use dotted paths such as
``derived_metadata.classification.inner_format`` when you need to bypass normal
namespace precedence.

## Python API

Records are returned as ``CatalogRecord`` instances.

```python
record = catalog.add_file(path, metadata={"species": "CO2"})
print(record.id)
print(record.record_type)          # "managed_file"
print(record.user_metadata)        # {"species": "CO2", ...}
print(record.path())               # stored Path
print(record.artifacts[0].role)    # "data_artifact"
```

Trash-style lifecycle operations are available from the catalog facade:

```python
deleted = catalog.delete(record.id, reason="superseded")
assert deleted.status == "deleted"
assert catalog.search(where={"species": "CO2"}) == []
assert catalog.get(record.id) is not None

restored = catalog.restore(record.id)
assert restored.status == "active"

catalog.delete(record.id)
catalog.purge(record.id)  # permanent; removes managed catalog-local artifacts first
```
