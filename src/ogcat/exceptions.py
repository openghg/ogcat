"""Public exception types raised by ogcat APIs."""

from __future__ import annotations

from collections.abc import Sequence


class PurgeIncompleteError(ValueError):
    """Raised when purge cleanup is incomplete and the tombstone is retained.

    Args:
        record_id: Record id whose purge could not fully complete.
        operation_id: Audit operation id for the failed purge attempt.
        failed_artifact_ids: Managed artifact ids that could not be removed.
        repository_error: Optional repository hard-delete failure.
    """

    def __init__(
        self,
        *,
        record_id: str | None,
        operation_id: str,
        failed_artifact_ids: Sequence[str] = (),
        repository_error: BaseException | None = None,
    ) -> None:
        self.record_id = record_id
        self.operation_id = operation_id
        self.failed_artifact_ids = tuple(failed_artifact_ids)
        self.repository_error = repository_error
        problem_parts: list[str] = []
        if self.failed_artifact_ids:
            problem_parts.append("failed artifacts: " + ", ".join(self.failed_artifact_ids))
        if repository_error is not None:
            problem_parts.append(
                f"repository delete failed: {type(repository_error).__name__}: {repository_error}"
            )
        problems = "; ".join(problem_parts) or "unknown purge failure"
        super().__init__(f"Purge incomplete for record {record_id}; tombstone retained ({problems}).")
