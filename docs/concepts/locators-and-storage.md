# Locators and storage

A *locator* tells ogcat where a catalogued artifact lives.  The locator is
stored in the catalog record alongside the metadata and is independent of how
the file ended up there.

## Locator kinds

``path``
:   A local filesystem path.  Managed files ingested with ``add_file()`` use
    this kind.  Path-backed records support :meth:`ogcat.CatalogRecord.path`
    and the ``ogcat path`` CLI command.

``urlpath``
:   An fsspec-addressable URL path, such as ``ssh://host/path/file.nc`` or
    ``s3://bucket/path/store.zarr``.  These locators are interpreted only when
    fsspec-backed storage behavior is requested.

``uri``
:   An external reference that ogcat records but does not manage or inspect.
    Use this for DOI, FTP, HTTP, ICOS, object-store, or project-specific
    references that domain code will interpret later.

``opaque``
:   A placeholder used when the locator is not yet set or when no path is
    applicable.  You will not normally see this in practice.

Other project-specific kinds can be stored using :meth:`ogcat.ArtifactLocator`
directly, but ogcat does not interpret them beyond recording the string value.

## Choosing an add method

Use the three catalog add methods for different storage responsibilities:

- ``Catalog.add_file(...)`` is for managed local ingest. It copies or moves a
  local source into the catalog's ``data/objects/`` tree by default.
- ``Catalog.add_reference(...)`` is for artifacts that already exist. It records
  a local path, URI, URI locator, or URL-path locator without copying, moving,
  or writing artifact data.
- ``Catalog.add_collection(...)`` is for existing logical datasets whose members
  live under one collection root, such as a directory of monthly NetCDF files or
  a remote prefix. It records one artifact with collection classification
  metadata and does not scan, copy, move, or open member files.
- ``Catalog.add_artifact(...)`` with a ``StoragePlan`` and an artifact writer is
  for workflow outputs. ogcat plans and records the artifact, while the writer
  performs the actual filesystem or storage operation.

## Managed files

``catalog.add_file()`` copies or moves the source file into the catalog's
``data/objects/`` tree by default and records a ``path`` locator pointing at
that UUID-backed primary copy. The configured directory and filename templates
create a human-readable symlink replica, not the canonical artifact path.

```python
record = catalog.add_file(
    Path("data.nc"),
    metadata={"species": "CO2"},
    operation="copy",     # or "move"
)
print(record.path())      # primary UUID path inside data/objects/
```

The human-readable replica location is derived from directory and filename
templates stored in ``catalog.json``. The defaults are:

```
directory: {year_added}/{original_stem}
filename:  {title_slug|original_stem}{original_suffix}
```

### Naming template fields

Storage and template-link naming templates are intended to produce
human-readable paths. They may use user metadata fields plus these generated
fields:

- ``date_added``: date used for the add operation, in ``YYYY-MM-DD`` form.
- ``year_added``: year from ``date_added``.
- ``original_filename``: source filename used for naming.
- ``original_stem``: normalized source filename without the preserved suffix.
- ``original_suffix``: preserved source suffix such as ``.nc`` or
  ``.tar.gz``.
- ``title_slug``: slugified ``title`` metadata when a non-empty title is
  supplied.
- ``year_month_or_original_stem``: ``YYYYMM`` from integer-like ``year`` and
  ``month`` metadata, ``YYYY`` from ``year`` alone, or ``original_stem``.

Internal identifiers are deliberately not part of human-readable storage
template policy. ``id``, ``uuid``, ``operation_id``, and ``artifact_uuid`` are
reserved for catalog bookkeeping, storage planning, audit, and operation
correlation. Metadata cannot use those names, and schema naming templates cannot
reference them. If you need a user-visible identifier in a path, provide it as
ordinary metadata with an explicit domain name such as ``dataset_id``.

Generated replica views are different: they are built from existing
``CatalogRecord`` objects and may use record fields such as ``id`` when that is
useful for disambiguating a view.

Pass ``primary_location="template"`` to keep the older template-primary
behavior:

```python
record = catalog.add_file(
    Path("data.nc"),
    metadata={"species": "CO2"},
    primary_location="template",
)
```

## Storage plans

``Catalog.plan_artifact_storage()`` performs the planning part of an add operation
without writing data or inserting a record. It validates metadata, applies the
primary placement policy, lets locator-resolution hooks adjust the result, and
returns a ``StoragePlan``. The default primary placement is UUID-backed; pass
``primary_location="template"`` when schema templates should define the primary
target.

```python
plan = catalog.plan_artifact_storage(
    Path("incoming/example.nc"),
    metadata={"title": "example"},
    write_mode="copy",
)
print(plan.locator)
```

``StoragePlan`` describes storage only.  It carries the resolved locator,
target kind, write mode, storage-relative path, resolved directory, and resolved
filename.  It does not carry record metadata; pass metadata again to
``add_artifact(...)`` when turning a storage plan into a record.

## Generated replica views

Use ``Catalog.plan_view()`` to build generated human-readable views from current
record metadata and primary locators. Planning is a dry run: it renders target
paths, reports duplicate paths and unsupported records, and does not create
links.

```python
plan = catalog.plan_view(
    root="./by-product",
    template="{product}/{species}/{id}_{original_filename}",
    mode="symlink",
    where={"provenance": "derived"},
)
print(plan.collisions)
result = plan.apply()
print(result.created)
```

