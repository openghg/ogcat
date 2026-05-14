"""Public package interface for ogcat."""

from ogcat.audit import AuditEvent, AuditSink, JsonlAuditSink, read_audit_events
from ogcat.catalog import Catalog
from ogcat.classification import classify_artifact
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
    UnzipSingleFileArtifactWriter,
    memory_source,
    memory_writer,
    path_source,
    path_writer,
    source_writer,
)

__all__ = [
    "ArtifactLocator",
    "ArtifactWriter",
    "AuditEvent",
    "AuditSink",
    "Catalog",
    "CatalogRecord",
    "CatalogRecordSet",
    "CatalogSpec",
    "classify_artifact",
    "CopyArtifactWriter",
    "FsspecStorageAdapter",
    "FunctionArtifactWriter",
    "HookManager",
    "HookWarning",
    "JsonlAuditSink",
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
    "UnzipSingleFileArtifactWriter",
    "ValidationIssue",
    "ValidationReport",
    "validate_metadata",
    "validate_record",
    "validate_schema",
    "validate_spec",
    "read_audit_events",
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
