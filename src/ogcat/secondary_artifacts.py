"""Secondary artifact operations coordinated by add-operation runners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from ogcat.hooks import OperationContext
from ogcat.models import DATA_ARTIFACT_ID, ArtifactDescriptor, ArtifactLocator, CatalogRecord, MetadataDict
from ogcat.naming import validate_human_readable_template_fields
from ogcat.replica_types import ReplicaMode
from ogcat.template_replicas import materialize_template_link_replica
from ogcat.transactions import UnitOfWork

SecondaryArtifactRole = Literal["template_link"]


@dataclass(frozen=True, slots=True)
class SecondaryArtifactResult:
    """Result from a materialized secondary artifact operation.

    Args:
        role: Semantic role of the secondary artifact.
        mode: Materialization mode used for the secondary artifact.
        message: Audit message to emit after successful materialization.
        event_type: Audit event type to emit after successful materialization.
        naming_metadata_updates: Metadata fields to merge onto the staged
            catalog record.
        artifacts: Artifact descriptors to append to the staged record.
        audit_details: Structured audit details for the operation.
    """

    role: SecondaryArtifactRole
    mode: ReplicaMode
    message: str
    event_type: str = "secondary_artifact"
    naming_metadata_updates: MetadataDict = field(default_factory=dict)
    artifacts: tuple[ArtifactDescriptor, ...] = ()
    audit_details: Mapping[str, object] = field(default_factory=dict)


class SecondaryArtifactOperation(Protocol):
    """Secondary artifact operation run after primary record staging."""

    @property
    def role(self) -> SecondaryArtifactRole:
        """Semantic role of the secondary artifact."""
        ...

    def run(
        self,
        transaction: UnitOfWork,
        context: OperationContext,
        record: CatalogRecord,
    ) -> SecondaryArtifactResult | None:
        """Materialize the secondary artifact for the staged record."""
        ...


@dataclass(frozen=True, slots=True)
class TemplateLinkSecondaryArtifact:
    """Template-link symlink secondary for UUID primary artifacts."""

    catalog_root: Path
    files_root: Path
    directory_template: str
    filename_template: str
    role: SecondaryArtifactRole = "template_link"
    mode: ReplicaMode = "symlink"

    def __post_init__(self) -> None:
        """Validate template-link fields before operation execution starts."""
        validate_human_readable_template_fields(self.directory_template, self.filename_template)

    def run(
        self,
        transaction: UnitOfWork,
        context: OperationContext,
        record: CatalogRecord,
    ) -> SecondaryArtifactResult | None:
        """Create the template-link replica and return record metadata updates."""
        materialized = materialize_template_link_replica(
            catalog_root=self.catalog_root,
            files_root=self.files_root,
            record=record,
            directory_template=self.directory_template,
            filename_template=self.filename_template,
            register_rollback=_rollback_registrar(transaction),
        )
        if materialized is None:
            return None
        return SecondaryArtifactResult(
            role=self.role,
            mode=self.mode,
            message="Template symlink replica created.",
            event_type="replica",
            naming_metadata_updates=materialized.naming_metadata,
            artifacts=(
                ArtifactDescriptor(
                    id="template_link",
                    role="view_link",
                    locator=ArtifactLocator.path(
                        materialized.target_path,
                        relative_path=materialized.catalog_relative_path,
                    ),
                    relationship={
                        "kind": "view_of",
                        "target_artifact_id": DATA_ARTIFACT_ID,
                        "view_role": self.role,
                    },
                    facets=[
                        {
                            "kind": "local_symlink",
                            "mode": self.mode,
                            "storage_relative_path": materialized.storage_relative_path,
                            "resolved_directory": materialized.resolved_directory,
                            "resolved_filename": materialized.resolved_filename,
                        }
                    ],
                ),
            ),
            audit_details={
                "replica_role": self.role,
                "replica_mode": self.mode,
                "replica_path": str(materialized.target_path),
                "primary_path": str(materialized.primary_path),
            },
        )


def _rollback_registrar(
    transaction: UnitOfWork,
) -> Callable[[Callable[[], None], str], object]:
    """Return the rollback callback shape expected by replica materializers."""

    def register_rollback(action: Callable[[], None], description: str) -> object:
        """Register one secondary artifact rollback action."""
        return transaction.register_rollback(action, description=description)

    return register_rollback


__all__ = [
    "SecondaryArtifactOperation",
    "SecondaryArtifactResult",
    "SecondaryArtifactRole",
    "TemplateLinkSecondaryArtifact",
]
