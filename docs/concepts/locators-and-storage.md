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

``Catalog.plan_artifact()`` performs the planning part of an add operation
without writing data or inserting a record.  It validates metadata, applies the
same naming templates, lets locator-resolution hooks adjust the result, and
returns a ``StoragePlan``.

```python
plan = catalog.plan_artifact(
    Path("incoming/example.nc"),
    metadata={"title": "example"},
    write_mode="copy",
)
print(plan.locator)
```

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

To catalog a file that should stay in place, use ``add_artifact()`` with a
path locator and ``record_type="external_reference"``.

```python
from ogcat import ArtifactLocator

catalog.add_artifact(
    record_type="external_reference",
    locator=ArtifactLocator.from_path("/data/shared/flux.nc"),
    metadata={"species": "CO2"},
)
```

The file is not copied or moved.  ogcat records only the path and the
metadata.

For non-local references, use a ``uri`` locator when ogcat should not check or
manage the target:

```python
catalog.add_artifact(
    record_type="external_reference",
    locator=ArtifactLocator(kind="uri", value="ftp://example.org/data/file.nc"),
    storage_mode="external",
)
```

Use ``ArtifactLocator.from_urlpath(...)`` when the location should be interpreted
by fsspec-backed storage adapters.  Install the optional dependency with
``ogcat[fsspec]`` before a writer performs fsspec-backed storage work.

## Catalog layout

```text
<catalog-root>/
  catalog.json      catalog specification and schemas
  db.json           TinyDB record store
  files/            managed file storage tree
```
