"""Structured catalog audit events and JSON Lines storage."""

from __future__ import annotations

import json
import os
import traceback as traceback_module
import warnings
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from ogcat.models import JsonValue, MetadataDict, normalize_metadata

AUDIT_LOG_RELATIVE_PATH = Path(".ogcat") / "logs" / "events.jsonl"
REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
)


@dataclass(slots=True)
class AuditEvent:
    """One structured audit event emitted by a catalog operation.

    Args:
        event_id: Unique event identifier.
        operation_id: Operation identifier shared with the active transaction.
        timestamp_utc: UTC ISO 8601 timestamp.
        level: Event severity, such as ``"info"``, ``"warning"``, or ``"error"``.
        event_type: Stable lifecycle event type.
        user_id: User associated with the operation.
        catalog_id: Catalog name or other stable catalog identifier.
        catalog_path: Filesystem path to the catalog root.
        record_id: Catalog record id touched by the event, if known.
        locator: Artifact locator summary, if known.
        message: Human-readable event summary.
        details: Redacted JSON-compatible event details.
        exception_type: Exception class name for failures.
        exception_message: Exception message for failures.
        traceback: Optional truncated traceback text.
    """

    operation_id: str
    level: str
    event_type: str
    message: str
    event_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp_utc: str = field(default_factory=lambda: _utc_timestamp())
    user_id: str | None = None
    catalog_id: str | None = None
    catalog_path: str | None = None
    record_id: str | None = None
    locator: MetadataDict | None = None
    details: MetadataDict = field(default_factory=dict)
    exception_type: str | None = None
    exception_message: str | None = None
    traceback: str | None = None

    def __post_init__(self) -> None:
        """Normalize event fields into JSON-compatible values."""
        self.level = self.level.lower()
        self.details = redact_sensitive_values(self.details)
        if self.locator is not None:
            self.locator = redact_sensitive_values(self.locator)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the event as a JSON-compatible dictionary."""
        payload: dict[str, JsonValue] = {
            "event_id": self.event_id,
            "operation_id": self.operation_id,
            "timestamp_utc": self.timestamp_utc,
            "level": self.level,
            "event_type": self.event_type,
            "user_id": self.user_id,
            "catalog_id": self.catalog_id,
            "catalog_path": self.catalog_path,
            "record_id": self.record_id,
            "locator": self.locator,
            "message": self.message,
            "details": self.details,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "traceback": self.traceback,
        }
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AuditEvent:
        """Build an event from a dictionary read from JSON Lines."""
        return cls(
            event_id=str(data.get("event_id") or uuid4().hex),
            operation_id=str(data.get("operation_id") or ""),
            timestamp_utc=str(data.get("timestamp_utc") or _utc_timestamp()),
            level=str(data.get("level") or "info"),
            event_type=str(data.get("event_type") or "unknown"),
            user_id=_optional_str(data.get("user_id")),
            catalog_id=_optional_str(data.get("catalog_id")),
            catalog_path=_optional_str(data.get("catalog_path")),
            record_id=_optional_str(data.get("record_id")),
            locator=_optional_metadata(data.get("locator"), field_name="locator"),
            message=str(data.get("message") or ""),
            details=_optional_metadata(data.get("details"), field_name="details") or {},
            exception_type=_optional_str(data.get("exception_type")),
            exception_message=_optional_str(data.get("exception_message")),
            traceback=_optional_str(data.get("traceback")),
        )

    @classmethod
    def from_exception(
        cls,
        *,
        operation_id: str,
        event_type: str,
        message: str,
        exception: BaseException,
        user_id: str | None = None,
        catalog_id: str | None = None,
        catalog_path: str | None = None,
        record_id: str | None = None,
        locator: MetadataDict | None = None,
        details: Mapping[str, object] | None = None,
        traceback_limit: int = 8,
    ) -> AuditEvent:
        """Build an error event from an exception."""
        traceback_text = "".join(
            traceback_module.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
                limit=traceback_limit,
            )
        ).strip()
        return cls(
            operation_id=operation_id,
            level="error",
            event_type=event_type,
            user_id=user_id,
            catalog_id=catalog_id,
            catalog_path=catalog_path,
            record_id=record_id,
            locator=locator,
            message=message,
            details=redact_sensitive_values({} if details is None else details),
            exception_type=type(exception).__name__,
            exception_message=str(exception),
            traceback=traceback_text,
        )


class AuditSink(Protocol):
    """Sink for structured audit events."""

    def emit(self, event: AuditEvent) -> None:
        """Persist or forward one audit event."""
        ...


@dataclass(frozen=True, slots=True)
class JsonlAuditSink:
    """Append-only JSON Lines audit sink under a catalog root.

    Args:
        catalog_root: Catalog root directory.
        relative_path: Log path relative to ``catalog_root``.
    """

    catalog_root: Path
    relative_path: Path = AUDIT_LOG_RELATIVE_PATH

    @property
    def path(self) -> Path:
        """Absolute path to the JSON Lines audit log."""
        return self.catalog_root / self.relative_path

    def emit(self, event: AuditEvent) -> None:
        """Append one event as a JSON line."""
        path = self.path
        os.makedirs(path.parent, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    def read_events(
        self,
        *,
        user_id: str | None = None,
        operation_id: str | None = None,
        record_id: str | None = None,
        level: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[AuditEvent]:
        """Read events from the sink with optional filters."""
        return read_audit_events(
            self.path,
            user_id=user_id,
            operation_id=operation_id,
            record_id=record_id,
            level=level,
            event_type=event_type,
            limit=limit,
        )


def read_audit_events(
    path: str | Path,
    *,
    user_id: str | None = None,
    operation_id: str | None = None,
    record_id: str | None = None,
    level: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
) -> list[AuditEvent]:
    """Read matching audit events from a JSON Lines file.

    Corrupt JSON lines and malformed event dictionaries are skipped with a
    runtime warning so one bad line does not hide the rest of the audit log.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative.")
    log_path = Path(path)
    if not log_path.exists():
        return []
    matches = [
        event
        for event in _iter_audit_events(log_path)
        if _matches_filters(
            event,
            user_id=user_id,
            operation_id=operation_id,
            record_id=record_id,
            level=level,
            event_type=event_type,
        )
    ]
    if limit is None:
        return matches
    if limit == 0:
        return []
    return matches[-limit:]


