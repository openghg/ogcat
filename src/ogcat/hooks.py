"""Lifecycle hook protocols and context objects for catalog operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ogcat.models import ArtifactLocator, MetadataDict
from ogcat.transactions import RollbackAction
from ogcat.validation import ValidationReport


@dataclass(slots=True)
class HookWarning:
    """A non-fatal hook warning.

    Args:
        hook_name: Name of the hook or plugin reporting the warning.
        message: Human-readable warning message.
        code: Stable machine-readable warning code.
    """

    hook_name: str
    message: str
    code: str = "hook.warning"

    def to_metadata(self) -> MetadataDict:
        """Convert the warning to JSON-compatible metadata."""
        return {
            "hook_name": self.hook_name,
            "message": self.message,
            "code": self.code,
        }


@dataclass(slots=True)
class OperationContext:
    """Mutable context passed to catalog lifecycle hooks.

    Args:
        catalog_root: Root path of the catalog.
        operation_id: Identifier shared with the transaction.
        operation: Catalog operation name, such as ``"add_file"``.
        record_type: Record type being created.
        user_metadata: User-supplied metadata, mutable by hooks before validation.
        derived_metadata: Derived metadata collected during the operation.
        planned_locators: Locators planned or supplied for the operation.
        register_rollback: Function hooks can call to participate in rollback.
        source_path: Optional local source path.
        source_descriptor: Optional non-path source description.
        storage_mode: Optional storage mode, such as ``"copy"`` or ``"move"``.
        original_path: Optional original path or URI.
        original_filename: Optional original filename.
        suffixes: Source suffixes associated with the artifact.
    """

    catalog_root: Path
    operation_id: str
    operation: str
    record_type: str
    user_metadata: MetadataDict
    derived_metadata: MetadataDict = field(default_factory=dict)
    planned_locators: list[ArtifactLocator] = field(default_factory=list)
    register_rollback: Callable[[RollbackAction | Callable[[], None]], RollbackAction] | None = None
    source_path: Path | None = None
    source_descriptor: str | None = None
    storage_mode: str | None = None
    original_path: str | Path | None = None
    original_filename: str | None = None
    suffixes: list[str] = field(default_factory=list)
    warnings: list[HookWarning] = field(default_factory=list)

    def add_warning(self, warning: HookWarning | str, *, hook_name: str = "hook") -> None:
        """Record a non-fatal warning for this operation."""
        if isinstance(warning, HookWarning):
            self.warnings.append(warning)
            return
        self.warnings.append(HookWarning(hook_name=hook_name, message=warning))

    def rollback(
        self,
        action: RollbackAction | Callable[[], None],
        *,
        description: str | None = None,
    ) -> RollbackAction:
        """Register a rollback action through the active catalog transaction."""
        if self.register_rollback is None:
            raise RuntimeError("No active rollback registration is available.")
        if callable(action) and not hasattr(action, "undo"):
            resolved_description = description or getattr(action, "__name__", "rollback action")
            return self.register_rollback(_DescribedRollbackAction(resolved_description, action))
        return self.register_rollback(action)


@dataclass(frozen=True, slots=True)
class _DescribedRollbackAction:
    """Rollback action wrapper used when hooks provide a plain callable."""

    description: str
    callback: Callable[[], None]

    def undo(self) -> None:
        """Run the wrapped rollback callback."""
        self.callback()


@runtime_checkable
class BeforeValidateMetadataHook(Protocol):
    """Hook called before schema metadata validation."""

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Inspect or mutate metadata before validation."""
        ...


@runtime_checkable
class AfterValidateMetadataHook(Protocol):
    """Hook called after schema metadata validation."""

    def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
        """Inspect validation results."""
        ...


@runtime_checkable
class PlanLocatorHook(Protocol):
    """Hook called after a locator has been planned or supplied."""

    def plan_locator(self, context: OperationContext) -> None:
        """Inspect or extend planned locators."""
        ...


@runtime_checkable
class BeforeWriteArtifactHook(Protocol):
    """Hook called before file or record write work."""

    def before_write_artifact(self, context: OperationContext) -> None:
        """Run before the artifact is written or staged."""
        ...


@runtime_checkable
class AfterWriteArtifactHook(Protocol):
    """Hook called after file or record write work."""

    def after_write_artifact(self, context: OperationContext) -> None:
        """Run after the artifact is written or staged."""
        ...


