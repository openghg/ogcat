"""Application services below the public catalog facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ogcat.extractors import extract_derived_metadata
from ogcat.hooks import ArtifactWriter, OperationContext, OperationSource
from ogcat.materialization import (
    storage_plan_for_locator,
    target_kind_from_writer,
    write_mode_from_writer,
)
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataDict
from ogcat.operation_helpers import storage_plan_with_locator
from ogcat.operation_runner import (
    AddOperationRequest,
    ArtifactLocatorFactory,
    DerivedMetadataCollector,
    RecordLifecycleOperationRequest,
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
        derived_metadata: MetadataDict | None = None,
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
            return plan_primary(context).locator

        def plan_local_file_storage(
            context: OperationContext,
            locator: ArtifactLocator,
        ) -> StoragePlan:
            """Build the storage plan for a managed local file."""
            primary = plan_primary(context)
            return primary.to_storage_plan(
                locator=locator,
                target_kind=target_kind_from_writer(artifact_writer),
                write_mode=write_mode_from_writer(artifact_writer),
                ogcat_owned=True,
                artifact_uuid=context.operation_id if primary_location == "template" else None,
            )

        def collect_file_metadata(context: OperationContext, locator: ArtifactLocator) -> None:
            """Collect generic derived metadata from the written file."""
            locator_path = locator.as_path()
            if locator_path is not None:
                for key, value in extract_derived_metadata(locator_path).items():
                    context.derived_metadata.setdefault(key, value)

        source_description = OperationSource(kind="local_file", path=source, descriptor=str(source))
        artifact_writer = _managed_path_writer(source=source, operation=operation)
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
                derived_metadata={} if derived_metadata is None else derived_metadata,
                naming_metadata=naming_metadata,
                time_added=time_added,
                source=source_description,
                locator_factory=resolve_local_file_locator,
                artifact_writer=artifact_writer,
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

        def plan_artifact_storage(
            _context: OperationContext,
            canonical_locator: ArtifactLocator,
        ) -> StoragePlan:
            """Use the explicit storage decision or derive one from the writer."""
            if storage_plan is not None:
                return storage_plan_with_locator(storage_plan, canonical_locator)
            return storage_plan_for_locator(canonical_locator, writer=artifact_writer)

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
            artifact_writer=artifact_writer,
            storage_plan_factory=plan_artifact_storage,
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
        artifact_writer: ArtifactWriter | None,
        storage_plan_factory: StoragePlanFactory,
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
            artifact_writer=artifact_writer,
            storage_plan_factory=storage_plan_factory,
            derived_metadata_collector=derived_metadata_collector,
            secondary_artifact_operations=secondary_artifact_operations,
        )
        result = self.catalog._build_add_operation_runner(request).run()
        if result is None:
            raise RuntimeError("add operation did not return a record.")
        return result

    def delete(
        self,
        *,
        record_id: object,
        reason: str | None,
        transaction: UnitOfWork,
        commit: bool,
    ) -> CatalogRecord:
        """Run the record tombstone operation."""
        record = self.catalog._require_record(record_id)
        request = RecordLifecycleOperationRequest(
            transaction=transaction,
            commit=commit,
            operation_type="delete",
            record=record,
            reason=reason,
        )
        result = self.catalog._build_record_lifecycle_operation_runner(request).run()
        if result is None:
            raise RuntimeError("delete operation did not return a record.")
        return result

    def restore(
        self,
        *,
        record_id: object,
        reason: str | None,
        transaction: UnitOfWork,
        commit: bool,
    ) -> CatalogRecord:
        """Run the record restore operation."""
        record = self.catalog._require_record(record_id)
        request = RecordLifecycleOperationRequest(
            transaction=transaction,
            commit=commit,
            operation_type="restore",
            record=record,
            reason=reason,
        )
        result = self.catalog._build_record_lifecycle_operation_runner(request).run()
        if result is None:
            raise RuntimeError("restore operation did not return a record.")
        return result

    def purge(
        self,
        *,
        record_id: object,
        force: bool,
        transaction: UnitOfWork,
        commit: bool,
    ) -> None:
        """Run the permanent record purge operation."""
        record = self.catalog._require_record(record_id)
        request = RecordLifecycleOperationRequest(
            transaction=transaction,
            commit=commit,
            operation_type="purge",
            record=record,
            force=force,
            managed_roots=(
                self.catalog.root / self.catalog.spec.files_root,
                self.catalog.root / self.catalog.spec.objects_root,
            ),
        )
        self.catalog._build_record_lifecycle_operation_runner(request).run()

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