def redact_sensitive_values(value: object) -> MetadataDict:
    """Return a metadata dictionary with sensitive-looking keys redacted."""
    normalized = normalize_metadata(value, field_name="audit")
    return cast(MetadataDict, _redact_value(normalized, path_parts=()))


def exception_operation_id(exception: BaseException) -> str | None:
    """Return an operation id attached to an exception note, if present."""
    for note in getattr(exception, "__notes__", ()):
        if not isinstance(note, str):
            continue
        prefix = "ogcat operation_id: "
        if note.startswith(prefix):
            return note.removeprefix(prefix).strip() or None
    return None


def add_operation_note(exception: BaseException, operation_id: str) -> None:
    """Attach an operation-id note to an exception if one is not already present."""
    if exception_operation_id(exception) == operation_id:
        return
    exception.add_note(f"ogcat operation_id: {operation_id}")


def _iter_audit_events(path: Path) -> Iterator[AuditEvent]:
    """Yield well-formed audit events from a JSON Lines file."""
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
                if not isinstance(payload, dict):
                    raise TypeError(f"expected JSON object, got {type(payload).__name__}")
                yield AuditEvent.from_dict(payload)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                warnings.warn(
                    f"Skipping corrupt audit log line {path}:{line_number}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )


def _matches_filters(
    event: AuditEvent,
    *,
    user_id: str | None,
    operation_id: str | None,
    record_id: str | None,
    level: str | None,
    event_type: str | None,
) -> bool:
    """Return whether an event passes all supplied filters."""
    return all(
        (
            user_id is None or event.user_id == user_id,
            operation_id is None or event.operation_id == operation_id,
            record_id is None or event.record_id == record_id,
            level is None or event.level == level.lower(),
            event_type is None or event.event_type == event_type,
        )
    )


def _redact_value(value: JsonValue, *, path_parts: Iterable[str]) -> JsonValue:
    """Recursively redact sensitive values below sensitive-looking keys."""
    if _is_sensitive_path(path_parts):
        return REDACTED
    if isinstance(value, dict):
        redacted: dict[str, JsonValue] = {}
        for key, item in value.items():
            redacted[key] = _redact_value(item, path_parts=(*path_parts, key))
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, path_parts=path_parts) for item in value]
    return value


def _is_sensitive_path(path_parts: Iterable[str]) -> bool:
    """Return whether a metadata path should be redacted."""
    return any(_is_sensitive_key(part) for part in path_parts)


def _is_sensitive_key(key: str) -> bool:
    """Return whether one key looks sensitive."""
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _optional_str(value: object) -> str | None:
    """Return an optional string from a JSON value."""
    if value is None:
        return None
    return str(value)


def _optional_metadata(value: object, *, field_name: str) -> MetadataDict | None:
    """Return an optional metadata dictionary from a JSON value."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a dictionary, got {type(value).__name__}")
    return redact_sensitive_values(value)


def _utc_timestamp() -> str:
    """Return a UTC ISO 8601 timestamp."""
    timestamp = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return timestamp.replace("+00:00", "Z")


__all__ = [
    "AUDIT_LOG_RELATIVE_PATH",
    "REDACTED",
    "AuditEvent",
    "AuditSink",
    "JsonlAuditSink",
    "add_operation_note",
    "exception_operation_id",
    "read_audit_events",
    "redact_sensitive_values",
]
