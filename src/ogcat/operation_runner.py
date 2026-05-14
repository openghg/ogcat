"""Internal coordinator for catalog add operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

from ogcat.audit import add_operation_note
from ogcat.classification import CLASSIFICATION_METADATA_KEY, classify_artifact
from ogcat.hooks import (
    HOOK_PHASES,
    ArtifactWriter,
    HookDispatcher,
    HookLifecycleCallback,
    HookManager,
    OperationContext,
    OperationSource,
)
from ogcat.models import ArtifactLocator, CatalogRecord, JsonValue, MetadataDict, normalize_metadata
from ogcat.spec import RecordSchema
from ogcat.storage import StoragePlan, TargetKind, WriteMode, plan_storage
from ogcat.transactions import OperationState, RollbackFailure, UnitOfWork
from ogcat.validation import ValidationReport

ArtifactLocatorFactory = Callable[[OperationContext], ArtifactLocator]
StoragePlanFactory = Callable[[OperationContext, ArtifactLocator], StoragePlan | None]
DerivedMetadataCollector = Callable[[OperationContext, ArtifactLocator], None]
_PhaseSetter = Callable[[str], None]


class _OperationAuditEmitter(Protocol):
    """Callable used by the runner to emit catalog operation audit events."""

    def __call__(
        self,
        context: OperationContext,
        *,
        event_type: str,
        level: str = "info",
        message: str,
        details: Mapping[str, object] | None = None,
        exception: BaseException | None = None,
        locator: ArtifactLocator | None = None,
    ) -> None:
        """Emit one operation audit event."""
        ...


class _MetadataValidationReporter(Protocol):
    """Callable used by the runner to validate operation metadata."""

    def __call__(
        self,
        *,
        schema: RecordSchema,
        metadata: object,
        record_type: str | None,
    ) -> ValidationReport:
        """Return a validation report for the supplied metadata."""
        ...


class _ArtifactRecordBuilder(Protocol):
    """Callable used by the runner to build a catalog record."""

    def __call__(
        self,
        *,
        record_type: str,
        locator: ArtifactLocator,
        record_id: str | None = None,
        metadata: MetadataDict | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: Mapping[Any, Any] | None = None,
        naming_metadata: Mapping[Any, Any] | None = None,
        time_added: str | None = None,
    ) -> CatalogRecord:
        """Build an artifact record without persisting it."""
        ...


@dataclass(slots=True)
class _OperationRunnerDependencies:
    """Catalog-owned collaborators used by an add operation runner."""

    catalog_root: Path
    hook_manager: HookManager
    schema_name: Callable[[str | None], str]
    metadata_validation_report: _MetadataValidationReporter
    build_artifact_record: _ArtifactRecordBuilder
    emit_operation_audit: _OperationAuditEmitter
    emit_hook_lifecycle_audit: HookLifecycleCallback


@dataclass(slots=True)
class _AddOperationRequest:
    """Inputs required to run one catalog add operation."""

    transaction: UnitOfWork
    commit: bool
    operation_type: str
    record_type: str
    schema: RecordSchema
    schema_record_type: str | None
    metadata: MetadataDict
    storage_mode: str | None
    original_path: str | Path | None
    original_filename: str | None
    suffixes: list[str] | None
    derived_metadata: MetadataDict
    naming_metadata: MetadataDict | None
    time_added: str | None
    source: OperationSource
    locator_factory: ArtifactLocatorFactory
    storage_plan_factory: StoragePlanFactory | None = None
    artifact_writer: ArtifactWriter | None = None
    derived_metadata_collector: DerivedMetadataCollector | None = None


@dataclass(slots=True)
class _AddOperationPlan:
    """Validated add-operation storage plan and its mutable hook context."""

    context: OperationContext
    locator: ArtifactLocator
    storage_plan: StoragePlan
    validation_report: ValidationReport


@dataclass(slots=True)
class OperationRunner:
    """Internal coordinator for one add-operation lifecycle."""

    dependencies: _OperationRunnerDependencies
    request: _AddOperationRequest

    def run(self) -> CatalogRecord:
        """Run the add operation and return the persisted or staged record."""
        hook_context = self._build_context()
        hook_dispatcher = self.dependencies.hook_manager.dispatcher(
            notify=self.dependencies.emit_hook_lifecycle_audit
        )
        current_phase = "operation-started"

        def set_phase(phase: str) -> None:
            """Track the currently running add-operation phase for failure audit."""
            nonlocal current_phase
            current_phase = phase

        self.dependencies.emit_operation_audit(
            hook_context,
            event_type="operation-started",
            message=f"Started {self.request.operation_type} operation.",
            details={"caller_owned_transaction": not self.request.commit},
        )
        try:
            validation_report = self._validate_metadata(
                context=hook_context,
                hook_dispatcher=hook_dispatcher,
                set_phase=set_phase,
            )
            canonical_locator = self._resolve_locator(
                context=hook_context,
                hook_dispatcher=hook_dispatcher,
                set_phase=set_phase,
            )
            add_plan = self._plan_storage(
                context=hook_context,
                locator=canonical_locator,
                validation_report=validation_report,
                set_phase=set_phase,
            )
            self._write_artifact(add_plan=add_plan, set_phase=set_phase)
            self._collect_metadata(
                add_plan=add_plan,
                hook_dispatcher=hook_dispatcher,
                set_phase=set_phase,
            )
            persisted = self._stage_record(
                add_plan=add_plan,
                hook_dispatcher=hook_dispatcher,
                set_phase=set_phase,
            )
            self._commit_if_owned(
                add_plan=add_plan,
                hook_dispatcher=hook_dispatcher,
                set_phase=set_phase,
            )
            return persisted
        except Exception as exc:
            self._handle_error(
                error=exc,
                context=hook_context,
                hook_dispatcher=hook_dispatcher,
                current_phase=current_phase,
            )
            raise

    def _build_context(self) -> OperationContext:
        """Build the mutable context shared by add-operation hooks and writers."""
        return OperationContext(
            catalog_root=self.dependencies.catalog_root,
            operation_id=self.request.transaction.operation_id,
            operation_type=self.request.operation_type,
            record_type=self.request.record_type,
            user_metadata=self.request.metadata,
            derived_metadata=self.request.derived_metadata,
            register_rollback=self.request.transaction.register_rollback,
            source=self.request.source,
            storage_mode=self.request.storage_mode,
            original_path=self.request.original_path,
            original_filename=self.request.original_filename,
            suffixes=[] if self.request.suffixes is None else list(self.request.suffixes),
        )

    def _validate_metadata(
        self,
        *,
        context: OperationContext,
        hook_dispatcher: HookDispatcher,
        set_phase: _PhaseSetter,
    ) -> ValidationReport:
        """Run validation hooks and return the add-operation validation report."""
        set_phase(HOOK_PHASES["before_validate_metadata"].name)
        hook_dispatcher.before_validate_metadata(context)
        set_phase("validation")
        context.user_metadata = _normalize_metadata_for_schema(
            context.user_metadata,
            schema_name=self.dependencies.schema_name(self.request.schema_record_type),
        )
        validation_report = self.dependencies.metadata_validation_report(
            schema=self.request.schema,
            metadata=context.user_metadata,
            record_type=self.request.schema_record_type,
        )
        set_phase(HOOK_PHASES["after_validate_metadata"].name)
        hook_dispatcher.after_validate_metadata(context, validation_report)
        self.dependencies.emit_operation_audit(
            context,
            event_type="validation",
            level=_validation_audit_level(validation_report),
            message=_validation_audit_message(validation_report),
            details=_validation_audit_details(validation_report),
        )
        validation_report.raise_for_errors()
        return validation_report

    def _resolve_locator(
        self,
        *,
        context: OperationContext,
        hook_dispatcher: HookDispatcher,
        set_phase: _PhaseSetter,
    ) -> ArtifactLocator:
        """Resolve the canonical artifact locator for an add operation."""
        set_phase("locator-factory")
        context.planned_locators = [self.request.locator_factory(context)]
        set_phase(HOOK_PHASES["resolve_artifact_locator"].name)
        hook_dispatcher.resolve_artifact_locator(context)
        canonical_locator = _artifact_locator_from_context(context)
        context.planned_locators[0] = canonical_locator
        return canonical_locator

    def _plan_storage(
        self,
        *,
        context: OperationContext,
        locator: ArtifactLocator,
        validation_report: ValidationReport,
        set_phase: _PhaseSetter,
    ) -> _AddOperationPlan:
        """Build and audit the storage plan for an add operation."""
        set_phase("storage-plan")
        context.storage_plan = (
            self.request.storage_plan_factory(context, locator)
            if self.request.storage_plan_factory is not None
            else plan_storage(
                locator,
                target_kind=_target_kind_from_writer(self.request.artifact_writer),
                write_mode=_write_mode_from_writer(self.request.artifact_writer),
                ogcat_owned=self.request.artifact_writer is not None,
                adapter=_adapter_name(locator),
                storage_relative_path=locator.relative_path,
                resolved_directory=_directory_from_locator(locator),
                resolved_filename=_filename_from_locator(locator),
            )
        )
        storage_plan = context.storage_plan
        if storage_plan is None:
            raise RuntimeError("Add operation did not produce a storage plan.")
        self.dependencies.emit_operation_audit(
            context,
            event_type="write",
            message="Storage plan prepared.",
            details={
                "write_phase": "storage-plan",
                "storage_plan": _storage_plan_audit_details(storage_plan),
            },
            locator=locator,
        )
        if self.request.naming_metadata is not None:
            self.request.naming_metadata.update(_naming_metadata_from_storage_plan(storage_plan))
        return _AddOperationPlan(
            context=context,
            locator=locator,
            storage_plan=storage_plan,
            validation_report=validation_report,
        )

    def _write_artifact(
        self,
        *,
        add_plan: _AddOperationPlan,
        set_phase: _PhaseSetter,
    ) -> None:
        """Materialise or skip the artifact write for an add operation."""
        if self.request.artifact_writer is None:
            if add_plan.storage_plan.write_mode != "reference":
                raise ValueError(
                    f"Storage plan with write mode {add_plan.storage_plan.write_mode!r} "
                    "requires an artifact_writer."
                )
            self.dependencies.emit_operation_audit(
                add_plan.context,
                event_type="write",
                message="Artifact write skipped for reference operation.",
                details={
                    "write_phase": "artifact-write",
                    "write_mode": add_plan.storage_plan.write_mode,
                },
                locator=add_plan.locator,
            )
            return

        set_phase("artifact-write")
        self.request.artifact_writer.write(add_plan.context, add_plan.context.source, add_plan.locator)
        self.dependencies.emit_operation_audit(
            add_plan.context,
            event_type="write",
            message="Artifact write completed.",
            details={
                "write_phase": "artifact-write",
                "writer_type": type(self.request.artifact_writer).__name__,
                "write_mode": add_plan.storage_plan.write_mode,
            },
            locator=add_plan.locator,
        )

    def _collect_metadata(
        self,
        *,
        add_plan: _AddOperationPlan,
        hook_dispatcher: HookDispatcher,
        set_phase: _PhaseSetter,
    ) -> None:
        """Collect derived metadata before record staging."""
        set_phase("metadata-extraction")
        _add_classification_metadata(add_plan.context, add_plan.locator)
        if self.request.derived_metadata_collector is not None:
            self.request.derived_metadata_collector(add_plan.context, add_plan.locator)
        set_phase(HOOK_PHASES["extract_metadata"].name)
        hook_dispatcher.extract_metadata(add_plan.context)
        add_plan.context.derived_metadata = normalize_metadata(
            add_plan.context.derived_metadata,
            field_name="derived_metadata",
        )

    def _stage_record(
        self,
        *,
        add_plan: _AddOperationPlan,
        hook_dispatcher: HookDispatcher,
        set_phase: _PhaseSetter,
    ) -> CatalogRecord:
        """Stage the catalog record and run record-write hooks."""
        set_phase(HOOK_PHASES["before_record_write"].name)
        hook_dispatcher.before_record_write(add_plan.context)
        add_plan.context.user_metadata = _normalize_metadata_for_schema(
            add_plan.context.user_metadata,
            schema_name=self.dependencies.schema_name(self.request.schema_record_type),
        )
        add_plan.context.derived_metadata = normalize_metadata(
            add_plan.context.derived_metadata,
            field_name="derived_metadata",
        )
        record = self.dependencies.build_artifact_record(
            record_type=self.request.record_type,
            locator=add_plan.locator,
            metadata=add_plan.context.user_metadata,
            storage_mode=self.request.storage_mode,
            original_path=self.request.original_path,
            original_filename=self.request.original_filename,
            suffixes=self.request.suffixes,
            derived_metadata=_metadata_with_hook_warnings(add_plan.context),
            naming_metadata=self.request.naming_metadata,
            time_added=self.request.time_added,
        )
        set_phase("record-write")
        persisted = self.request.transaction.insert_staged_record(record)
        add_plan.context.record_id = _require_record_id(persisted)
        self.dependencies.emit_operation_audit(
            add_plan.context,
            event_type="write",
            message="Record staged.",
            details={
                "write_phase": "record-write",
                "transaction_state": self.request.transaction.state.value,
            },
            locator=add_plan.locator,
        )
        set_phase(HOOK_PHASES["after_record_write"].name)
        hook_dispatcher.after_record_write(add_plan.context)
        return persisted

    def _commit_if_owned(
        self,
        *,
        add_plan: _AddOperationPlan,
        hook_dispatcher: HookDispatcher,
        set_phase: _PhaseSetter,
    ) -> None:
        """Commit internal add-operation transactions and run commit hooks."""
        if not self.request.commit:
            return
        set_phase(HOOK_PHASES["before_commit"].name)
        hook_dispatcher.before_commit(add_plan.context)
        set_phase("commit")
        self.request.transaction.commit()
        self.dependencies.emit_operation_audit(
            add_plan.context,
            event_type="commit",
            message="Commit succeeded.",
            details={"transaction_state": self.request.transaction.state.value},
            locator=add_plan.locator,
        )
        # After-commit hooks are best-effort: failures warn, but cannot turn an
        # already-persisted record into an apparent API failure.
        set_phase(HOOK_PHASES["after_commit"].name)
        hook_dispatcher.after_commit(add_plan.context)

    def _handle_error(
        self,
        *,
        error: Exception,
        context: OperationContext,
        hook_dispatcher: HookDispatcher,
        current_phase: str,
    ) -> None:
        """Audit and roll back a failed add operation."""
        add_operation_note(error, context.operation_id)
        hook_dispatcher.on_error(context, error)
        self.dependencies.emit_operation_audit(
            context,
            event_type="failure",
            message=f"{self.request.operation_type} operation failed.",
            details={
                "phase": current_phase,
                "transaction_state": self.request.transaction.state.value,
                "caller_owned_transaction": not self.request.commit,
            },
            exception=error,
        )
        # Caller-supplied transactions stay caller-owned. Internal transactions
        # commit=True and roll back here before re-raising.
        if self.request.commit and self.request.transaction.state is not OperationState.COMMITTED:
            self.dependencies.emit_operation_audit(
                context,
                event_type="rollback",
                message="Rollback started.",
                details={
                    "rollback_phase": "started",
                    "transaction_state": self.request.transaction.state.value,
                },
            )
            self.request.transaction.rollback(original_exception=error)
            hook_dispatcher.on_rollback(context, error)
            if self.request.transaction.rollback_errors:
                rollback_details: dict[str, object] = {
                    "rollback_phase": "failed",
                    "transaction_state": self.request.transaction.state.value,
                    "rollback_errors": _rollback_errors_audit_details(
                        self.request.transaction.rollback_errors
                    ),
                }
                self.dependencies.emit_operation_audit(
                    context,
                    event_type="rollback",
                    level="error",
                    message="Rollback completed with failures.",
                    details=rollback_details,
                    exception=self.request.transaction.rollback_errors[0].exception,
                )
            else:
                self.dependencies.emit_operation_audit(
                    context,
                    event_type="rollback",
                    message="Rollback completed.",
                    details={
                        "rollback_phase": "completed",
                        "transaction_state": self.request.transaction.state.value,
                    },
                )


def _normalize_metadata_for_schema(metadata: object, *, schema_name: str) -> MetadataDict:
    """Normalize user metadata with the existing schema-aware error prefix."""
    return normalize_metadata(
        metadata,
        field_name="metadata",
        label=f"Metadata for schema {schema_name}",
    )


def _artifact_locator_from_context(context: OperationContext) -> ArtifactLocator:
    """Return the canonical locator after locator-resolution hooks run."""
    if not context.planned_locators:
        raise ValueError("resolve_artifact_locator hook removed the planned artifact locator.")
    return context.planned_locators[0]


def _metadata_with_hook_warnings(context: OperationContext) -> MetadataDict:
    """Return derived metadata with non-fatal hook warnings included."""
    metadata = normalize_metadata(context.derived_metadata, field_name="derived_metadata")
    if context.warnings:
        warnings_metadata: list[JsonValue] = [warning.to_metadata() for warning in context.warnings]
        metadata["hook_warnings"] = warnings_metadata
    return normalize_metadata(metadata, field_name="derived_metadata")


def _add_classification_metadata(context: OperationContext, locator: ArtifactLocator) -> None:
    """Add cheap artifact classification metadata without overwriting caller values."""
    classification = classify_artifact(
        locator,
        original_path=context.original_path,
        original_filename=context.original_filename,
        suffixes=context.suffixes,
    )
    existing = context.derived_metadata.get(CLASSIFICATION_METADATA_KEY)
    if isinstance(existing, Mapping):
        context.derived_metadata[CLASSIFICATION_METADATA_KEY] = {
            **classification,
            **existing,
        }
    elif existing is None:
        context.derived_metadata[CLASSIFICATION_METADATA_KEY] = classification


def _storage_plan_with_locator(plan: StoragePlan, locator: ArtifactLocator) -> StoragePlan:
    """Return a storage plan adjusted to a hook-resolved canonical locator."""
    if plan.locator == locator:
        return plan
    return replace(
        plan,
        locator=locator,
        adapter=_adapter_name(locator),
        storage_relative_path=locator.relative_path,
        resolved_directory=_directory_from_locator(locator),
        resolved_filename=_filename_from_locator(locator),
    )


def _naming_metadata_from_storage_plan(plan: StoragePlan) -> MetadataDict:
    """Build record naming metadata from storage planning outputs."""
    metadata: MetadataDict = {}
    if plan.storage_relative_path is not None:
        metadata["storage_relative_path"] = plan.storage_relative_path
    if plan.resolved_directory is not None:
        metadata["resolved_directory"] = plan.resolved_directory
    if plan.resolved_filename is not None:
        metadata["resolved_filename"] = plan.resolved_filename
    return metadata


def _target_kind_from_writer(artifact_writer: ArtifactWriter | None) -> TargetKind:
    """Infer a storage target kind from a writer when it declares one."""
    if artifact_writer is None:
        return "file"
    target_kind = getattr(artifact_writer, "target_kind", "file")
    if target_kind in {"file", "directory"}:
        return cast(TargetKind, target_kind)
    return "file"


def _write_mode_from_writer(artifact_writer: ArtifactWriter | None) -> WriteMode:
    """Infer a storage write mode from a writer when it declares one."""
    if artifact_writer is None:
        return "reference"
    write_mode = getattr(artifact_writer, "write_mode", "write")
    if write_mode in {"copy", "move", "write", "reference"}:
        return cast(WriteMode, write_mode)
    return "write"


def _adapter_name(locator: ArtifactLocator) -> str | None:
    """Return the storage adapter name implied by a locator."""
    if locator.kind == "path":
        return "local"
    if locator.kind == "urlpath":
        return "fsspec"
    return None


def _filename_from_locator(locator: ArtifactLocator) -> str:
    """Return the final filename-like component from a locator."""
    if locator.kind == "path":
        return Path(locator.value).name
    return locator.value.rstrip("/").rsplit("/", 1)[-1]


def _directory_from_locator(locator: ArtifactLocator) -> str:
    """Return the directory-like component from a locator."""
    if locator.kind == "path":
        return Path(locator.value).parent.as_posix()
    return locator.value.rstrip("/").rsplit("/", 1)[0]


def _directory_from_relative_path(relative_path: str | None) -> str | None:
    """Return the directory component from a storage-relative path."""
    if relative_path is None:
        return None
    directory = Path(relative_path).parent.as_posix()
    return "" if directory == "." else directory


def _validation_audit_level(report: ValidationReport) -> str:
    """Return the audit level for a validation report."""
    if report.errors:
        return "error"
    if report.warnings:
        return "warning"
    return "info"


def _validation_audit_message(report: ValidationReport) -> str:
    """Return a concise validation audit message."""
    if report.errors:
        return "Metadata validation failed."
    if report.warnings:
        return "Metadata validation completed with warnings."
    return "Metadata validation succeeded."


def _validation_audit_details(report: ValidationReport) -> dict[str, object]:
    """Return validation issue summaries for audit events."""
    return {
        "validation": {
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
            "issues": [
                {
                    "path": issue.path,
                    "message": issue.message,
                    "severity": issue.severity,
                    "code": issue.code,
                    "hint": issue.hint,
                }
                for issue in report.issues
            ],
        }
    }


def _storage_plan_audit_details(plan: StoragePlan) -> dict[str, object]:
    """Return storage plan details that are safe for audit logging."""
    return {
        "target_kind": plan.target_kind,
        "write_mode": plan.write_mode,
        "checksum": plan.checksum,
        "ogcat_owned": plan.ogcat_owned,
        "profile": plan.profile,
        "adapter": plan.adapter,
        "time_added": plan.time_added,
        "storage_relative_path": plan.storage_relative_path,
        "resolved_directory": plan.resolved_directory,
        "resolved_filename": plan.resolved_filename,
    }


def _rollback_errors_audit_details(rollback_errors: list[RollbackFailure]) -> list[dict[str, str]]:
    """Return rollback failure summaries for audit events."""
    return [
        {
            "description": failure.description,
            "exception_type": type(failure.exception).__name__,
            "exception_message": str(failure.exception),
        }
        for failure in rollback_errors
    ]


def _require_record_id(record: CatalogRecord) -> str:
    """Return a persisted record id."""
    if record.id is None:
        raise RuntimeError("Repository returned a persisted record without an id.")
    return record.id
