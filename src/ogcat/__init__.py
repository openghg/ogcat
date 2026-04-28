"""Public package interface for ogcat."""

from ogcat.catalog import Catalog
from ogcat.hooks import ArtifactWriter, HookManager, HookWarning, OperationContext, OperationSource
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataFieldDescription
from ogcat.plugins import PluginRegistry
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
    "ArtifactWriter",
    "Catalog",
    "CatalogRecord",
    "CatalogSpec",
    "HookManager",
    "HookWarning",
    "MetadataFieldDescription",
    "OperationState",
    "OperationContext",
    "OperationSource",
    "PluginRegistry",
    "RollbackFailure",
    "RecordSchema",
    "UnitOfWork",
    "ValidationIssue",
    "ValidationReport",
    "validate_metadata",
    "validate_record",
    "validate_schema",
    "validate_spec",
]
