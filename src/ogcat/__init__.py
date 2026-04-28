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
from ogcat.writers import (
    FunctionArtifactWriter,
    UnzipArtifactWriter,
    memory_source,
    memory_writer,
    path_source,
    path_writer,
    source_writer,
)

__all__ = [
    "ArtifactLocator",
    "ArtifactWriter",
    "Catalog",
    "CatalogRecord",
    "CatalogSpec",
    "FunctionArtifactWriter",
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
    "UnzipArtifactWriter",
    "ValidationIssue",
    "ValidationReport",
    "validate_metadata",
    "validate_record",
    "validate_schema",
    "validate_spec",
    "memory_source",
    "memory_writer",
    "path_source",
    "path_writer",
    "source_writer",
]
