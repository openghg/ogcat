"""Lifecycle hooks, writer protocols, and operation context objects.

Hooks are structural protocols: a plugin object participates in a lifecycle
phase by implementing the corresponding method, such as
``before_validate_metadata(context)`` or ``extract_metadata(context)``. Hook
methods receive an :class:`OperationContext`, a mutable object that carries the
catalog root, operation id, source description, planned locators, user metadata,
derived metadata, rollback registrar, and accumulated hook warnings.

The design intentionally keeps hook classes lightweight. Plugin authors do not
subclass a base class; they implement one or more protocol methods, usually with
``context: OperationContext`` and ``-> None`` or ``-> MetadataDict | None`` type
hints. ``HookManager`` stores the long-lived hook registry. Operation-scoped
``HookDispatcher`` instances dispatch protocols in deterministic registration
order, merge returned derived metadata, record non-fatal after-commit failures
as warnings, and preserve the original exception when error or rollback hooks
fail.

``_HOOK_PHASE_SEQUENCE`` defines formal hook phases, and ``HOOK_PHASES`` is the
public mapping used during dispatch and validation. ``HOOK_METHOD_NAMES`` is
derived from that source of truth for compatibility with existing validation
callers.

Artifact writers use the same context model. Any object satisfying
:class:`ArtifactWriter` can materialise an :class:`ogcat.models.ArtifactLocator`
from an :class:`OperationSource` before the catalog record is committed.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, cast, runtime_checkable

from ogcat.models import ArtifactLocator, MetadataDict
from ogcat.storage import StoragePlan
from ogcat.transactions import RollbackAction
from ogcat.validation import ValidationReport

HookLifecycleStage = Literal["started", "completed", "failed"]
_HookMethod = Callable[..., object]


@dataclass(frozen=True, slots=True)
class HookPhase:
    """Formal metadata for one hook dispatch phase.

    Args:
        name: Hook method name used to select participating hooks.
        label: Human-readable phase label for diagnostics.
    """

    name: str
    label: str


# Source of truth for lifecycle hook phase registration.
_HOOK_PHASE_SEQUENCE = (
    HookPhase("before_validate_metadata", "before-validate metadata"),
    HookPhase("after_validate_metadata", "after-validate metadata"),
    HookPhase("resolve_artifact_locator", "artifact locator resolution"),
    HookPhase("before_record_write", "before-record-write"),
    HookPhase("after_record_write", "after-record-write"),
    HookPhase("extract_metadata", "metadata extraction"),
    HookPhase("before_commit", "before-commit"),
    HookPhase("after_commit", "after-commit"),
    HookPhase("on_error", "operation error"),
    HookPhase("on_rollback", "operation rollback"),
)
HOOK_PHASES: Mapping[str, HookPhase] = MappingProxyType({phase.name: phase for phase in _HOOK_PHASE_SEQUENCE})
HOOK_METHOD_NAMES = tuple(HOOK_PHASES)


@dataclass(frozen=True, slots=True)
class HookLifecycleEvent:
    """Lifecycle notification emitted around one hook dispatch phase.

    Args:
        phase: Formal hook phase metadata.
        stage: Dispatch stage.
        context: Operation context supplied to the hook phase.
        hook_count: Number of hooks participating in this phase.
        warnings_added: Number of context warnings added during dispatch.
        error: Exception raised by the phase, if dispatch failed.
    """

    phase: HookPhase
    stage: HookLifecycleStage
    context: OperationContext
    hook_count: int
    warnings_added: int = 0
    error: BaseException | None = None


HookLifecycleCallback = Callable[[HookLifecycleEvent], None]


def coerce_hook_iterable(value: object, *, label: str) -> list[object]:
    """Return a validated list of hooks from an iterable input.

    Args:
        value: Candidate iterable of hook objects.
        label: User-facing input name used in error messages.

    Returns:
        Validated hook objects as a defensive list copy.

    Raises:
        TypeError: If the value is not an iterable of hook objects.
    """
    expected = (
        "a HookManager or iterable of hook objects"
        if label == "hooks"
        else "a PluginRegistry or iterable of hook objects"
    )
    if isinstance(value, str | bytes):
        raise TypeError(f"{label} must be {expected}, got {type(value).__name__}")
    if not isinstance(value, Iterable):
        raise TypeError(f"{label} must be {expected}, got {type(value).__name__}")
    return validate_hook_objects(value, label=label)


def validate_hook_objects(hooks: Iterable[object], *, label: str) -> list[object]:
    """Validate hook objects and return a defensive list copy.

    Args:
        hooks: Hook objects to validate.
        label: User-facing input name used in error messages.

    Returns:
        Validated hook objects as a defensive list copy.

    Raises:
        TypeError: If any hook object has no supported hook methods or a
            matching hook method is not callable.
    """
    validated = list(hooks)
    method_list = ", ".join(HOOK_METHOD_NAMES)
    missing = object()
    for index, hook in enumerate(validated):
        implemented_methods: list[str] = []
        for method_name in HOOK_METHOD_NAMES:
            method = getattr(hook, method_name, missing)
            if method is missing:
                continue
            if not callable(method):
                raise TypeError(
                    f"{label} item {index} has non-callable hook method "
                    f"{method_name!r}; got {type(method).__name__}"
                )
            implemented_methods.append(method_name)
        if not implemented_methods:
            raise TypeError(
                f"{label} item {index} must provide at least one callable hook method "
                f"({method_list}); got {type(hook).__name__}"
            )
    return validated


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


class RollbackRegistrar(Protocol):
    """Function signature used to register rollback work with a transaction."""

    def __call__(
        self,
        action: RollbackAction | Callable[[], None],
        *,
        description: str | None = None,
    ) -> RollbackAction:
        """Register a rollback action."""
        ...


@dataclass(slots=True)
class OperationSource:
    """Description of the artifact source for a catalog operation.

    Args:
        kind: Short source kind, such as ``"local_file"`` or ``"external"``.
        path: Optional local source path.
        descriptor: Optional non-path source description or URI.
        metadata: Source-specific JSON-compatible metadata.
        payload: Optional in-memory Python object for writer helpers.
    """

    kind: str
    path: Path | None = None
    descriptor: str | None = None
    metadata: MetadataDict = field(default_factory=dict)
    payload: object | None = None


class ArtifactWriter(Protocol):
    """Plugin-facing writer that materialises artifact data before record write."""

    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        """Write artifact data from source to target."""
        ...


@dataclass(slots=True)
class OperationContext:
    """Mutable context passed to catalog lifecycle hooks.

    Hooks exert their effect by mutating documented fields on this object,
    raising an exception, or registering rollback work. `user_metadata` may be
    mutated before validation. `planned_locators` may be changed during
    `resolve_artifact_locator`; the first locator is treated as canonical.
    `derived_metadata` may be updated during metadata extraction.

    Args:
        catalog_root: Root path of the catalog.
        operation_id: Identifier shared with the transaction.
        operation_type: Catalog operation name, such as ``"add_file"``.
        record_type: Record type being created.
        user_metadata: User-supplied metadata, mutable by hooks before validation.
        record_id: Persisted record id, set after a record write succeeds.
        derived_metadata: Derived metadata collected during the operation.
        planned_locators: Locators planned or supplied for the operation.
        register_rollback: Low-level rollback registrar. Hook authors should
            normally call ``context.rollback(...)`` instead.
        source: Artifact source description for this operation.
        storage_mode: Optional storage mode, such as ``"copy"`` or ``"move"``.
        storage_plan: Optional planned artifact storage decision.
        original_path: Optional original path or URI.
        original_filename: Optional original filename.
        suffixes: Source suffixes associated with the artifact.
    """

    catalog_root: Path
    operation_id: str
    operation_type: str
    record_type: str
    user_metadata: MetadataDict
    record_id: str | None = None
    derived_metadata: MetadataDict = field(default_factory=dict)
    planned_locators: list[ArtifactLocator] = field(default_factory=list)
    register_rollback: RollbackRegistrar | None = None
    source: OperationSource = field(default_factory=lambda: OperationSource(kind="unknown"))
    storage_mode: str | None = None
    storage_plan: StoragePlan | None = None
    original_path: str | Path | None = None
    original_filename: str | None = None
    suffixes: list[str] = field(default_factory=list)
    warnings: list[HookWarning] = field(default_factory=list)

    @property
    def source_path(self) -> Path | None:
        """Optional local source path, kept for compatibility with existing hooks."""
        return self.source.path

    @source_path.setter
    def source_path(self, value: Path | None) -> None:
        """Set the optional local source path on the operation source."""
        self.source.path = value

    @property
    def source_descriptor(self) -> str | None:
        """Optional source description, kept for compatibility with existing hooks."""
        return self.source.descriptor

    @source_descriptor.setter
    def source_descriptor(self, value: str | None) -> None:
        """Set the optional source description on the operation source."""
        self.source.descriptor = value

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
        return self.register_rollback(action, description=description)


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
class ResolveArtifactLocatorHook(Protocol):
    """Hook called after an artifact locator has been proposed."""

    def resolve_artifact_locator(self, context: OperationContext) -> None:
        """Inspect, replace, or extend planned artifact locators."""
        ...


@runtime_checkable
class BeforeRecordWriteHook(Protocol):
    """Hook called before writing the catalog record."""

    def before_record_write(self, context: OperationContext) -> None:
        """Run before the catalog record is written."""
        ...


@runtime_checkable
class AfterRecordWriteHook(Protocol):
    """Hook called after writing the catalog record."""

    def after_record_write(self, context: OperationContext) -> None:
        """Run after the catalog record is written."""
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


class HookDispatcher:
    """Operation-scoped dispatcher for registered catalog hooks.

    The dispatcher snapshots hooks for one operation.

    Args:
        hooks: Hook objects available to this operation.
        notify: Optional lifecycle callback for this operation.
    """

    def __init__(self, hooks: Iterable[object], notify: HookLifecycleCallback | None = None) -> None:
        self._hooks = tuple(hooks)
        self._notify = notify

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Dispatch ``before_validate_metadata`` hooks."""
        self._dispatch_context_method(HOOK_PHASES["before_validate_metadata"], context)

    def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
        """Dispatch ``after_validate_metadata`` hooks."""

        def invoke(_hook: object, method: _HookMethod) -> None:
            method(context, report)

        self._dispatch(HOOK_PHASES["after_validate_metadata"], context, invoke)

    def resolve_artifact_locator(self, context: OperationContext) -> None:
        """Dispatch ``resolve_artifact_locator`` hooks."""
        self._dispatch_context_method(HOOK_PHASES["resolve_artifact_locator"], context)

    def before_record_write(self, context: OperationContext) -> None:
        """Dispatch ``before_record_write`` hooks."""
        self._dispatch_context_method(HOOK_PHASES["before_record_write"], context)

    def after_record_write(self, context: OperationContext) -> None:
        """Dispatch ``after_record_write`` hooks."""
        self._dispatch_context_method(HOOK_PHASES["after_record_write"], context)

    def extract_metadata(self, context: OperationContext) -> None:
        """Dispatch metadata extraction hooks and merge returned metadata."""

        def invoke(_hook: object, method: _HookMethod) -> None:
            extracted = method(context)
            if extracted is not None:
                context.derived_metadata.update(cast(MetadataDict, extracted))

        self._dispatch(HOOK_PHASES["extract_metadata"], context, invoke)

    def before_commit(self, context: OperationContext) -> None:
        """Dispatch ``before_commit`` hooks."""
        self._dispatch_context_method(HOOK_PHASES["before_commit"], context)

    def after_commit(self, context: OperationContext) -> None:
        """Dispatch ``after_commit`` hooks without failing committed work."""

        def invoke(hook: object, method: _HookMethod) -> None:
            try:
                method(context)
            except Exception as exc:
                warning = HookWarning(
                    hook_name=type(hook).__name__,
                    message=f"after_commit hook failed: {type(exc).__name__}: {exc}",
                    code="hook.after_commit_failed",
                )
                context.add_warning(warning)
                warnings.warn(
                    f"{warning.hook_name}: {warning.message}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        self._dispatch(HOOK_PHASES["after_commit"], context, invoke)

    def on_error(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch error hooks, preserving the original operation failure."""

        def invoke(_hook: object, method: _HookMethod) -> None:
            try:
                method(context, error)
            except Exception as exc:
                error.add_note(f"error hook failed: {type(exc).__name__}: {exc}")

        self._dispatch(HOOK_PHASES["on_error"], context, invoke)

    def on_rollback(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch rollback hooks, preserving the original operation failure."""

        def invoke(_hook: object, method: _HookMethod) -> None:
            try:
                method(context, error)
            except Exception as exc:
                error.add_note(f"rollback hook failed: {type(exc).__name__}: {exc}")

        self._dispatch(HOOK_PHASES["on_rollback"], context, invoke)

    def _dispatch(
        self,
        phase: HookPhase,
        context: OperationContext,
        invoke: Callable[[object, _HookMethod], None],
    ) -> None:
        """Dispatch one phase and emit lifecycle events."""
        matching_hooks = self._matching_hooks(phase)
        hook_count = len(matching_hooks)
        if hook_count == 0:
            return

        self._notify_event(
            HookLifecycleEvent(
                phase=phase,
                stage="started",
                context=context,
                hook_count=hook_count,
            )
        )
        warning_count_before = len(context.warnings)
        try:
            for hook, method in matching_hooks:
                invoke(hook, method)
        except Exception as exc:
            self._notify_event(
                HookLifecycleEvent(
                    phase=phase,
                    stage="failed",
                    context=context,
                    hook_count=hook_count,
                    warnings_added=len(context.warnings) - warning_count_before,
                    error=exc,
                )
            )
            raise

        self._notify_event(
            HookLifecycleEvent(
                phase=phase,
                stage="completed",
                context=context,
                hook_count=hook_count,
                warnings_added=len(context.warnings) - warning_count_before,
            )
        )

    def _dispatch_context_method(self, phase: HookPhase, context: OperationContext) -> None:
        """Dispatch a phase whose hook methods only accept a context."""

        def invoke(_hook: object, method: _HookMethod) -> None:
            method(context)

        self._dispatch(phase, context, invoke)

    def _matching_hooks(self, phase: HookPhase) -> tuple[tuple[object, _HookMethod], ...]:
        """Return registered hooks that implement a lifecycle phase."""
        hooks: list[tuple[object, _HookMethod]] = []
        for hook in self._hooks:
            method = getattr(hook, phase.name, None)
            if callable(method):
                hooks.append((hook, method))
        return tuple(hooks)

    def _notify_event(self, event: HookLifecycleEvent) -> None:
        """Notify one lifecycle event without failing hook dispatch."""
        if self._notify is None:
            return
        try:
            self._notify(event)
        except Exception as exc:
            warnings.warn(
                f"hook lifecycle callback failed: {type(exc).__name__}: {exc}",
                RuntimeWarning,
                stacklevel=3,
            )


class HookManager:
    """Long-lived registry for catalog hooks."""

    def __init__(self, hooks: Iterable[object] = ()) -> None:
        self._hooks = validate_hook_objects(hooks, label="hooks")

    @property
    def hooks(self) -> tuple[object, ...]:
        """Registered hooks in dispatch order."""
        return tuple(self._hooks)

    def register(self, hook: object) -> object:
        """Register a hook object and return it for decorator-style usage."""
        validated = validate_hook_objects([hook], label="hooks")
        self._hooks.append(validated[0])
        return validated[0]

    def dispatcher(self, notify: HookLifecycleCallback | None = None) -> HookDispatcher:
        """Return an operation-scoped hook dispatcher."""
        return HookDispatcher(self._hooks, notify=notify)

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Dispatch ``before_validate_metadata`` hooks."""
        self.dispatcher().before_validate_metadata(context)

    def after_validate_metadata(self, context: OperationContext, report: ValidationReport) -> None:
        """Dispatch ``after_validate_metadata`` hooks."""
        self.dispatcher().after_validate_metadata(context, report)

    def resolve_artifact_locator(self, context: OperationContext) -> None:
        """Dispatch ``resolve_artifact_locator`` hooks."""
        self.dispatcher().resolve_artifact_locator(context)

    def before_record_write(self, context: OperationContext) -> None:
        """Dispatch ``before_record_write`` hooks."""
        self.dispatcher().before_record_write(context)

    def after_record_write(self, context: OperationContext) -> None:
        """Dispatch ``after_record_write`` hooks."""
        self.dispatcher().after_record_write(context)

    def extract_metadata(self, context: OperationContext) -> None:
        """Dispatch metadata extraction hooks and merge returned metadata."""
        self.dispatcher().extract_metadata(context)

    def before_commit(self, context: OperationContext) -> None:
        """Dispatch ``before_commit`` hooks."""
        self.dispatcher().before_commit(context)

    def after_commit(self, context: OperationContext) -> None:
        """Dispatch ``after_commit`` hooks without failing committed work."""
        self.dispatcher().after_commit(context)

    def on_error(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch error hooks, preserving the original operation failure."""
        self.dispatcher().on_error(context, error)

    def on_rollback(self, context: OperationContext, error: BaseException) -> None:
        """Dispatch rollback hooks, preserving the original operation failure."""
        self.dispatcher().on_rollback(context, error)


__all__ = [
    "AfterCommitHook",
    "AfterValidateMetadataHook",
    "AfterRecordWriteHook",
    "ArtifactWriter",
    "BeforeCommitHook",
    "BeforeValidateMetadataHook",
    "BeforeRecordWriteHook",
    "ErrorHook",
    "ExtractMetadataHook",
    "HOOK_METHOD_NAMES",
    "HOOK_PHASES",
    "HookDispatcher",
    "HookLifecycleCallback",
    "HookLifecycleEvent",
    "HookLifecycleStage",
    "HookManager",
    "HookPhase",
    "HookWarning",
    "OperationContext",
    "OperationSource",
    "ResolveArtifactLocatorHook",
    "RollbackHook",
    "RollbackRegistrar",
    "coerce_hook_iterable",
    "validate_hook_objects",
]
