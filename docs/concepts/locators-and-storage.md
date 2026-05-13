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
  local source into the catalog's ``files/`` tree.
- ``Catalog.add_reference(...)`` is for artifacts that already exist. It records
  a local path, URI locator, or URL-path locator without copying, moving, or
  writing artifact data.
- ``Catalog.add_artifact(...)`` with a ``StoragePlan`` and an artifact writer is
  for workflow outputs. ogcat plans and records the artifact, while the writer
  performs the actual filesystem or storage operation.

## Managed files

``catalog.add_file()`` copies or moves the source file into the catalog's
``files/`` tree and records a ``path`` locator pointing at the stored copy.

```python
record = catalog.add_file(
    Path("data.nc"),
    metadata={"species": "CO2"},
    operation="copy",     # or "move"
)
print(record.path())      # path inside files/
```

The storage location is derived from directory and filename templates stored
in ``catalog.json``.  The defaults are:

```
directory: {year_added}/{original_stem}
filename:  {title_slug|original_stem}{original_suffix}
```

## Storage plans

``Catalog.plan_artifact_storage()`` performs the planning part of an add operation
without writing data or inserting a record.  It validates metadata, applies the
same naming templates, lets locator-resolution hooks adjust the result, and
returns a ``StoragePlan``.

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

## Overriding template-derived storage paths

Pass an explicit locator when the correct target path is known and should not be
derived from the schema naming templates.  This is useful when the physical
source filename is not the filename that should be stored, such as a ``.zip``
archive that contains a single ``.nc`` member.

```python
from pathlib import Path

from ogcat import ArtifactLocator, UnzipSingleFileArtifactWriter, path_source

archive_path = Path("incoming/GCP-GridFEDv2023.1_2018.zip")
target_path = catalog.root / "files" / "flux/raw/GridFED/v2023.1/co2-o2/GCP-GridFEDv2023.1_2018.nc"

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
catalog's managed ``files/`` root.

```python
catalog.add_reference(
    "/data/shared/flux.nc",
    metadata={"species": "CO2"},
)
```

For non-local references, use a ``uri`` locator when ogcat should not check or
manage the target:

```python
from ogcat import ArtifactLocator

catalog.add_reference(
    ArtifactLocator(kind="uri", value="ftp://example.org/data/file.nc"),
)
```

Use ``ArtifactLocator.from_urlpath(...)`` with ``add_reference(...)`` when the
location should be interpreted by fsspec-backed storage adapters. Install the
optional dependency with
``ogcat[fsspec]`` before a writer performs fsspec-backed storage work.

## Catalog layout

```text
<catalog-root>/
  catalog.json      catalog specification and schemas
  db.json           TinyDB record store
  files/            managed file storage tree
```
