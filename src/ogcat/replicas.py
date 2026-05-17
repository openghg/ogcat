"""Replica and generated view helpers.

Replica views are derived filesystem state.  They point at catalogued primary
artifacts but do not change catalog records.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from ogcat.models import CatalogRecord
from ogcat.naming import normalise_segment, render_template
from ogcat.replica_context import replica_template_context
from ogcat.replica_links import (
    relative_symlink_target,
    symlink_points_to,
)
from ogcat.replica_types import ReplicaMode, ReplicaRole


class ReplicaState(StrEnum):
    """Lifecycle or diagnostic state for one planned replica."""

    PLANNED = "planned"
    CREATED = "created"
    UP_TO_DATE = "up_to_date"
    COLLISION = "collision"
    UNSUPPORTED = "unsupported"
    MISSING_TARGET = "missing_target"
    ERROR = "error"


_BLOCKING_STATES = frozenset(
    {
        ReplicaState.COLLISION,
        ReplicaState.UNSUPPORTED,
        ReplicaState.MISSING_TARGET,
    }
)


@dataclass(frozen=True, slots=True)
class ReplicaPlanItem:
    """One planned or applied replica path.

    Args:
        record_id: Catalog record id when available.
        source_path: Primary local path the replica should point at.
        target_path: Replica path to create or validate.
        mode: Replica materialisation mode.
        role: Semantic role of the replica.
        state: Current plan or apply state.
        message: Optional human-readable diagnostic.
    """

    record_id: str | None
    source_path: Path | None
    target_path: Path
    mode: ReplicaMode = "symlink"
    role: ReplicaRole = "view_link"
    state: ReplicaState = ReplicaState.PLANNED
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ReplicaApplyResult:
    """Result from applying a replica view plan."""

    items: tuple[ReplicaPlanItem, ...]

    @property
    def created(self) -> list[ReplicaPlanItem]:
        """Return replicas created by this apply call."""
        return [item for item in self.items if item.state == ReplicaState.CREATED]

    @property
    def up_to_date(self) -> list[ReplicaPlanItem]:
        """Return replicas that already pointed at the desired target."""
        return [item for item in self.items if item.state == ReplicaState.UP_TO_DATE]

    @property
    def skipped(self) -> list[ReplicaPlanItem]:
        """Return replicas skipped or left unapplied when errors were skipped."""
        skipped_states = {
            ReplicaState.COLLISION,
            ReplicaState.ERROR,
            ReplicaState.UNSUPPORTED,
            ReplicaState.MISSING_TARGET,
        }
        return [item for item in self.items if item.state in skipped_states]

    @property
    def errors(self) -> list[ReplicaPlanItem]:
        """Return replicas with blocking errors."""
        error_states = {
            ReplicaState.COLLISION,
            ReplicaState.UNSUPPORTED,
            ReplicaState.MISSING_TARGET,
            ReplicaState.ERROR,
        }
        return [item for item in self.items if item.state in error_states]


@dataclass(frozen=True, slots=True)
class ReplicaViewPlan:
    """Dry-run plan for a generated replica view.

    Args:
        root: Root directory for generated view paths.
        template: Path template rendered for each record.
        mode: Replica materialisation mode.
        items: Planned replica items.
    """

    root: Path
    template: str
    mode: ReplicaMode
    items: tuple[ReplicaPlanItem, ...]

    @property
    def collisions(self) -> list[ReplicaPlanItem]:
        """Return planned items that cannot be applied because of collisions."""
        return [item for item in self.items if item.state == ReplicaState.COLLISION]

    @property
    def unsupported(self) -> list[ReplicaPlanItem]:
        """Return planned items whose source cannot be linked locally."""
        return [item for item in self.items if item.state == ReplicaState.UNSUPPORTED]

    @property
    def missing_targets(self) -> list[ReplicaPlanItem]:
        """Return planned items whose primary local target is missing."""
        return [item for item in self.items if item.state == ReplicaState.MISSING_TARGET]

    def apply(self, *, skip_errors: bool = False) -> ReplicaApplyResult:
        """Create symlink replicas described by this plan.

        Args:
            skip_errors: Whether to skip invalid items instead of raising before
                creating links.

        Returns:
            Per-item apply result.

        Raises:
            ValueError: If the plan contains blocking items and ``skip_errors``
                is false.
        """
        blocking = [item for item in self.items if item.state in _BLOCKING_STATES]
        if blocking and not skip_errors:
            raise ValueError(_format_blocking_items(blocking))

        results: list[ReplicaPlanItem] = []
        for item in self.items:
            if item.state in _BLOCKING_STATES:
                results.append(item)
                continue
            try:
                applied = _apply_symlink_item(item)
                if applied.state in _BLOCKING_STATES and not skip_errors:
                    raise ValueError(_format_blocking_items([applied]))
                results.append(applied)
            except OSError as exc:
                error_item = replace(item, state=ReplicaState.ERROR, message=str(exc))
                if not skip_errors:
                    raise ValueError(_format_blocking_items([error_item])) from exc
                results.append(error_item)
        return ReplicaApplyResult(tuple(results))


def plan_replica_view(
    *,
    root: str | Path,
    template: str,
    records: Sequence[CatalogRecord],
    mode: ReplicaMode = "symlink",
    role: ReplicaRole = "view_link",
) -> ReplicaViewPlan:
    """Plan a generated local replica view for records.

    Args:
        root: Root directory for rendered replica paths.
        template: Combined path template relative to ``root``.
        records: Catalog records to include in the view.
        mode: Replica materialisation mode. Only ``"symlink"`` is supported.
        role: Semantic role for planned replicas.

    Returns:
        Dry-run replica view plan.
    """
    if mode != "symlink":
        raise ValueError("Only symlink replica views are supported.")

    root_path = Path(root).expanduser().resolve()
    items = [
        _plan_record_replica(root=root_path, template=template, record=record, mode=mode, role=role)
        for record in records
    ]
    return ReplicaViewPlan(
        root=root_path,
        template=template,
        mode=mode,
        items=tuple(_mark_duplicate_targets(items)),
    )


def _plan_record_replica(
    *,
    root: Path,
    template: str,
    record: CatalogRecord,
    mode: ReplicaMode,
    role: ReplicaRole,
) -> ReplicaPlanItem:
    """Plan one record's local replica link."""
    target_path = _render_replica_path(root, template, replica_template_context(record))
    source_path = record.path()
    record_id = None if record.id is None else str(record.id)
    if source_path is None:
        return ReplicaPlanItem(
            record_id=record_id,
            source_path=None,
            target_path=target_path,
            mode=mode,
            role=role,
            state=ReplicaState.UNSUPPORTED,
            message=f"Record {record_id or '<unpersisted>'} is not path-backed.",
        )
    if not source_path.exists():
        return ReplicaPlanItem(
            record_id=record_id,
            source_path=source_path,
            target_path=target_path,
            mode=mode,
            role=role,
            state=ReplicaState.MISSING_TARGET,
            message=f"Primary target does not exist: {source_path}",
        )
    existing_state = _existing_target_state(target_path, source_path)
    if existing_state is not None:
        return replace(
            ReplicaPlanItem(
                record_id=record_id,
                source_path=source_path,
                target_path=target_path,
                mode=mode,
                role=role,
            ),
            state=existing_state[0],
            message=existing_state[1],
        )
    return ReplicaPlanItem(
        record_id=record_id,
        source_path=source_path,
        target_path=target_path,
        mode=mode,
        role=role,
    )


