"""Public package interface for ogcat."""

from ogcat.catalog import Catalog
from ogcat.hooks import ArtifactWriter, HookManager, HookWarning, OperationContext, OperationSource
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataFieldDescription
from ogcat.plugins import PluginRegistry
from ogcat.record_set import CatalogRecordSet
from ogcat.search import FieldPath, SearchOp, SearchQuery, SearchTerm
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.storage import (
    FsspecStorageAdapter,
    LocalStorageAdapter,
    StoragePlan,
    adapter_for_locator,
    create_directory_target,
    ensure_parent_directory,
    ensure_target_absent,
    plan_storage,
    remove_target,
    require_local_path,
    require_storage_target,
)
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
    CopyArtifactWriter,
    FunctionArtifactWriter,
    MoveArtifactWriter,
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
    "CatalogRecordSet",
    "CatalogSpec",
    "CopyArtifactWriter",
    "FsspecStorageAdapter",
    "FunctionArtifactWriter",
    "HookManager",
    "HookWarning",
    "LocalStorageAdapter",
    "MetadataFieldDescription",
    "MoveArtifactWriter",
    "OperationState",
    "OperationContext",
    "OperationSource",
    "PluginRegistry",
    "RollbackFailure",
    "RecordSchema",
    "FieldPath",
    "SearchOp",
    "SearchQuery",
    "SearchTerm",
    "StoragePlan",
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
    "adapter_for_locator",
    "create_directory_target",
    "ensure_parent_directory",
    "ensure_target_absent",
    "plan_storage",
    "remove_target",
    "require_local_path",
    "require_storage_target",
    "source_writer",
]
