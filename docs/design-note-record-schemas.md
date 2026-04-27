# Typed Record Schemas

`ogcat` keeps schemas deliberately small. A catalog has one explicit
`default_schema` for broad, heterogeneous ingest and an optional
`record_schemas` mapping keyed by record type. Each `RecordSchema` can describe
metadata fields, a directory template, a filename template, and a short
description.

Metadata field descriptions can also carry lightweight type names. These are
serialised as human-readable schema hints for now; they are not enforced by the
catalog core.

`default_schema` is the source of truth for broad catalog behavior. Earlier MVP
top-level fields such as `metadata_fields`, `directory_template`, and
`filename_template` were removed before any real catalog migration burden existed,
which keeps `CatalogSpec` smaller and avoids parallel compatibility state.

For this first pass, record type and schema name are the same concept only where
a named schema exists. `Catalog.add_file(..., record_type="flux")` selects the
`flux` schema and raises a clear error if that named schema is missing. Generic
artifact records can still use arbitrary record types; they fall back to the
default schema unless a matching named schema is present.

Validation remains lightweight and schema-driven. Required metadata fields are
checked when a record is added. If a schema supplies `value_types`, those values
are also validated on add and can reject records with incompatible metadata.
Callers can use `ogcat.validate_metadata()` or `ogcat.validate_record()` to get
structured validation reports for CLI output, tests, or plugin code.

Unknown metadata is allowed by default so broad catalogs can stay free-form:

```python
from ogcat import RecordSchema, validate_metadata

schema = RecordSchema()
report = validate_metadata({"title": "Example", "extra": "allowed"}, schema)
assert report.ok
```

Project catalogs can opt into strict unknown-field handling by setting
`allow_unknown_metadata=False` on a schema and calling validation with
`strict=True`:

```python
from ogcat import MetadataFieldDescription, RecordSchema, validate_metadata

schema = RecordSchema(
    metadata_fields=[
        MetadataFieldDescription(name="title", description="Short title.", required=True),
    ],
    allow_unknown_metadata=False,
)
report = validate_metadata({"title": "Example", "extra": "blocked"}, schema, strict=True)
assert not report.ok
```

Field descriptions can also include simple `value_types` labels such as `str`,
`int`, `number`, `bool`, `date`, `datetime`, `list[str]`, and `dict`. These are
validated internally with Pydantic while the public runtime objects remain
dataclasses. Schema authors can call `validate_schema()` or `validate_spec()` to
catch unsupported type labels without causing repeated warnings during regular
record validation. Domain-specific checks should live in plugins or caller code
and can append their own `ValidationIssue` objects to the same report format.