def _mark_duplicate_targets(items: list[ReplicaPlanItem]) -> list[ReplicaPlanItem]:
    """Mark plan-internal duplicate targets as collisions."""
    counts = Counter(item.target_path for item in items)
    duplicate_targets = {path for path, count in counts.items() if count > 1}
    if not duplicate_targets:
        return items
    marked: list[ReplicaPlanItem] = []
    for item in items:
        if item.target_path in duplicate_targets:
            marked.append(
                replace(
                    item,
                    state=ReplicaState.COLLISION,
                    message=f"Multiple records render to the same replica path: {item.target_path}",
                )
            )
        else:
            marked.append(item)
    return marked


def _apply_symlink_item(item: ReplicaPlanItem) -> ReplicaPlanItem:
    """Apply one symlink plan item."""
    if item.source_path is None:
        return replace(item, state=ReplicaState.UNSUPPORTED)
    if not item.source_path.exists():
        return replace(
            item,
            state=ReplicaState.MISSING_TARGET,
            message=f"Primary target does not exist: {item.source_path}",
        )
    existing_state = _existing_target_state(item.target_path, item.source_path)
    if existing_state is not None:
        return replace(item, state=existing_state[0], message=existing_state[1])
    item.target_path.parent.mkdir(parents=True, exist_ok=True)
    item.target_path.symlink_to(relative_symlink_target(item.source_path, link_path=item.target_path))
    return replace(item, state=ReplicaState.CREATED)


def _existing_target_state(target_path: Path, source_path: Path) -> tuple[ReplicaState, str] | None:
    """Return the state implied by an existing target path."""
    if not target_path.exists() and not target_path.is_symlink():
        return None
    if target_path.is_symlink() and symlink_points_to(target_path, source_path):
        return ReplicaState.UP_TO_DATE, f"Replica already points at {source_path}"
    return ReplicaState.COLLISION, f"Replica path already exists: {target_path}"


def _render_replica_path(root: Path, template: str, context: dict[str, object]) -> Path:
    """Render a safe relative replica path under ``root``."""
    rendered = render_template(template, context)
    parts = [part for part in rendered.replace("\\", "/").split("/") if part]
    if not parts:
        raise ValueError("Replica template rendered an empty path.")
    normalised_parts = [normalise_segment(part) for part in parts]
    if any(part in {".", ".."} for part in normalised_parts) or Path(rendered).is_absolute():
        raise ValueError(f"Replica template must render a relative path below the view root: {rendered}")
    return root.joinpath(*normalised_parts)


def _format_blocking_items(items: Sequence[ReplicaPlanItem]) -> str:
    """Format replica plan blockers for exceptions."""
    messages = [item.message or f"{item.state}: {item.target_path}" for item in items]
    return "Replica view contains blocking item(s): " + "; ".join(messages)


__all__ = [
    "ReplicaApplyResult",
    "ReplicaMode",
    "ReplicaPlanItem",
    "ReplicaRole",
    "ReplicaState",
    "ReplicaViewPlan",
    "plan_replica_view",
]