For v1, only local symlink replicas are supported. URI and URL-path records are
reported as unsupported for local views, and missing primary paths are reported
before applying unless ``skip_errors=True`` is passed to ``apply()``.

## Overriding template-derived storage paths

Pass an explicit locator when the correct target path is known and should not be
derived from the schema naming templates.  This is useful when the physical
source filename is not the filename that should be stored, such as a ``.zip``
archive that contains a single ``.nc`` member.

```python
from pathlib import Path

from ogcat import ArtifactLocator, UnzipSingleFileArtifactWriter, path_source

archive_path = Path("incoming/GCP-GridFEDv2023.1_2018.zip")
target_path = catalog.root / "data" / "files" / "flux/raw/GridFED/v2023.1/co2-o2/GCP-GridFEDv2023.1_2018.nc"

plan = catalog.plan_artifact_storage(
    archive_path,
    record_type="raw_flux",
    locator=ArtifactLocator.from_path(target_path),
    target_kind="file",
    write_mode="write",
    metadata={"product": "GridFED", "version": "v2023.1", "species": "co2-o2", "year": 2018},
)

record = catalog.add_artifact(
    record_type="raw_flux",
    storage_plan=plan,
    metadata={"product": "GridFED", "version": "v2023.1", "species": "co2-o2", "year": 2018},
    source=path_source(archive_path, kind="zip_file"),
    artifact_writer=UnzipSingleFileArtifactWriter(),
)
```

When a locator is supplied, ``plan_artifact_storage(...)`` still validates
metadata and exposes the planned locator to hooks, but it does not render the
schema directory and filename templates.  The resulting record uses the
explicit locator from the plan.

Hook timing matters.  ``before_validate_metadata`` runs before planning, so it
receives neither ``context.planned_locators`` nor ``context.storage_plan``.
``resolve_artifact_locator`` receives proposed locators in
``context.planned_locators`` and can return the locator that should be used for
the artifact being added.  After that hook returns, ogcat builds the final
``StoragePlan`` and exposes it to later hooks and artifact writers as
``context.storage_plan``.  The plan lets domain code materialise a generic
artifact such as a directory of NetCDF files or a ``.zarr`` store while ogcat
core only records the locator.  Artifact writers remain responsible for
filesystem side effects and rollback registration.

## External references

To catalog a file that should stay in place, use ``add_reference()``. For local
paths, ogcat infers the path locator, original filename, suffixes, and original
path. The file is not copied or moved, and it does not need to be under the
catalog's managed ``data/files/`` root.

```python
catalog.add_reference(
    "/data/shared/flux.nc",
    metadata={"species": "CO2"},
)
```

For non-local references, use a ``uri`` locator when ogcat should not check or
manage the target. URI-looking strings are recorded as ``uri`` references; use
``uri=`` when that is clearer at the call site:

```python
catalog.add_reference(uri="ftp://example.org/data/file.nc")
```

Use ``ArtifactLocator.from_urlpath(...)`` or ``urlpath=`` with
``add_reference(...)`` when the location should be interpreted by fsspec-backed
storage adapters. Install the optional dependency with
``ogcat[fsspec]`` before a writer performs fsspec-backed storage work.

## Collection artifacts

Use ``add_collection(...)`` when one logical dataset is made from multiple
member artifacts that should be selected and opened together. Common examples
are monthly or yearly NetCDF files for one model/product series, directories of
tiles, and remote object-store prefixes.

```python
footprints = catalog.add_collection(
    Path("/group/chem/acrg/LPDM/fp_NAME/EASTASIA/BCOB-10magl/inert"),
    record_type="footprint_collection",
    metadata={
        "site": "BCOB",
        "domain": "EASTASIA",
        "years": [2023, 2024],
        "member_count": 24,
    },
    collection_pattern="BCOB-10magl_NAME_UMG_EASTASIA_inert_*.nc",
    member_format="netcdf",
    member_suffixes=[".nc"],
    reader_hint="xarray.open_mfdataset",
)
```

A local path-backed collection must be an existing directory. When the root is
not mounted locally, pass ``uri=`` or ``urlpath=`` instead; ogcat records the
locator and classification metadata without checking the remote contents.

```python
remote_footprints = catalog.add_collection(
    uri="/group/chem/acrg/LPDM/fp_NAME/EASTASIA/BCOB-10magl/inert",
    record_type="footprint_collection",
    metadata={"site": "BCOB", "domain": "EASTASIA"},
    collection_pattern="BCOB-10magl_NAME_UMG_EASTASIA_inert_*.nc",
    member_format="netcdf",
    member_suffixes=[".nc"],
    reader_hint="xarray.open_mfdataset",
)
```

Collection metadata is stored under
``derived_metadata["classification"]``. The most useful fields are flattened for
ordinary search:

```python
records = catalog.search(
    where={
        "artifact_kind": "collection",
        "member_format": "netcdf",
        "collection_pattern": "BCOB-10magl_NAME_UMG_EASTASIA_inert_*.nc",
    }
)
```

ogcat does not interpret the member pattern beyond validating that it is a safe
relative pattern. Downstream code is responsible for resolving the root and
opening members, for example with xarray when ``reader_hint`` is
``"xarray.open_mfdataset"``.

## Catalog layout

```text
<catalog-root>/
  catalog.json      catalog specification and schemas
  db.json           TinyDB record store
  data/files/       human-readable template replica tree
  data/objects/     UUID primary object storage tree
```