@runtime_checkable
class ExtractMetadataHook(Protocol):
    """Hook called during derived metadata extraction."""

    def extract_metadata(self, context: OperationContext) -> MetadataDict | None:
        """Return derived metadata to merge into the operation context."""
        ...


@runtime_checkable
class BeforeCommitHook(Protocol):
    """Hook called before committing the catalog transaction."""

    def before_commit(self, context: OperationContext) -> None:
        """Run before transaction commit."""
        ...


@runtime_checkable
class AfterCommitHook(Protocol):
    """Hook called after committing the catalog transaction."""

    def after_commit(self, context: OperationContext) -> None:
        """Run after transaction commit."""
        ...


@runtime_checkable
class RollbackHook(Protocol):
    """Hook called when an operation fails and rolls back."""

    def on_rollback(self, context: OperationContext, error: BaseException) -> None:
        """Run after rollback has been requested for a failed operation."""
        ...


@runtime_checkable
class ErrorHook(Protocol):
    """Hook called when an operation fails."""

    def on_error(self, context: OperationContext, error: BaseException) -> None:
        """Run when an operation fails."""
        ...


class HookManager:
    """Deterministic dispatcher for registered catalog hooks."""

    def __init__(self, hooks: Iterable[object] = ()) -> None:
        self._hooks = list(hooks)

    @property
    def hooks(self) -> tuple[object, ...]:
        """Registered hooks in dispatch order."""
        return tuple(self._hooks)

    def register(self, hook: object) -> object:
        """Register a hook object and return it for decorator-style usage."""
        self._hooks.append(hook)
        return hook

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Dispatch ``before_validate_metadata`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, BeforeValidateMetadataHook):
                hook.before_validate_metadata(context)

    def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
        """Dispatch ``after_validate_metadata`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, AfterValidateMetadataHook):
                hook.after_validate_metadata(context, report)

    def plan_locator(self, context: OperationContext) -> None:
        """Dispatch ``plan_locator`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, PlanLocatorHook):
                hook.plan_locator(context)

    def before_write_artifact(self, context: OperationContext) -> None:
        """Dispatch ``before_write_artifact`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, BeforeWriteArtifactHook):
                hook.before_write_artifact(context)

    def after_write_artifact(self, context: OperationContext) -> None:
        """Dispatch ``after_write_artifact`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, AfterWriteArtifactHook):
                hook.after_write_artifact(context)

    def extract_metadata(self, context: OperationContext) -> None:
        """Dispatch metadata extraction hooks and merge returned metadata."""
        for hook in self._hooks:
            if isinstance(hook, ExtractMetadataHook):
                extracted = hook.extract_metadata(context)
                if extracted is not None:
                    context.derived_metadata.update(extracted)

    def before_commit(self, context: OperationContext) -> None:
        """Dispatch ``before_commit`` hooks."""
        for hook in self._hooks:
            if isinstance(hook, BeforeCommitHook):
                hook.before_commit(context)

    def after_commit(self, context: OperationContext) -> None:
        """Dispatch ``after_commit`` hooks without failing committed work."""
        for hook in self._hooks:
            if isinstance(hook, AfterCommitHook):
                try:
                    hook.after_commit(context)
                except Exception as exc:
                    context.add_warning(
                        HookWarning(
                            hook_name=type(hook).__name__,
                            message=f"after_commit hook failed: {type(exc).__name__}: {exc}",
                            code="hook.after_commit_failed",
                        )
                    )

    def on_error(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch error hooks, preserving the original operation failure."""
        for hook in self._hooks:
            if isinstance(hook, ErrorHook):
                try:
                    hook.on_error(context, error)
                except Exception as exc:
                    error.add_note(f"error hook failed: {type(exc).__name__}: {exc}")

    def on_rollback(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch rollback hooks, preserving the original operation failure."""
        for hook in self._hooks:
            if isinstance(hook, RollbackHook):
                try:
                    hook.on_rollback(context, error)
                except Exception as exc:
                    error.add_note(f"rollback hook failed: {type(exc).__name__}: {exc}")


__all__ = [
    "AfterCommitHook",
    "AfterValidateMetadataHook",
    "AfterWriteArtifactHook",
    "BeforeCommitHook",
    "BeforeValidateMetadataHook",
    "BeforeWriteArtifactHook",
    "ErrorHook",
    "ExtractMetadataHook",
    "HookManager",
    "HookWarning",
    "OperationContext",
    "PlanLocatorHook",
    "RollbackHook",
]
