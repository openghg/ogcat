# Design Note: Artifact Claims And Facets

This note records the first claim and facet schema slice from ADR 0002. It is
limited to JSON-compatible persistence, validation, and documentation. It does
not implement reader dispatch, writer registration, converters, or optional
scientific readers.

## Chosen Shape

Artifact claims use one open envelope with a ``kind`` field:

```json
{
  "kind": "interface",
  "name": "bytes",
  "namespace": "ogcat.core",
  "version": "1",
  "evidence": "validated",
  "confidence": "validated",
  "metadata": {}
}
```

Core recognizes three standard claim kinds from ADR 0002:

- ``data_type`` for source or external forms such as NetCDF, CSV, service, or
  collection pattern.
- ``representation`` for storage or encoding shapes such as file, directory,
  archive, text, or Zarr store.
- ``interface`` for access contracts such as bytes, text, directory listing,
  archive members, table, xarray dataset, manifest, or collection.

The kind vocabulary is intentionally open. Plugins may persist their own claim
kinds, names, and metadata without changing core schema. The Python API exposes
``ArtifactClaim`` plus small convenience constructors: ``DataTypeClaim``,
``RepresentationClaim``/``Representation``, and ``InterfaceClaim``.

Facets use the same envelope, but they describe structured facts rather than
type or interface claims:

```json
{
  "kind": "stat",
  "name": "size",
  "namespace": "ogcat.core",
  "version": "1",
  "evidence": "probed",
  "confidence": "validated",
  "metadata": {"bytes": 12345}
}
```

The Python API exposes ``ArtifactFacet`` and the alias ``Facet``.

## Required Fields

Normalized claims and facets always contain:

- ``kind``: non-empty category string.
- ``name``: non-empty namespace-local name.
- ``namespace``: non-empty owner namespace, defaulting to ``ogcat.core``.
- ``version``: non-empty schema version string, defaulting to ``1``.
- ``evidence``: one supported vocabulary term.
- ``confidence``: one supported vocabulary term, defaulting to ``evidence``.
- ``metadata``: JSON-compatible dictionary for structured details.

Existing artifact descriptors remain readable. Raw dict claims with missing
``namespace``, ``version``, ``evidence``, ``confidence``, or ``metadata`` are
normalized with defaults. Raw dict facets may also omit ``name``; in that case
``name`` defaults to ``kind`` so earlier payloads such as
``{"kind": "image", "format": "png"}`` remain readable. Unknown top-level
fields are folded into ``metadata`` during normalization.

Claims still require both ``kind`` and ``name``. Facets still require ``kind``.
Required identifier fields must be strings; ``None``, non-string values, and
empty strings are rejected. Invalid evidence, confidence, metadata, list, or
item shapes raise ``ValueError`` or ``TypeError`` during ``ArtifactDescriptor``
and ``CatalogRecord`` construction, including repository load.

## Evidence And Confidence

The first vocabulary is:

- ``declared``: supplied by caller, writer, plugin, or source metadata.
- ``inferred``: cheap inference such as locator text, suffix, or directory
  shape.
- ``probed``: inspected cheaply without proving domain validity.
- ``validated``: checked by a validator or reader-specific capability.
- ``stale``: previously true or declared evidence that may no longer describe
  the artifact.
- ``failed``: attempted evidence collection or validation failed.

Suffix-only detection should use ``evidence="inferred"`` and usually
``confidence="inferred"``. Details such as source and suffix list belong in
``metadata``:

```json
{
  "kind": "representation",
  "name": "netcdf",
  "namespace": "ogcat.core",
  "version": "1",
  "evidence": "inferred",
  "confidence": "inferred",
  "metadata": {"source": "suffix", "suffixes": [".nc"]}
}
```

This makes the difference between "looks like NetCDF" and "validated as
NetCDF" explicit.

## Namespaces

``ogcat.core`` is reserved for core claim and facet names. Plugin-owned claims
and facets should use a stable namespace such as a package name, plugin id, or
reverse-DNS string, for example ``pydata.xarray`` or ``org.unidata``. The
``version`` field is scoped to that namespace and name. Core validates only the
envelope and JSON compatibility, not plugin-specific schemas.

## Cheap Core Examples

Core can represent common cheap facts without importing optional reader
libraries:

- interfaces: ``bytes``, ``text``, ``directory-listing``, ``archive-members``,
  ``manifest``.
- representations: ``path``, ``file``, ``directory``, ``archive``,
  ``compressed-file``, ``text``, ``manifest``.
- facets: ``stat/size``, ``stat/mtime``, ``checksum/sha256``,
  ``suffix/suffixes``, ``archive/member-summary``, ``manifest/entries``.

This slice provides the schema and constructors. It does not automatically
generate these claims and facets during ingest, and it does not use them for
reader dispatch yet.

## Relationship To Classification Metadata

``derived_metadata.classification`` remains the current home for cheap inferred
facts such as ``artifact_kind``, ``format``, ``archive_format``,
``inner_format``, ``collection_pattern``, ``member_format``,
``member_suffixes``, and ``reader_hint``. This slice does not replace that
namespace and does not automatically mirror classification into facets.

Future migration can mirror selected classification facts into inferred
claims/facets while preserving the existing derived metadata search behavior.
For now, the bridge is documentation and compatibility: classification facts
remain readable and searchable, and new descriptors may add explicit inferred
claims/facets when a writer or plugin has enough context.
