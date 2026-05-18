"""Application services below the public catalog facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ogcat.extractors import extract_derived_metadata
from ogcat.hooks import ArtifactWriter, OperationContext, OperationSource
from ogcat.materialization import (
    MaterializationIntent,
    MaterializationPlan,
    reference_intent,
    storage_plan_intent,
    writer_intent,
)
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataDict
from ogcat.operation_helpers import storage_plan_with_locator
from ogcat.operation_runner import (
    AddOperationRequest,
    ArtifactLocatorFactory,
    DerivedMetadataCollector,
    StoragePlanFactory,
)
from ogcat.secondary_artifacts import SecondaryArtifactOperation, TemplateLinkSecondaryArtifact
from ogcat.spec import RecordSchema
from ogcat.storage import StoragePlan
from ogcat.storage_planning import (
    PrimaryLocation,
    PrimaryStoragePlanningContext,
    PrimaryStoragePlanResult,
    plan_primary_storage,
)
from ogcat.transactions import UnitOfWork
from ogcat.writers import (
    CopyArtifactWriter,
    CopyDirectoryArtifactWriter,
    MoveArtifactWriter,
    MoveDirectoryArtifactWriter,
)

if TYPE_CHECKING:
    from ogcat.catalog import Catalog


@dataclass(slots=True)
class CatalogApplication:
    """Coordinate catalog operations below the public Python API."""

    catalog: Catalog

    def add_file(
        self,
        *,
        source: Path,
        metadata: MetadataDict,
        schema: RecordSchema,
        schema_record_type: str | None,
        record_type: str,
        directory_template: str,
        filename_template: str,
        operation: str,
        primary_location: PrimaryLocation,
        create_template_replica: bool,
        time_added: str,
    ) -> CatalogRecord:
        """Run the managed local-file add operation."""
        files_root = self.catalog.root / self.catalog.spec.files_root
        objects_root = self.catalog.root / self.catalog.spec.objects_root
        naming_metadata: MetadataDict = {
            "record_schema": "default" if schema_record_type is None else schema_record_type,
            "directory_template": directory_template,
            "filename_template": filename_template,
            "primary_location": primary_location,
        }
        planned_primary: PrimaryStoragePlanResult | None = None

        def plan_primary(context: OperationContext) -> PrimaryStoragePlanResult:
            """Plan the managed-file primary location for this operation."""
            return plan_primary_storage(
                PrimaryStoragePlanningContext(
                    catalog_root=self.catalog.root,
                    files_root=files_root,
                    objects_root=objects_root,
                    operation_id=context.operation_id,
                    metadata=context.user_metadata,
                    directory_template=directory_template,
                    filename_template=filename_template,
                    source_path=source,
                    storage_root=None,
                    date_added=time_added[:10],
                    primary_location=primary_location,
                )
            )

        def resolve_local_file_locator(context: OperationContext) -> ArtifactLocator:
            """Resolve the managed-file storage path for this operation."""
            nonlocal planned_primary
            planned_primary = plan_primary(context)
            if primary_location == "template":
                naming_metadata["artifact_uuid"] = context.operation_id
            return planned_primary.locator

        def plan_local_file_storage(
            context: OperationContext,
            locator: ArtifactLocator,
        ) -> StoragePlan:
            """Build the storage plan for a managed local file."""
            primary = planned_primary or plan_primary(context)
            artifact_uuid = context.operation_id if primary_location == "template" else None
            primary_target = primary.to_materialization_target(
                locator=locator,
                target_kind=materialization_intent.target_kind,
                artifact_uuid=artifact_uuid,
            )
            return MaterializationPlan(
                primary_target=primary_target,
                intent=materialization_intent,
            ).to_storage_plan()

        def collect_file_metadata(context: OperationContext, locator: ArtifactLocator) -> None:
            """Collect generic derived metadata from the written file."""
            locator_path = locator.as_path()
            if locator_path is not None:
                context.derived_metadata.update(extract_derived_metadata(locator_path))

        source_description = OperationSource(kind="local_file", path=source, descriptor=str(source))
        artifact_writer = _managed_path_writer(source=source, operation=operation)
        materialization_intent = writer_intent(artifact_writer)
        secondary_artifact_operations = self._template_link_secondary_artifacts(
            primary_location=primary_location,
            create_template_replica=create_template_replica,
            directory_template=directory_template,
            filename_template=filename_template,
        )

        with self.catalog.transaction() as transaction:
            return self.run_add_operation(
                transaction=transaction,
                commit=True,
                operation_type="add_file",
                record_type=record_type,
                schema=schema,
                schema_record_type=schema_record_type,
                metadata=metadata,
                storage_mode=operation,
                original_path=source,
                original_filename=source.name,
                suffixes=source.suffixes,
                derived_metadata={},
                naming_metadata=naming_metadata,
                time_added=time_added,
                source=source_description,
                locator_factory=resolve_local_file_locator,
                materialization_intent=materialization_intent,
                storage_plan_factory=plan_local_file_storage,
                derived_metadata_collector=collect_file_metadata,
                secondary_artifact_operations=secondary_artifact_operations,
            )

    def add_artifact(
        self,
        *,
        transaction: UnitOfWork,
        commit: bool,
        record_type: str,
        locator: ArtifactLocator,
        metadata: MetadataDict,
        storage_mode: str | None,
        original_path: str | Path | None,
        original_filename: str | None,
        suffixes: list[str] | None,
        derived_metadata: MetadataDict,
        naming_metadata: MetadataDict | None,
        time_added: str | None,
        source: OperationSource | None,
        artifact_writer: ArtifactWriter | None,
        storage_plan: StoragePlan | None,
        schema: RecordSchema,
    ) -> CatalogRecord:
        """Run the general add-artifact operation."""
        operation_source = source or OperationSource(
            kind="external",
            path=locator.as_path(),
            descriptor=locator.value,
        )
        if storage_plan is not None:
            materialization_intent = storage_plan_intent(storage_plan, writer=artifact_writer)
        else:
            materialization_intent = (
                reference_intent() if artifact_writer is None else writer_intent(artifact_writer)
            )
        return self.run_add_operation(
            transaction=transaction,
            commit=commit,
            operation_type="add_artifact",
            record_type=record_type,
            schema=schema,
            schema_record_type=record_type,
            metadata=metadata,
            storage_mode=storage_mode,
            original_path=original_path,
            original_filename=original_filename,
            suffixes=suffixes,
            derived_metadata=derived_metadata,
            naming_metadata=naming_metadata,
            time_added=time_added,
            source=operation_source,
            locator_factory=lambda context: locator,
            materialization_intent=materialization_intent,
            storage_plan_factory=(
                None
                if storage_plan is None
                else lambda context, canonical_locator: storage_plan_with_locator(
                    storage_plan,
                    canonical_locator,
                )
            ),
        )

    def run_add_operation(
        self,
        *,
        transaction: UnitOfWork,
        commit: bool,
        operation_type: str,
        record_type: str,
        schema: RecordSchema,
        schema_record_type: str | None,
        metadata: MetadataDict,
        storage_mode: str | None,
        original_path: str | Path | None,
        original_filename: str | None,
        suffixes: list[str] | None,
        derived_metadata: MetadataDict,
        naming_metadata: MetadataDict | None,
        time_added: str | None,
        source: OperationSource,
        locator_factory: ArtifactLocatorFactory,
        materialization_intent: MaterializationIntent,
        storage_plan_factory: StoragePlanFactory | None = None,
        derived_metadata_collector: DerivedMetadataCollector | None = None,
        secondary_artifact_operations: tuple[SecondaryArtifactOperation, ...] = (),
    ) -> CatalogRecord:
        """Build and run a shared add-operation request."""
        request = AddOperationRequest(
            transaction=transaction,
            commit=commit,
            operation_type=operation_type,
            record_type=record_type,
            schema=schema,
            schema_record_type=schema_record_type,
            metadata=metadata,
            storage_mode=storage_mode,
            original_path=original_path,
            original_filename=original_filename,
            suffixes=suffixes,
            derived_metadata=derived_metadata,
            naming_metadata=naming_metadata,
            time_added=time_added,
            source=source,
            locator_factory=locator_factory,
            materialization_intent=materialization_intent,
            storage_plan_factory=storage_plan_factory,
            derived_metadata_collector=derived_metadata_collector,
            secondary_artifact_operations=secondary_artifact_operations,
        )
        return self.catalog._build_add_operation_runner(request).run()

    def _template_link_secondary_artifacts(
        self,
        *,
        primary_location: PrimaryLocation,
        create_template_replica: bool,
        directory_template: str,
        filename_template: str,
    ) -> tuple[SecondaryArtifactOperation, ...]:
        """Return default secondary artifacts for UUID primary file adds."""
        if primary_location != "uuid" or not create_template_replica:
            return ()
        return (
            TemplateLinkSecondaryArtifact(
                catalog_root=self.catalog.root,
                files_root=self.catalog.root / self.catalog.spec.files_root,
                directory_template=directory_template,
                filename_template=filename_template,
            ),
        )


def _managed_path_writer(*, source: Path, operation: str) -> ArtifactWriter:
    """Return the managed-ingest writer for the source path shape."""
    if source.is_dir():
        return CopyDirectoryArtifactWriter() if operation == "copy" else MoveDirectoryArtifactWriter()
    return CopyArtifactWriter() if operation == "copy" else MoveArtifactWriter()


__all__ = ["CatalogApplication"]
