"""Main catalog API."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ogcat.extractors import extract_derived_metadata
from ogcat.hooks import HookManager, OperationContext
from ogcat.models import ArtifactLocator, CatalogRecord, JsonValue, MetadataDict
from ogcat.naming import build_naming_context, render_storage_location
from ogcat.plugins import PluginRegistry
from ogcat.repository import CatalogRepository
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.tinydb_repository import TinyDbCatalogRepository
from ogcat.transactions import OperationState, UnitOfWork
from ogcat.validation import ValidationReport, validate_metadata

ArtifactLocatorFactory = Callable[[OperationContext], ArtifactLocator]
ArtifactWriter = Callable[[OperationContext, ArtifactLocator], None]
DerivedMetadataCollector = Callable[[OperationContext, ArtifactLocator], None]


@dataclass(slots=True)
class Catalog:
    """User-facing catalog API bound to a catalog root."""

    root: Path
    spec: CatalogSpec
    repository: CatalogRepository
    hook_manager: HookManager = field(default_factory=HookManager)

    @classmethod
    def create(
        cls,
        root: str | Path,
        spec: CatalogSpec,
        *,
        plugins: PluginRegistry | None = None,
        hooks: HookManager | None = None,
    ) -> Catalog:
        """Create a new catalog directory and write its specification."""
        root_path = Path(root).expanduser().resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        spec.write(root_path / "catalog.json")
        (root_path / spec.files_root).mkdir(parents=True, exist_ok=True)
        repository = _open_repository(root_path, spec)
        return cls(
            root=root_path,
            spec=spec,
            repository=repository,
            hook_manager=_coerce_hook_manager(plugins=plugins, hooks=hooks),
        )

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        plugins: PluginRegistry | None = None,
        hooks: HookManager | None = None,
    ) -> Catalog:
        """Open an existing catalog from disk."""
        root_path = Path(root).expanduser().resolve()
        spec = CatalogSpec.read(root_path / "catalog.json")
        repository = _open_repository(root_path, spec)
        return cls(
            root=root_path,
            spec=spec,
            repository=repository,
            hook_manager=_coerce_hook_manager(plugins=plugins, hooks=hooks),
        )

    def add_file(
        self,
        path: str | Path,
        metadata: MetadataDict | None = None,
        operation: str | None = None,
        record_type: str | None = None,
    ) -> CatalogRecord:
        """Add a file to the catalog using managed copy or move."""
        source = Path(path).expanduser().resolve()
        metadata_input = {} if metadata is None else metadata
        schema = self._select_schema(record_type, require_known=record_type is not None)
        schema_name = self._schema_name(record_type)
        metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        resolved_record_type = "managed_file" if record_type is None else record_type
        directory_template = _require_template(schema.directory_template, field_name="directory_template")
        filename_template = _require_template(schema.filename_template, field_name="filename_template")
        chosen_operation = operation or self.spec.default_operation
        if chosen_operation not in {"copy", "move"}:
            raise ValueError(f"Unsupported operation: {chosen_operation}")

        timestamp = _utc_timestamp()
        files_root = self.root / self.spec.files_root
        naming_metadata: MetadataDict = {
            "record_schema": "default" if record_type is None else record_type,
            "directory_template": directory_template,
            "filename_template": filename_template,
        }

        def resolve_local_file_locator(context: OperationContext) -> ArtifactLocator:
            """Resolve the managed-file storage path for this operation."""
            naming_context = build_naming_context(
                record_id=context.operation_id,
                original_path=source,
                metadata=context.user_metadata,
                date_added=timestamp[:10],
            )
            target, rel_path, resolved_filename = render_storage_location(
                files_root=files_root,
                directory_template=directory_template,
                filename_template=filename_template,
                context=naming_context,
            )
            naming_metadata["resolved_filename"] = resolved_filename
            return ArtifactLocator.path(target, relative_path=rel_path)

        def write_local_file(context: OperationContext, locator: ArtifactLocator) -> None:
            """Copy or move the source file to the canonical path locator."""
            target = _path_from_locator(locator)
            target.parent.mkdir(parents=True, exist_ok=True)
            if chosen_operation == "copy":
                context.rollback(
                    lambda path=target: path.unlink(missing_ok=True),
                    description=f"remove copied file {target}",
                )
                shutil.copy2(source, target)
            else:
                context.rollback(
                    lambda source_path=source, target_path=target: _rollback_moved_file(
                        source_path=source_path,
                        target_path=target_path,
                    ),
                    description=f"restore moved file from {target} to {source}",
                )
                shutil.move(str(source), str(target))

        def collect_file_metadata(context: OperationContext, locator: ArtifactLocator) -> None:
            """Collect generic derived metadata from the written file."""
            context.derived_metadata.update(extract_derived_metadata(_path_from_locator(locator)))

        with self.transaction() as transaction:
            return self._run_add_operation(
                transaction=transaction,
                commit=True,
                operation_type="add_file",
                record_type=resolved_record_type,
                schema=schema,
                schema_record_type=record_type,
                metadata=metadata,
                storage_mode=chosen_operation,
                original_path=source,
                original_filename=source.name,
                suffixes=source.suffixes,
                derived_metadata={},
                naming_metadata=naming_metadata,
                time_added=timestamp,
                source_path=source,
                source_descriptor=str(source),
                locator_factory=resolve_local_file_locator,
                artifact_writer=write_local_file,
                derived_metadata_collector=collect_file_metadata,
            )

    def add_artifact(
        self,
        *,
        record_type: str,
        locator: ArtifactLocator,
        metadata: MetadataDict | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: MetadataDict | None = None,
        naming_metadata: MetadataDict | None = None,
        time_added: str | None = None,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Add an artifact record without performing any file operation.

        This is the minimal general record API. `add_file()` remains the managed
        ingest convenience wrapper that prepares a path-backed locator and then
        delegates here. Pass a catalog transaction to stage the record as part
        of a larger best-effort unit of work.
        """
        metadata_input = {} if metadata is None else metadata
        derived_metadata = {} if derived_metadata is None else dict(derived_metadata)
        schema = self._select_schema(record_type, require_known=False)
        schema_name = self._schema_name(record_type)
        metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        if transaction is not None:
            if transaction.repository is not self.repository:
                raise ValueError("Transaction is bound to a different catalog repository.")
            return self._add_artifact_in_transaction(
                transaction=transaction,
                commit=False,
                record_type=record_type,
                locator=locator,
                metadata=metadata,
                storage_mode=storage_mode,
                original_path=original_path,
                original_filename=original_filename,
                suffixes=suffixes,
                derived_metadata=derived_metadata,
                naming_metadata=naming_metadata,
                time_added=time_added,
                schema=schema,
            )
        with self.transaction() as unit_of_work:
            return self._add_artifact_in_transaction(
                transaction=unit_of_work,
                commit=True,
                record_type=record_type,
                locator=locator,
                metadata=metadata,
                storage_mode=storage_mode,
                original_path=original_path,
                original_filename=original_filename,
                suffixes=suffixes,
                derived_metadata=derived_metadata,
                naming_metadata=naming_metadata,
                time_added=time_added,
                schema=schema,
            )

    @contextmanager
    def transaction(self) -> Iterator[UnitOfWork]:
        """Create a best-effort unit of work for composed catalog operations.

        The current TinyDB backend uses staged writes and compensating rollback
        actions. This context manager does not provide true database
        transactions or ACID semantics.
        """
        with UnitOfWork(self.repository) as transaction:
            yield transaction

    def add_artifacts(
        self,
        artifacts: list[dict[str, object]],
    ) -> list[CatalogRecord]:
        """Add multiple artifact records.

        Each item should provide the same keyword-style fields accepted by
        `add_artifact()`. This keeps the public artifact API small while allowing
        batch-oriented callers to avoid one-at-a-time repository writes.
        """
        validated_items = [_validate_artifact_batch_item(item, index) for index, item in enumerate(artifacts)]

        records: list[CatalogRecord] = []
        for index, validated in enumerate(validated_items):
            try:
                locator = _coerce_artifact_locator(validated["locator"])
            except TypeError as exc:
                raise TypeError(f"artifact batch item {index}: invalid locator: {exc}") from exc
            except ValueError as exc:
                raise ValueError(f"artifact batch item {index}: invalid locator: {exc}") from exc

            record_type = str(validated["record_type"])
            metadata = validated.get("metadata")  # type: ignore[assignment]
            schema = self._select_schema(record_type, require_known=False)
            try:
                self._validate_metadata(
                    schema=schema,
                    metadata={} if metadata is None else metadata,  # type: ignore[arg-type]
                    record_type=record_type,
                )
            except TypeError as exc:
                raise TypeError(f"artifact batch item {index}: {exc}") from exc
            except ValueError as exc:
                raise ValueError(f"artifact batch item {index}: {exc}") from exc
            records.append(
                self._build_artifact_record(
                    record_type=record_type,
                    locator=locator,
                    metadata=metadata,  # type: ignore[arg-type]
                    storage_mode=(
                        None if validated.get("storage_mode") is None else str(validated["storage_mode"])
                    ),
                    original_path=validated.get("original_path"),  # type: ignore[arg-type]
                    original_filename=(
                        None
                        if validated.get("original_filename") is None
                        else str(validated["original_filename"])
                    ),
                    suffixes=validated.get("suffixes"),  # type: ignore[arg-type]
                    derived_metadata=validated.get("derived_metadata"),  # type: ignore[arg-type]
                    naming_metadata=validated.get("naming_metadata"),  # type: ignore[arg-type]
                    time_added=(
                        None if validated.get("time_added") is None else str(validated["time_added"])
                    ),
                )
            )
        return self.repository.insert_many(records)

    def search(
        self,
        *,
        where: dict[str, object] | None = None,
        contains: dict[str, str] | None = None,
        regex: dict[str, str] | None = None,
        ignore_case: bool = False,
    ) -> list[CatalogRecord]:
        """Search catalog records using equality, substring, and regex filters."""
        return self.repository.search(
            where=where,
            contains=contains,
            regex=regex,
            ignore_case=ignore_case,
            resolution_order=self.spec.field_resolution_order,
        )

    def describe(self) -> dict[str, object]:
        """Return a simple serialisable summary of the catalog."""
        db_path = self.root / self.spec.db_path
        files_root = self.root / self.spec.files_root
        default_schema = self.spec.get_schema()
        return {
            "catalog_name": self.spec.catalog_name,
            "root_path": str(self.root),
            "backend": self.spec.db_backend,
            "database_path": str(db_path),
            "files_root": str(files_root),
            "default_operation": self.spec.default_operation,
            "directory_template": default_schema.directory_template,
            "filename_template": default_schema.filename_template,
            "field_resolution_order": list(self.spec.field_resolution_order),
            "record_count": len(self.repository.all()),
            "has_metadata_fields": self._has_metadata_fields(),
            "record_schemas": self.list_record_schemas(),
        }

    def list_metadata_fields(self, record_type: str | None = None) -> list[dict[str, JsonValue]]:
        """Return serialisable metadata field descriptions."""
        schema = self._select_schema(record_type, require_known=record_type is not None)
        return [field_description.to_dict() for field_description in schema.metadata_fields]

    def get_schema(self, record_type: str | None = None) -> dict[str, object]:
        """Return a serialisable schema description."""
        return self._select_schema(record_type, require_known=record_type is not None).to_dict()

    def list_record_schemas(self) -> list[str]:
        """Return available named record schema names."""
        return self.spec.list_record_schemas()

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        return self.repository.get(record_id)

    def path(self, record_id: str) -> Path | None:
        """Return the stored path for a path-backed record, if present."""
        record = self.get(record_id)
        if record is None:
            return None
        return record.path()

    def _build_artifact_record(
        self,
        *,
        record_id: str | None = None,
        record_type: str,
        locator: ArtifactLocator,
        metadata: MetadataDict | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: MetadataDict | None = None,
        naming_metadata: MetadataDict | None = None,
        time_added: str | None = None,
    ) -> CatalogRecord:
        """Build an artifact record without persisting it."""
        resolved_time_added = time_added or _utc_timestamp()
        if original_path is None:
            resolved_original_path = None
        elif isinstance(original_path, Path):
            resolved_original_path = str(original_path)
        else:
            resolved_original_path = original_path
        locator_path = locator.as_path()

        return CatalogRecord(
            id=record_id,
            catalog=self.spec.catalog_name,
            time_added=resolved_time_added,
            record_type=record_type,
            locator=locator,
            stored_abspath=str(locator_path) if locator_path is not None else None,
            stored_relpath=locator.relative_path,
            storage_mode=storage_mode,
            original_path=resolved_original_path,
            original_filename=original_filename,
            suffixes=[] if suffixes is None else list(suffixes),
            user_metadata={} if metadata is None else dict(metadata),
            derived_metadata={} if derived_metadata is None else dict(derived_metadata),
            naming_metadata={} if naming_metadata is None else dict(naming_metadata),
        )

    def _add_artifact_in_transaction(
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
        schema: RecordSchema,
    ) -> CatalogRecord:
        """Add an artifact record within an active transaction."""
        return self._run_add_operation(
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
            source_path=locator.as_path(),
            source_descriptor=locator.value,
            locator_factory=lambda context: locator,
        )

    def _run_add_operation(
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
        source_path: Path | None,
        source_descriptor: str | None,
        locator_factory: ArtifactLocatorFactory,
        artifact_writer: ArtifactWriter | None = None,
        derived_metadata_collector: DerivedMetadataCollector | None = None,
    ) -> CatalogRecord:
        """Run the shared add operation lifecycle for file and record-only adds."""
        hook_context = OperationContext(
            catalog_root=self.root,
            operation_id=transaction.operation_id,
            operation_type=operation_type,
            record_type=record_type,
            user_metadata=metadata,
            derived_metadata=derived_metadata,
            register_rollback=transaction.register_rollback,
            source_path=source_path,
            source_descriptor=source_descriptor,
            storage_mode=storage_mode,
            original_path=original_path,
            original_filename=original_filename,
            suffixes=[] if suffixes is None else list(suffixes),
        )
        try:
            self.hook_manager.before_validate_metadata(hook_context)
            validation_report = self._metadata_validation_report(
                schema=schema,
                metadata=hook_context.user_metadata,
                record_type=schema_record_type,
            )
            self.hook_manager.after_validate_metadata(hook_context, validation_report)
            validation_report.raise_for_errors()
            hook_context.planned_locators = [locator_factory(hook_context)]
            self.hook_manager.resolve_artifact_locator(hook_context)
            canonical_locator = _artifact_locator_from_context(hook_context)
            hook_context.planned_locators[0] = canonical_locator
            if artifact_writer is not None:
                artifact_writer(hook_context, canonical_locator)
            if derived_metadata_collector is not None:
                derived_metadata_collector(hook_context, canonical_locator)
            self.hook_manager.extract_metadata(hook_context)
            record = self._build_artifact_record(
                record_type=record_type,
                locator=canonical_locator,
                metadata=hook_context.user_metadata,
                storage_mode=storage_mode,
                original_path=original_path,
                original_filename=original_filename,
                suffixes=suffixes,
                derived_metadata=_metadata_with_hook_warnings(hook_context),
                naming_metadata=naming_metadata,
                time_added=time_added,
            )
            self.hook_manager.before_record_write(hook_context)
            persisted = transaction.insert_staged_record(record)
            self.hook_manager.after_record_write(hook_context)
            if commit:
                self.hook_manager.before_commit(hook_context)
                transaction.commit()
                # After-commit hooks are best-effort: failures warn, but cannot
                # turn an already-persisted record into an apparent API failure.
                self.hook_manager.after_commit(hook_context)
            return persisted
        except Exception as exc:
            self.hook_manager.on_error(hook_context, exc)
            # Caller-supplied transactions stay caller-owned. Internal
            # transactions commit=True and roll back here before re-raising.
            if commit and transaction.state is not OperationState.COMMITTED:
                transaction.rollback(original_exception=exc)
                self.hook_manager.on_rollback(hook_context, exc)
            raise

    def _select_schema(self, record_type: str | None, *, require_known: bool) -> RecordSchema:
        """Select the schema that applies to a record."""
        if record_type is None:
            return self.spec.get_schema()
        if record_type in self.spec.record_schemas:
            return self.spec.get_schema(record_type)
        if require_known:
            raise ValueError(f"Unknown record schema: {record_type}")
        return self.spec.get_schema()

    def _validate_metadata(
        self,
        *,
        schema: RecordSchema,
        metadata: object,
        record_type: str | None,
    ) -> None:
        """Apply validation for the selected schema."""
        self._metadata_validation_report(
            schema=schema,
            metadata=metadata,
            record_type=record_type,
        ).raise_for_errors()

    def _metadata_validation_report(
        self,
        *,
        schema: RecordSchema,
        metadata: object,
        record_type: str | None,
    ) -> ValidationReport:
        """Return validation report for the selected schema."""
        schema_name = self._schema_name(record_type)
        return validate_metadata(metadata, schema, schema_name=schema_name)

    def _schema_name(self, record_type: str | None) -> str:
        """Return the validation schema name for a record type."""
        if record_type is not None and record_type in self.spec.record_schemas:
            return record_type
        return "default"

    def _has_metadata_fields(self) -> bool:
        """Return whether any default or named schema describes metadata fields."""
        if self.spec.get_schema().metadata_fields:
            return True
        return any(schema.metadata_fields for schema in self.spec.record_schemas.values())


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp string for record creation."""
    timestamp = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return timestamp.replace("+00:00", "Z")


def _open_repository(root: Path, spec: CatalogSpec) -> CatalogRepository:
    """Create the configured repository for a catalog spec."""
    if spec.db_backend != "tinydb":
        raise ValueError(f"Unsupported db_backend: {spec.db_backend}")
    return TinyDbCatalogRepository(root / spec.db_path)


def _coerce_hook_manager(
    *,
    plugins: PluginRegistry | None,
    hooks: HookManager | None,
) -> HookManager:
    """Resolve optional plugin or hook registration inputs."""
    if hooks is not None and plugins is not None:
        raise ValueError("Pass either plugins or hooks, not both.")
    if hooks is not None:
        return hooks
    if plugins is not None:
        return plugins.hook_manager()
    return HookManager()


def _metadata_with_hook_warnings(context: OperationContext) -> MetadataDict:
    """Return derived metadata with non-fatal hook warnings included."""
    metadata = dict(context.derived_metadata)
    if context.warnings:
        warnings_metadata: list[JsonValue] = [warning.to_metadata() for warning in context.warnings]
        metadata["hook_warnings"] = warnings_metadata
    return metadata


def _coerce_metadata_input(
    metadata: object,
    *,
    schema_name: str,
) -> MetadataDict:
    """Copy user metadata after preserving existing non-dictionary errors."""
    if isinstance(metadata, dict):
        return dict(metadata)
    raise TypeError(f"Metadata for schema {schema_name} must be a dictionary, got {type(metadata).__name__}")


def _artifact_locator_from_context(context: OperationContext) -> ArtifactLocator:
    """Return the canonical locator after locator-resolution hooks run."""
    if not context.planned_locators:
        raise ValueError("resolve_artifact_locator hook removed the planned artifact locator.")
    return context.planned_locators[0]


def _path_from_locator(locator: ArtifactLocator) -> Path:
    """Return a local path from a path-backed artifact locator."""
    path = locator.as_path()
    if path is None:
        raise ValueError("Managed file operations require a path-backed artifact locator.")
    return path


def _rollback_moved_file(*, source_path: Path, target_path: Path) -> None:
    """Restore a moved file when possible, otherwise remove the moved target."""
    if not target_path.exists():
        return
    if not source_path.exists():
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_path), str(source_path))
        return
    target_path.unlink(missing_ok=True)


def _require_template(value: str | None, *, field_name: str) -> str:
    """Return a schema template that should have been filled by the spec."""
    if value is None:
        raise ValueError(f"Default schema is missing {field_name}")
    return value


def _coerce_artifact_locator(value: object) -> ArtifactLocator:
    """Coerce a locator value for batch artifact creation."""
    if isinstance(value, ArtifactLocator):
        return value
    if isinstance(value, dict):
        return ArtifactLocator.from_dict(value)
    raise TypeError("artifact locator must be an ArtifactLocator or locator dictionary")


def _validate_artifact_batch_item(item: object, index: int) -> dict[str, object]:
    """Validate one batch artifact item and return it as a dictionary."""
    if not isinstance(item, dict):
        raise TypeError(f"artifact batch item {index} must be a dictionary")

    missing_keys = [key for key in ["record_type", "locator"] if key not in item]
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise ValueError(f"artifact batch item {index} is missing required key(s): {missing}")
    forbidden_id_keys = [key for key in ["record_id", "id"] if key in item]
    if forbidden_id_keys:
        forbidden = ", ".join(forbidden_id_keys)
        raise ValueError(f"artifact batch item {index} must not supply {forbidden}")

    return item


def _require_record_id(record: CatalogRecord) -> str:
    """Return a persisted record id."""
    if record.id is None:
        raise RuntimeError("Repository returned a persisted record without an id.")
    return record.id
