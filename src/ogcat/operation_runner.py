"""Internal operation runner interfaces and the add-operation implementation.

``Catalog`` owns public API argument handling, schema selection, and transaction
creation. Operation runners own the operation lifecycle once those inputs are
prepared. The module-level ``OperationRunner`` ABC is intentionally generic so
future operation families, such as artifact updates, can implement the same
``run()`` command interface without pretending they are add operations.

``AddOperationRunner`` is the concrete runner for the current add lifecycle. It
uses a template-style flow: ``run()`` fixes the ordering of validation, locator
resolution, storage planning, artifact writing, metadata collection, record
staging, commit, and rollback, while private phase methods keep each step
separately testable and replaceable by future sibling runners.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from ogcat.audit import add_operation_note
from ogcat.classification import CLASSIFICATION_METADATA_KEY, classify_artifact
from ogcat.exceptions import PurgeIncompleteError
from ogcat.hooks import (
    HOOK_PHASES,
    HookDispatcher,
    HookLifecycleCallback,
    HookManager,
    OperationContext,
    OperationSource,
)
from ogcat.materialization import (
    MaterializationIntent,
    materialization_plan_from_locator,
    validate_writer_matches_storage_plan,
)
from ogcat.models import (
    ArtifactDescriptor,
    ArtifactLocator,
    CatalogRecord,
    JsonValue,
    MetadataDict,
    normalize_metadata,
)
from ogcat.operation_helpers import (
    artifact_locator_from_context,
    naming_metadata_from_storage_plan,
    normalize_metadata_for_schema,
)
from ogcat.secondary_artifacts import SecondaryArtifactOperation, SecondaryArtifactResult
from ogcat.spec import RecordSchema
from ogcat.storage import StoragePlan, TargetKind, remove_target
from ogcat.transactions import OperationState, RollbackFailure, UnitOfWork
from ogcat.validation import ValidationReport

ArtifactLocatorFactory = Callable[[OperationContext], ArtifactLocator]
StoragePlanFactory = Callable[[OperationContext, ArtifactLocator], StoragePlan | None]
DerivedMetadataCollector = Callable[[OperationContext, ArtifactLocator], None]
_PhaseSetter = Callable[[str], None]
_PurgeArtifactAction = Literal["removed", "skipped", "failed"]


class OperationAuditEmitter(Protocol):
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


class MetadataValidationReporter(Protocol):
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


class ArtifactRecordBuilder(Protocol):
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
class OperationServices:
    """Catalog-owned services shared by internal operation runners."""

    catalog_root: Path
    hook_manager: HookManager
    schema_name: Callable[[str | None], str]
    metadata_validation_report: MetadataValidationReporter
    build_artifact_record: ArtifactRecordBuilder
    emit_operation_audit: OperationAuditEmitter
    emit_hook_lifecycle_audit: HookLifecycleCallback


@dataclass(slots=True)
class AddOperationRequest:
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
    materialization_intent: MaterializationIntent
    storage_plan_factory: StoragePlanFactory | None = None
    derived_metadata_collector: DerivedMetadataCollector | None = None
    secondary_artifact_operations: tuple[SecondaryArtifactOperation, ...] = ()


@dataclass(slots=True)
class RecordLifecycleOperationRequest:
    """Inputs required to run one record lifecycle operation."""

    transaction: UnitOfWork
    commit: bool
    operation_type: str
    record: CatalogRecord
    reason: str | None = None
    force: bool = False
    managed_roots: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class _PurgeArtifactResult:
    """Outcome of one artifact purge attempt."""

    artifact_id: str
    artifact_role: str
    action: _PurgeArtifactAction
    target_kind: TargetKind | None = None
    reason: str | None = None
    exception: Exception | None = None


@dataclass(frozen=True, slots=True)
class _PurgeOutcome:
    """Aggregate purge outcome returned before commit/raise handling."""

    incomplete_error: PurgeIncompleteError | None = None


@dataclass(slots=True)
class _AddOperationPlan:
    """Validated add-operation storage plan and its mutable hook context."""

    context: OperationContext
    locator: ArtifactLocator
    storage_plan: StoragePlan
    validation_report: ValidationReport


class OperationRunner(ABC):
    """Explicit command interface for internal operation runners."""

    @abstractmethod
    def run(self) -> CatalogRecord | None:
        """Run the operation and return the persisted or staged record."""
        ...


@dataclass(slots=True)
class AddOperationRunner(OperationRunner):
    """Template-method coordinator for one internal add-operation lifecycle."""

    dependencies: OperationServices
    request: AddOperationRequest

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
        context.user_metadata = normalize_metadata_for_schema(
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
        canonical_locator = artifact_locator_from_context(context)
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
            else materialization_plan_from_locator(
                locator,
                intent=self.request.materialization_intent,
            ).to_storage_plan()
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
            self.request.naming_metadata.update(naming_metadata_from_storage_plan(storage_plan))
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
        writer = self.request.materialization_intent.writer
        if add_plan.storage_plan.write_mode == "reference":
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
        if writer is None:
            raise ValueError(
                f"Storage plan with write mode {add_plan.storage_plan.write_mode!r} "
                "requires an artifact_writer."
            )
        validate_writer_matches_storage_plan(writer, add_plan.storage_plan)

        set_phase("artifact-write")
        writer.write(add_plan.context, add_plan.context.source, add_plan.locator)
        self.dependencies.emit_operation_audit(
            add_plan.context,
            event_type="write",
            message="Artifact write completed.",
            details={
                "write_phase": "artifact-write",
                "writer_type": type(writer).__name__,
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
        add_plan.context.user_metadata = normalize_metadata_for_schema(
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
        persisted = self._run_secondary_artifacts(
            add_plan=add_plan,
            record=persisted,
            set_phase=set_phase,
        )
        set_phase(HOOK_PHASES["after_record_write"].name)
        hook_dispatcher.after_record_write(add_plan.context)
        return persisted

    def _run_secondary_artifacts(
        self,
        *,
        add_plan: _AddOperationPlan,
        record: CatalogRecord,
        set_phase: _PhaseSetter,
    ) -> CatalogRecord:
        """Run required secondary artifact operations for the staged record."""
        persisted = record
        for operation in self.request.secondary_artifact_operations:
            set_phase(f"secondary-artifact:{operation.role}")
            result = operation.run(
                self.request.transaction,
                add_plan.context,
                persisted,
            )
            if result is None:
                continue
            persisted = self._apply_secondary_artifact_result(
                add_plan=add_plan,
                record=persisted,
                result=result,
            )
        return persisted

    def _apply_secondary_artifact_result(
        self,
        *,
        add_plan: _AddOperationPlan,
        record: CatalogRecord,
        result: SecondaryArtifactResult,
    ) -> CatalogRecord:
        """Persist secondary artifact metadata and emit its audit event."""
        persisted = record
        updated_record = record
        if result.naming_metadata_updates:
            naming_metadata = dict(record.naming_metadata)
            naming_metadata.update(result.naming_metadata_updates)
            updated_record = replace(updated_record, naming_metadata=naming_metadata)
        if result.artifacts:
            updated_record = replace(
                updated_record,
                artifacts=[*updated_record.artifacts, *result.artifacts],
            )
        if updated_record != record:
            persisted = self.request.transaction.update_staged_record(updated_record)
        self.dependencies.emit_operation_audit(
            add_plan.context,
            event_type=result.event_type,
            message=result.message,
            details=dict(result.audit_details),
            locator=record.locator,
        )
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


@dataclass(slots=True)
class RecordLifecycleOperationRunner(OperationRunner):
    """Coordinator for delete, restore, and purge record lifecycle operations."""

    dependencies: OperationServices
    request: RecordLifecycleOperationRequest

    def run(self) -> CatalogRecord | None:
        """Run the requested record lifecycle operation."""
        context = self._build_context()
        self.dependencies.emit_operation_audit(
            context,
            event_type="operation-started",
            message=f"Started {self.request.operation_type} operation.",
            details={
                "caller_owned_transaction": not self.request.commit,
                "reason_present": self.request.reason is not None,
                "record_status": self.request.record.status,
            },
            locator=self.request.record.locator,
        )
        result: CatalogRecord | None = None
        incomplete_purge_error: PurgeIncompleteError | None = None
        current_phase = "operation-started"
        try:
            if self.request.operation_type == "delete":
                current_phase = "tombstone"
                result = self._delete(context)
            elif self.request.operation_type == "restore":
                current_phase = "restore"
                result = self._restore(context)
            elif self.request.operation_type == "purge":
                current_phase = "purge"
                purge_outcome = self._purge(context)
                incomplete_purge_error = purge_outcome.incomplete_error
            else:
                raise ValueError(f"Unsupported record lifecycle operation: {self.request.operation_type}")
            current_phase = "commit"
            self._commit_if_owned(context)
        except Exception as exc:
            self._handle_error(error=exc, context=context, current_phase=current_phase)
            raise
        if incomplete_purge_error is not None:
            add_operation_note(incomplete_purge_error, context.operation_id)
            raise incomplete_purge_error
        return result

    def _build_context(self) -> OperationContext:
        """Build the mutable context used for lifecycle audit events."""
        record = self.request.record
        return OperationContext(
            catalog_root=self.dependencies.catalog_root,
            operation_id=self.request.transaction.operation_id,
            operation_type=self.request.operation_type,
            record_type=record.record_type,
            user_metadata=record.user_metadata,
            record_id=record.id,
            derived_metadata=record.derived_metadata,
            planned_locators=[record.locator],
            register_rollback=self.request.transaction.register_rollback,
            source=OperationSource(
                kind="catalog_record",
                path=record.path(),
                descriptor=None if record.id is None else f"record:{record.id}",
            ),
            storage_mode=record.storage_mode,
            original_path=record.original_path,
            original_filename=record.original_filename,
            suffixes=list(record.suffixes),
        )

    def _delete(self, context: OperationContext) -> CatalogRecord:
        """Tombstone the record and return the updated record."""
        record = self.request.record
        if record.status == "deleted":
            raise ValueError(f"Record is already deleted: {record.id}")
        updated = replace(
            record,
            status="deleted",
            lifecycle_metadata=_updated_lifecycle_metadata(
                record,
                operation_type="delete",
                operation_id=context.operation_id,
                reason=self.request.reason,
            ),
        )
        persisted = self.request.transaction.update_staged_record(updated)
        self.dependencies.emit_operation_audit(
            context,
            event_type="lifecycle",
            message="Record tombstoned.",
            details=_lifecycle_audit_details(
                before=record,
                after=persisted,
                reason=self.request.reason,
            ),
            locator=record.locator,
        )
        return persisted

    def _restore(self, context: OperationContext) -> CatalogRecord:
        """Restore a tombstoned record and return the updated record."""
        record = self.request.record
        if record.status != "deleted":
            raise ValueError(f"Record is not deleted: {record.id}")
        updated = replace(
            record,
            status="active",
            lifecycle_metadata=_updated_lifecycle_metadata(
                record,
                operation_type="restore",
                operation_id=context.operation_id,
                reason=self.request.reason,
            ),
        )
        persisted = self.request.transaction.update_staged_record(updated)
        self.dependencies.emit_operation_audit(
            context,
            event_type="lifecycle",
            message="Record restored.",
            details=_lifecycle_audit_details(
                before=record,
                after=persisted,
                reason=self.request.reason,
            ),
            locator=record.locator,
        )
        return persisted

    def _purge(self, context: OperationContext) -> _PurgeOutcome:
        """Remove managed artifacts, then hard-delete the record on full success."""
        record = self.request.record
        if record.status != "deleted" and not self.request.force:
            raise ValueError(f"Record must be deleted before purge: {record.id}")
        if record.id is None:
            raise ValueError("Cannot purge a record without an id.")
        results: list[_PurgeArtifactResult] = []
        for artifact in record.artifacts:
            try:
                results.append(self._purge_artifact(context, artifact))
            except Exception as exc:
                results.append(self._emit_artifact_failure(context, artifact, exception=exc))

        failed_results = _failed_purge_artifact_results(results)
        if failed_results:
            return _PurgeOutcome(
                incomplete_error=self._retain_incomplete_purge(
                    context,
                    results=results,
                    repository_error=None,
                )
            )

        try:
            self.request.transaction.repository.delete(record.id)
        except Exception as exc:
            return _PurgeOutcome(
                incomplete_error=self._retain_incomplete_purge(
                    context,
                    results=results,
                    repository_error=exc,
                )
            )

        self.dependencies.emit_operation_audit(
            context,
            event_type="purge",
            message="Record purged.",
            details={
                "record_id": record.id,
                "force": self.request.force,
                "artifact_count": len(record.artifacts),
                "purge_counts": _purge_counts(results),
                "artifact_summaries": _artifact_audit_summaries(record),
            },
            locator=record.locator,
        )
        return _PurgeOutcome()

    def _purge_artifact(
        self,
        context: OperationContext,
        artifact: ArtifactDescriptor,
    ) -> _PurgeArtifactResult:
        """Purge one managed artifact or audit why it was skipped."""
        if self.request.record.storage_mode == "reference":
            return self._emit_artifact_skip(context, artifact, reason="record storage mode is reference")
        locator = artifact.locator
        if locator is None:
            return self._emit_artifact_skip(context, artifact, reason="missing locator")
        target_path = locator.as_path()
        if target_path is None:
            return self._emit_artifact_skip(
                context,
                artifact,
                reason=f"unsupported locator kind: {locator.kind}",
            )
        if not _is_managed_path(target_path, managed_roots=self.request.managed_roots):
            return self._emit_artifact_skip(
                context,
                artifact,
                reason="locator is outside managed catalog roots",
            )

        target_kind = _target_kind_for_existing_path(target_path)
        try:
            remove_target(locator, target_kind=target_kind)
        except Exception as exc:
            return self._emit_artifact_failure(
                context,
                artifact,
                exception=exc,
                target_kind=target_kind,
            )
        self.dependencies.emit_operation_audit(
            context,
            event_type="purge_artifact",
            message="Managed artifact removed.",
            details={
                "artifact_id": artifact.id,
                "artifact_role": artifact.role,
                "target_kind": target_kind,
                "purge_action": "removed",
            },
            locator=locator,
        )
        return _PurgeArtifactResult(
            artifact_id=artifact.id,
            artifact_role=artifact.role,
            action="removed",
            target_kind=target_kind,
        )

    def _emit_artifact_skip(
        self,
        context: OperationContext,
        artifact: ArtifactDescriptor,
        *,
        reason: str,
    ) -> _PurgeArtifactResult:
        """Audit that one artifact was intentionally not purged."""
        self.dependencies.emit_operation_audit(
            context,
            event_type="purge_artifact",
            message="Artifact purge skipped.",
            details={
                "artifact_id": artifact.id,
                "artifact_role": artifact.role,
                "purge_action": "skipped",
                "reason": reason,
            },
            locator=artifact.locator,
        )
        return _PurgeArtifactResult(
            artifact_id=artifact.id,
            artifact_role=artifact.role,
            action="skipped",
            reason=reason,
        )

    def _emit_artifact_failure(
        self,
        context: OperationContext,
        artifact: ArtifactDescriptor,
        *,
        exception: Exception,
        target_kind: TargetKind | None = None,
    ) -> _PurgeArtifactResult:
        """Audit that one managed artifact could not be purged."""
        self.dependencies.emit_operation_audit(
            context,
            event_type="purge_artifact",
            level="error",
            message="Managed artifact purge failed.",
            details={
                "artifact_id": artifact.id,
                "artifact_role": artifact.role,
                "target_kind": target_kind,
                "purge_action": "failed",
            },
            exception=exception,
            locator=artifact.locator,
        )
        return _PurgeArtifactResult(
            artifact_id=artifact.id,
            artifact_role=artifact.role,
            action="failed",
            target_kind=target_kind,
            exception=exception,
        )

    def _retain_incomplete_purge(
        self,
        context: OperationContext,
        *,
        results: Sequence[_PurgeArtifactResult],
        repository_error: Exception | None,
    ) -> PurgeIncompleteError:
        """Persist an incomplete purge attempt and return the caller-facing error."""
        record = self.request.record
        updated = replace(
            record,
            artifacts=_artifacts_with_purge_states(record.artifacts, results),
            lifecycle_metadata=_incomplete_purge_lifecycle_metadata(
                record,
                operation_id=context.operation_id,
                results=results,
                repository_error=repository_error,
            ),
        )
        persisted = self.request.transaction.update_staged_record(updated)
        incomplete_error = PurgeIncompleteError(
            record_id=record.id,
            operation_id=context.operation_id,
            failed_artifact_ids=[result.artifact_id for result in _failed_purge_artifact_results(results)],
            repository_error=repository_error,
        )
        self.dependencies.emit_operation_audit(
            context,
            event_type="purge",
            level="error",
            message="Record purge incomplete; record retained.",
            details={
                "record_id": record.id,
                "force": self.request.force,
                "artifact_count": len(record.artifacts),
                "purge_counts": _purge_counts(results),
                "purge_status": "incomplete",
                "repository_delete_failed": repository_error is not None,
                "artifact_summaries": _artifact_audit_summaries(persisted),
                "purge_result_summaries": _purge_result_summaries(results),
            },
            exception=repository_error,
            locator=record.locator,
        )
        return incomplete_error

    def _commit_if_owned(self, context: OperationContext) -> None:
        """Commit internal lifecycle transactions."""
        if not self.request.commit:
            return
        self.request.transaction.commit()
        self.dependencies.emit_operation_audit(
            context,
            event_type="commit",
            message="Commit succeeded.",
            details={"transaction_state": self.request.transaction.state.value},
            locator=self.request.record.locator,
        )

    def _handle_error(
        self,
        *,
        error: Exception,
        context: OperationContext,
        current_phase: str,
    ) -> None:
        """Audit and roll back a failed lifecycle operation."""
        add_operation_note(error, context.operation_id)
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
            locator=self.request.record.locator,
        )
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
            if self.request.transaction.rollback_errors:
                self.dependencies.emit_operation_audit(
                    context,
                    event_type="rollback",
                    level="error",
                    message="Rollback completed with failures.",
                    details={
                        "rollback_phase": "failed",
                        "transaction_state": self.request.transaction.state.value,
                        "rollback_errors": _rollback_errors_audit_details(
                            self.request.transaction.rollback_errors
                        ),
                    },
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
        "artifact_uuid": plan.artifact_uuid,
        "primary_location": plan.primary_location,
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


def _updated_lifecycle_metadata(
    record: CatalogRecord,
    *,
    operation_type: str,
    operation_id: str,
    reason: str | None,
) -> MetadataDict:
    """Return lifecycle metadata with latest transition fields recorded."""
    timestamp = _utc_timestamp()
    metadata: dict[str, object] = dict(record.lifecycle_metadata)
    metadata[f"{operation_type}_operation_id"] = operation_id
    timestamp_key = "deleted_at" if operation_type == "delete" else f"{operation_type}d_at"
    metadata[timestamp_key] = timestamp
    if reason is not None:
        metadata[f"{operation_type}_reason"] = reason
    return normalize_metadata(metadata, field_name="lifecycle_metadata")


def _incomplete_purge_lifecycle_metadata(
    record: CatalogRecord,
    *,
    operation_id: str,
    results: Sequence[_PurgeArtifactResult],
    repository_error: Exception | None,
) -> MetadataDict:
    """Return lifecycle metadata describing the latest incomplete purge attempt."""
    counts = _purge_counts(results)
    metadata: dict[str, object] = dict(record.lifecycle_metadata)
    metadata["purge_operation_id"] = operation_id
    metadata["purge_attempted_at"] = _utc_timestamp()
    metadata["purge_status"] = "incomplete"
    metadata["purge_removed_count"] = counts["removed_count"]
    metadata["purge_skipped_count"] = counts["skipped_count"]
    metadata["purge_failure_count"] = counts["failed_count"]
    metadata["purge_repository_delete_failed"] = repository_error is not None
    metadata["purge_failures"] = _purge_result_summaries(
        _failed_purge_artifact_results(results),
        include_exception_details=False,
    )
    if repository_error is not None:
        metadata["purge_repository_error_type"] = type(repository_error).__name__
    return normalize_metadata(metadata, field_name="lifecycle_metadata")


def _lifecycle_audit_details(
    *,
    before: CatalogRecord,
    after: CatalogRecord,
    reason: str | None,
) -> dict[str, object]:
    """Return audit details for a lifecycle status transition."""
    return {
        "record_id": before.id,
        "status_before": before.status,
        "status_after": after.status,
        "reason_present": reason is not None,
        "metadata_keys": sorted(before.user_metadata),
        "derived_metadata_keys": sorted(before.derived_metadata),
        "artifact_summaries": _artifact_audit_summaries(before),
    }


def _artifact_audit_summaries(record: CatalogRecord) -> list[dict[str, object]]:
    """Return compact artifact descriptor summaries for audit logging."""
    return [
        {
            "id": artifact.id,
            "role": artifact.role,
            "state": artifact.state,
            "locator_kind": None if artifact.locator is None else artifact.locator.kind,
            "relative_path": None if artifact.locator is None else artifact.locator.relative_path,
        }
        for artifact in record.artifacts
    ]


def _failed_purge_artifact_results(
    results: Sequence[_PurgeArtifactResult],
) -> list[_PurgeArtifactResult]:
    """Return failed artifact purge outcomes."""
    return [result for result in results if result.action == "failed"]


def _purge_counts(results: Sequence[_PurgeArtifactResult]) -> dict[str, int]:
    """Return artifact purge outcome counts."""
    return {
        "removed_count": sum(1 for result in results if result.action == "removed"),
        "skipped_count": sum(1 for result in results if result.action == "skipped"),
        "failed_count": sum(1 for result in results if result.action == "failed"),
    }


def _purge_result_summaries(
    results: Sequence[_PurgeArtifactResult],
    *,
    include_exception_details: bool = True,
) -> list[dict[str, object]]:
    """Return compact purge outcome summaries for audit and lifecycle metadata."""
    summaries: list[dict[str, object]] = []
    for result in results:
        summary: dict[str, object] = {
            "artifact_id": result.artifact_id,
            "artifact_role": result.artifact_role,
            "purge_action": result.action,
            "target_kind": result.target_kind,
            "reason": result.reason,
        }
        if include_exception_details and result.exception is not None:
            summary["exception_type"] = type(result.exception).__name__
            summary["exception_message"] = str(result.exception)
        summaries.append(summary)
    return summaries


def _artifacts_with_purge_states(
    artifacts: Sequence[ArtifactDescriptor],
    results: Sequence[_PurgeArtifactResult],
) -> list[ArtifactDescriptor]:
    """Return artifact descriptors marked with retained purge outcome states."""
    states_by_artifact_id = {
        result.artifact_id: _artifact_state_for_purge_result(result)
        for result in results
        if result.action in {"removed", "failed"}
    }
    return [
        replace(artifact, state=states_by_artifact_id.get(artifact.id, artifact.state))
        for artifact in artifacts
    ]


def _artifact_state_for_purge_result(result: _PurgeArtifactResult) -> str:
    """Return the retained descriptor state for a purge outcome."""
    if result.action == "removed":
        return "purged"
    if result.action == "failed":
        return "purge_failed"
    return "available"


def _is_managed_path(path: Path, *, managed_roots: tuple[Path, ...]) -> bool:
    """Return whether a local path lives under one of the catalog-managed roots."""
    try:
        resolved_path = path.expanduser().resolve()
    except OSError:
        resolved_path = path.expanduser().absolute()
    for root in managed_roots:
        try:
            resolved_root = root.expanduser().resolve()
        except OSError:
            resolved_root = root.expanduser().absolute()
        if resolved_path == resolved_root or resolved_root in resolved_path.parents:
            return True
    return False


def _target_kind_for_existing_path(path: Path) -> TargetKind:
    """Infer the safest storage target kind for an existing local path."""
    if path.exists() and path.is_dir() and not path.is_symlink():
        return "directory"
    return "file"


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp string for lifecycle metadata."""
    timestamp = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return timestamp.replace("+00:00", "Z")
