# Catalog records

A catalog record represents one catalogued artifact.  Every record contains a
fixed set of reserved fields plus three metadata namespaces.

## Reserved fields

| Field | Description |
|-------|-------------|
| ``id`` | Stable string identifier assigned at ingest time. |
| ``catalog`` | Name of the catalog that owns the record. |
| ``record_type`` | Kind of artifact, e.g. ``managed_file`` or ``external_reference``. |
| ``locator`` | Describes where the artifact lives (see [Locators and storage](locators-and-storage.md)). |
| ``storage_mode`` | How the artifact was stored, e.g. ``copy``, ``move``, or ``external``. |
| ``original_filename`` | Source filename at ingest time. |
| ``suffixes`` | File suffix list derived from the source path. |
| ``time_added`` | ISO 8601 timestamp when the record was created. |

## Metadata namespaces

``user_metadata``
:   Key–value pairs supplied by the caller at ingest time.  Any
    JSON-compatible value is accepted. Common Python values such as
    `pathlib.Path`, tuples, sets, and NumPy scalar-like values are normalized
    before the record is validated and stored. This is the primary place to
    store domain metadata such as species, year, or instrument.

``derived_metadata``
:   Metadata added automatically during ingest by extractors and hooks.  For
    netCDF files this includes dimension names and sizes when ``xarray`` is
    installed. Derived metadata is normalized with the same JSON-compatible
    rules before storage. Do not rely on derived metadata being present for
    every file type.

``naming_metadata``
:   Internal metadata used to evaluate directory and filename templates.  You
    do not normally need to read or set this directly.

## Searching across namespaces

When you search with an unqualified field name such as ``species``, ogcat
looks in this order:

1. top-level record fields (``id``, ``record_type``, …)
2. ``user_metadata``
3. ``derived_metadata``

Use an explicit dotted path to target a specific namespace:
``user_metadata.species``, ``derived_metadata.netcdf.dims.time``, or the
short aliases ``user.species`` and ``derived.netcdf.dims.time``.

## Python API

Records are returned as ``CatalogRecord`` instances.

```python
record = catalog.add_file(path, metadata={"species": "CO2"})
print(record.id)
print(record.record_type)          # "managed_file"
print(record.user_metadata)        # {"species": "CO2", ...}
print(record.path())               # stored Path
```
