"""Public package interface for ogcat."""

from ogcat.catalog import Catalog
from ogcat.hooks import HookManager, HookWarning, OperationContext
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataFieldDescription
from ogcat.plugins import PluginRegistry
from ogcat.search import FieldPath, SearchOp, SearchQuery, SearchTerm
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.transactions import OperationState, RollbackFailure, UnitOfWork
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
    "HookManager",
    "HookWarning",
    "MetadataFieldDescription",
    "OperationState",
    "OperationContext",
    "PluginRegistry",
    "RollbackFailure",
    "RecordSchema",
    "FieldPath",
    "SearchOp",
    "SearchQuery",
    "SearchTerm",
    "UnitOfWork",
    "ValidationIssue",
    "ValidationReport",
    "validate_metadata",
    "validate_record",
    "validate_schema",
    "validate_spec",
]
