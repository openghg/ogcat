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

Small query helpers in ``ogcat.artifact_claims`` support the next registry
slice without duplicating normalization logic: ``iter_claims()``,
``has_claim()``, ``claim_key()``, ``iter_facets()``, ``has_facet()``, and
``facet_key()``.

## Relationship To Capability Selection

The #119 capability registry uses these claims and facets as dispatch facts.
It should inspect the ``ArtifactDescriptor`` attached to the artifact being
read, written, or converted; it should not use ``CatalogRecord.record_type`` as
an I/O dispatch key.

Selection is explicit. A single artifact may legitimately advertise several
interfaces at the same time, for example bytes, text, table, and JSON. A caller
that asks only to "read" such an artifact has not supplied enough information.
The registry should raise an ambiguity error until the caller requests a
specific interface or output claim such as ``interface=text`` or
``interface=table``.

Facets make otherwise similar claims precise. Text readers can use encoding
facets, delimited-table readers can use dialect facets, archive readers can use
member facets, and collection readers can use member-pattern facets. These
facts are plugin-readable metadata; they do not require core to import optional
scientific libraries.

Capability selection treats claim metadata as descriptive. Claims match by
their namespace/kind/name/version envelope. Facets match by the same envelope
plus required metadata as a subset, so dispatch-significant details such as
encoding values, delimiters, archive members, or local path requirements should
be modeled as facets.

## Worked Examples

These examples are design targets for writers, inspectors, and future readers.
They are not automatic output in this schema slice.

### Zarr Directory Store

Issue #121 and PR #122 made ``add_file()`` intentionally support managed
directory artifacts such as ``example.zarr``. A Zarr store is a directory-shaped
artifact, but it is one dataset artifact, not a collection. The useful claims
separate storage shape, data type, and optional interfaces:

```json
{
  "claims": [
    {"kind": "representation", "name": "directory", "namespace": "ogcat.core"},
    {"kind": "data_type", "name": "zarr", "namespace": "zarr.dev"},
    {"kind": "interface", "name": "directory-listing", "namespace": "ogcat.core"},
    {"kind": "interface", "name": "zarr-group", "namespace": "zarr.dev"},
    {"kind": "interface", "name": "xarray-dataset", "namespace": "pydata.xarray"}
  ],
  "facets": [
    {"kind": "suffix", "name": "suffixes", "metadata": {"suffixes": [".zarr"]}}
  ]
}
```

The Zarr and xarray claims do not mean core imports those libraries. They are
capability claims that a plugin or optional reader can satisfy later.

### NetCDF Member Collection

A directory of NetCDF files opened with a glob is different from a Zarr store.
It is a directory representation with explicit collection semantics:

```json
{
  "claims": [
    {"kind": "representation", "name": "directory", "namespace": "ogcat.core"},
    {"kind": "interface", "name": "collection", "namespace": "ogcat.core"},
    {"kind": "interface", "name": "xarray-dataset", "namespace": "pydata.xarray"}
  ],
  "facets": [
    {
      "kind": "collection",
      "name": "members",
      "metadata": {
        "pattern": "*.nc",
        "member_format": "netcdf",
        "member_suffixes": [".nc"],
        "reader_hint": "xarray.open_mfdataset"
      }
    }
  ]
}
```

This is the claim/facet shape that issue #109 can use for managed collection
writes. A reader can dispatch on the ``collection`` or ``xarray-dataset``
interface and then use the member facet to decide how to open the directory.

### CSV-Like Table

CSV-like data can stay dependency-light in core by recording text and table
interfaces without importing pandas:

```json
{
  "claims": [
    {"kind": "representation", "name": "text", "namespace": "ogcat.core"},
    {"kind": "data_type", "name": "csv", "namespace": "iana.media-types"},
    {"kind": "interface", "name": "text", "namespace": "ogcat.core"},
    {"kind": "interface", "name": "table", "namespace": "ogcat.core"}
  ]
}
```

### Single NetCDF File

A single NetCDF-like artifact has file representation and can advertise both
cheap byte access and optional scientific interfaces:

```json
{
  "claims": [
    {"kind": "representation", "name": "file", "namespace": "ogcat.core"},
    {"kind": "data_type", "name": "netcdf", "namespace": "org.unidata"},
    {"kind": "interface", "name": "bytes", "namespace": "ogcat.core"},
    {"kind": "interface", "name": "xarray-dataset", "namespace": "pydata.xarray"}
  ]
}
```

### Grouped NetCDF/HDF5 File

Groups are structured facts or interfaces, not a reason for a core NetCDF
dependency:

```json
{
  "claims": [
    {"kind": "data_type", "name": "netcdf", "namespace": "org.unidata"},
    {"kind": "interface", "name": "netcdf-groups", "namespace": "org.unidata"}
  ],
  "facets": [
    {
      "kind": "netcdf",
      "name": "groups",
      "namespace": "org.unidata",
      "metadata": {"groups": ["/", "/observations", "/metadata"]}
    }
  ]
}
```

## Directory Stores Versus Collections

PR #122 clarified an important ADR distinction. Directory-backed artifacts are
not automatically collections. A managed ``.zarr`` directory added with
``add_file()`` is one dataset artifact whose representation is ``directory``.
A managed directory of monthly ``.nc`` files is a collection only when a caller,
writer, or plugin explicitly adds collection claims/facets such as member
pattern, member format, and member suffixes.

This keeps storage shape separate from logical dataset shape:

- ``representation=directory`` says how bytes are stored.
- ``data_type=zarr`` or ``data_type=netcdf`` says what external format or data
  type is claimed.
- ``interface=collection`` says member traversal is part of the artifact
  contract.
- collection facets say which members participate and how a reader should
  combine them.

## Writer Result Open Loop

Writers are the natural source of produced artifact claims/facets because they
know what they materialized. This branch defines the schema, but it does not
define the merge path from writer output into descriptors. Issue #117 should
define that path, preserve existing ``None``-returning writers, and decide how
writer results interact with ``OperationContext``, rollback, storage plans,
audit metadata, and derived classification metadata.

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
