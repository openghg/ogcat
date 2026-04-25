# Typed Record Schemas

`ogcat` keeps schemas deliberately small. A catalog has one explicit
`default_schema` for broad, heterogeneous ingest and an optional
`record_schemas` mapping keyed by record type. Each `RecordSchema` can describe
metadata fields, a directory template, a filename template, and a short
description.

`default_schema` is the source of truth for broad catalog behavior. Earlier MVP
top-level fields such as `metadata_fields`, `directory_template`, and
`filename_template` were removed before any real catalog migration burden existed,
which keeps `CatalogSpec` smaller and avoids parallel compatibility state.

For this first pass, record type and schema name are the same concept only where
a named schema exists. `Catalog.add_file(..., record_type="flux")` selects the
`flux` schema and raises a clear error if that named schema is missing. Generic
artifact records can still use arbitrary record types; they fall back to the
default schema unless a matching named schema is present.

Validation is intentionally shallow. Required metadata fields are checked for
presence when a record is added, but field types, coercion, plugin hooks,
extractor bindings, readers, managers, and automatic dispatch are left for later
layers.
