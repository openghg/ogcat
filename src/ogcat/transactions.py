"""Best-effort catalog transaction helpers.

The TinyDB-backed implementation uses compensating rollback actions. This is a
unit-of-work helper, not a true database transaction or ACID boundary.

``UnitOfWork`` coordinates record writes with cleanup callbacks registered by
artifact writers and hooks. Staged work starts in ``PLANNED`` state, moves to
``STAGED`` when a record or rollback action is registered, and becomes
``COMMITTED`` only after all catalog and artifact work has succeeded. If the
operation exits early, rollback actions run in reverse order and failures are
captured as ``RollbackFailure`` entries while preserving the original exception.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from uuid import uuid4

from ogcat.models import CatalogRecord
from ogcat.repository import CatalogRepository


class OperationState(StrEnum):
    """Lifecycle state for a best-effort catalog operation."""

    PLANNED = "planned"
    STAGED = "staged"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class RollbackAction(Protocol):
    """Action that can compensate for part of a staged operation."""

    @property
    def description(self) -> str:
        """Human-readable cleanup description."""
        ...

    def undo(self) -> None:
        """Undo the staged operation."""
        ...


@dataclass(frozen=True, slots=True)
class CallableRollbackAction:
    """Rollback action backed by a callable."""

    description: str
    callback: Callable[[], None]

    def undo(self) -> None:
        """Run the rollback callback."""
        self.callback()


@dataclass(frozen=True, slots=True)
class RollbackFailure:
    """A rollback action failure captured during best-effort cleanup."""

    description: str
    exception: BaseException


class UnitOfWork:
    """Best-effort unit of work for catalog record staging and cleanup.

    Args:
        repository: Repository used for staged catalog record writes.
        operation_id: Optional externally supplied operation id. If omitted, a
            random id is generated for audit/debug correlation.
    """

    def __init__(self, repository: CatalogRepository, *, operation_id: str | None = None) -> None:
        self.repository = repository
        self.operation_id = operation_id or uuid4().hex
        self.state = OperationState.PLANNED
        self.rollback_errors: list[RollbackFailure] = []
        self._rollback_actions: list[RollbackAction] = []

    def __enter__(self) -> UnitOfWork:
        """Enter the unit-of-work context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        """Rollback uncommitted work and preserve any active exception."""
        if self.state is not OperationState.COMMITTED:
            self.rollback(original_exception=exc)
        return False

    def register_rollback(
        self,
        action: RollbackAction | Callable[[], None],
        *,
        description: str | None = None,
    ) -> RollbackAction:
        """Register an action to run if the unit of work rolls back.

        Args:
            action: A rollback action object or a no-argument callable.
            description: Human-readable cleanup description for callable
                actions. Ignored for action objects that already carry one.

        Returns:
            The registered rollback action.
        """
        if self.state is OperationState.COMMITTED:
            raise RuntimeError("Cannot register rollback action after commit.")
        if callable(action) and not hasattr(action, "undo"):
            rollback_action: RollbackAction = CallableRollbackAction(
                description=description or getattr(action, "__name__", "rollback action"),
                callback=action,
            )
        else:
            rollback_action = cast(RollbackAction, action)
        self._rollback_actions.append(rollback_action)
        if self.state is OperationState.PLANNED:
            self.state = OperationState.STAGED
        return rollback_action

    def insert_staged_record(self, record: CatalogRecord) -> CatalogRecord:
        """Insert a staged record and delete it if the unit of work rolls back."""
        if self.state is OperationState.COMMITTED:
            raise RuntimeError("Cannot stage record after commit.")
        persisted = self.repository.insert(record)
        if persisted.id is None:
            raise RuntimeError("Repository returned a persisted record without an id.")
        record_id = persisted.id
        self.register_rollback(
            lambda: self.repository.delete(record_id),
            description=f"delete staged catalog record {record_id}",
        )
        self.state = OperationState.STAGED
        return persisted

    def stage_record(self, record: CatalogRecord) -> CatalogRecord:
        """Alias for ``insert_staged_record``."""
        return self.insert_staged_record(record)

    def update_staged_record(self, record: CatalogRecord) -> CatalogRecord:
        """Update a staged record and restore the previous version on rollback.

        Args:
            record: Replacement record to persist. It must include an existing
                repository id.

        Returns:
            The replacement record.

        Raises:
            RuntimeError: If the transaction has already committed.
            ValueError: If the replacement record has no id.
            KeyError: If no stored record exists for the supplied id.
        """
        if self.state is OperationState.COMMITTED:
            raise RuntimeError("Cannot update record after commit.")
        if record.id is None:
            raise ValueError("Cannot update a record without an id.")
        previous = self.repository.get(record.id)
        if previous is None:
            raise KeyError(f"Record not found: {record.id}")
        self.register_rollback(
            lambda: self.repository.update(previous),
            description=f"restore catalog record {record.id}",
        )
        self.repository.update(record)
        self.state = OperationState.STAGED
        return record

    def commit(self) -> None:
        """Commit staged work and discard rollback actions."""
        self._rollback_actions.clear()
        self.state = OperationState.COMMITTED

    def rollback(self, *, original_exception: BaseException | None = None) -> None:
        """Run registered rollback actions in reverse order.

        Args:
            original_exception: Optional exception that triggered rollback. When
                supplied, rollback failure summaries are added as exception notes
                so the original failure remains visible to callers.
        """
        if self.state is OperationState.COMMITTED:
            return
        while self._rollback_actions:
            action = self._rollback_actions.pop()
            try:
                action.undo()
            except Exception as exc:
                self.rollback_errors.append(RollbackFailure(action.description, exc))

        if self.rollback_errors:
            self.state = OperationState.FAILED
            if original_exception is not None:
                for failure in self.rollback_errors:
                    original_exception.add_note(
                        f"rollback failed for {failure.description}: "
                        f"{type(failure.exception).__name__}: {failure.exception}"
                    )
        else:
            self.state = OperationState.ROLLED_BACK
