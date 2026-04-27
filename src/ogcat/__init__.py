"""Public package interface for ogcat."""

from ogcat.catalog import Catalog
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataFieldDescription
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.validation import (
    ValidationIssue,
    ValidationReport,
    validate_metadata,
    validate_record,
    validate_schema,
    validate_spec,
)

__all__ = [
    "ArtifactLocator",
    "Catalog",
    "CatalogRecord",
    "CatalogSpec",
    "MetadataFieldDescription",
    "RecordSchema",
    "ValidationIssue",
    "ValidationReport",
    "validate_metadata",
    "validate_record",
    "validate_schema",
    "validate_spec",
]
