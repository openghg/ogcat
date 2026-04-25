# Typed Record Schemas

`ogcat` keeps schemas deliberately small. A catalog has one explicit
`default_schema` for broad, heterogeneous ingest and an optional
`record_schemas` mapping keyed by record type. Each `RecordSchema` can describe
metadata fields, a directory template, a filename template, and a short
description.

Backward compatibility is preserved by keeping the older top-level
`metadata_fields`, `directory_template`, and `filename_template` fields on
`CatalogSpec`. When older `catalog.json` files are read, those fields are used to
construct the effective default schema. When a newer default schema is supplied,
the legacy attributes remain available as compatibility accessors.

For this first pass, record type and schema name are the same concept only where
a named schema exists. `Catalog.add_file(..., record_type="flux")` selects the
`flux` schema and raises a clear error if that named schema is missing. Generic
artifact records can still use arbitrary record types; they fall back to the
default schema unless a matching named schema is present.

Validation is intentionally shallow. Required metadata fields are checked for
presence when a record is added, but field types, coercion, plugin hooks,
extractor bindings, readers, managers, and automatic dispatch are left for later
layers.
