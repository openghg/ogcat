# Locators and storage

A *locator* tells ogcat where a catalogued artifact lives.  The locator is
stored in the catalog record alongside the metadata and is independent of how
the file ended up there.

## Locator kinds

``path``
:   A local filesystem path.  Managed files ingested with ``add_file()`` use
    this kind.  Path-backed records support :meth:`ogcat.CatalogRecord.path`
    and the ``ogcat path`` CLI command.

``opaque``
:   A placeholder used when the locator is not yet set or when no path is
    applicable.  You will not normally see this in practice.

Other kinds such as ``uri`` or ``s3`` can be stored using
:meth:`ogcat.ArtifactLocator` directly, but ogcat does not interpret them
beyond recording the string value.

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

## External references

To catalog a file that should stay in place, use ``add_artifact()`` with a
path locator and ``record_type="external_reference"``.

```python
from ogcat import ArtifactLocator

catalog.add_artifact(
    record_type="external_reference",
    locator=ArtifactLocator.path("/data/shared/flux.nc"),
    metadata={"species": "CO2"},
)
```

The file is not copied or moved.  ogcat records only the path and the
metadata.

## Catalog layout

```text
<catalog-root>/
  catalog.json      catalog specification and schemas
  db.json           TinyDB record store
  files/            managed file storage tree
```
