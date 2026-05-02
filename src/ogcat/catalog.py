"""Main catalog API."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast, overload
from uuid import uuid4

from ogcat.extractors import extract_derived_metadata
from ogcat.hooks import ArtifactWriter, HookManager, OperationContext, OperationSource
from ogcat.models import ArtifactLocator, CatalogRecord, JsonValue, MetadataDict
from ogcat.naming import build_naming_context, render_storage_location
from ogcat.plugins import PluginRegistry
from ogcat.record_set import CatalogRecordSet
from ogcat.repository import CatalogRepository
from ogcat.search import SearchQuery
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.storage import (
    LocalStorageAdapter,
    StoragePlan,
    TargetKind,
    WriteMode,
    plan_storage,
)
from ogcat.tinydb_repository import TinyDbCatalogRepository
from ogcat.transactions import OperationState, UnitOfWork
from ogcat.validation import ValidationReport, validate_metadata, validate_spec
from ogcat.writers import CopyArtifactWriter, MoveArtifactWriter

ArtifactLocatorFactory = Callable[[OperationContext], ArtifactLocator]
StoragePlanFactory = Callable[[OperationContext, ArtifactLocator], StoragePlan | None]
DerivedMetadataCollector = Callable[[OperationContext, ArtifactLocator], None]


@dataclass(slots=True)
class Catalog:
    """User-facing API bound to one catalog root.

    Args:
        root: Root directory containing ``catalog.json``, ``db.json``, and
            managed files.
        spec: Catalog specification loaded from or written to ``catalog.json``.
        repository: Record storage backend.
        hook_manager: Dispatcher for lifecycle hooks.
    """

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
        """Create a catalog directory and write its specification.

        Args:
            root: Directory to create or reuse for the catalog.
            spec: Catalog specification to persist.
            plugins: Optional plugin registry used to build a hook manager.
            hooks: Optional hook manager. Pass either ``plugins`` or ``hooks``.

        Returns:
            Open catalog instance bound to ``root``.

        Raises:
            ValueError: If the configured backend is unsupported, or both
                ``plugins`` and ``hooks`` are supplied.
        """
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
        """Open an existing catalog from disk.

        Args:
            root: Existing catalog root containing ``catalog.json``.
            plugins: Optional plugin registry used to build a hook manager.
            hooks: Optional hook manager. Pass either ``plugins`` or ``hooks``.

        Returns:
            Open catalog instance bound to ``root``.

        Raises:
            FileNotFoundError: If ``catalog.json`` is missing.
            ValueError: If the configured backend is unsupported, or both
                ``plugins`` and ``hooks`` are supplied.
        """
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
        """Add a local file using managed copy or move.

        Args:
            path: Source file to ingest.
            metadata: JSON-compatible user metadata.
            operation: ``"copy"`` or ``"move"``. Defaults to the catalog spec.
            record_type: Optional named schema to validate against.

        Returns:
            Persisted catalog record.

        Raises:
            TypeError: If metadata is not a dictionary.
            ValueError: If validation fails, the operation is unsupported, or
                ``record_type`` names an unknown schema.
        """
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
                operation_id=context.operation_id,
                original_path=source,
                metadata=context.user_metadata,
                date_added=timestamp[:10],
            )
            storage_adapter = LocalStorageAdapter()
            target, rel_path, _resolved_filename = render_storage_location(
                files_root=files_root,
                directory_template=directory_template,
                filename_template=filename_template,
                context=naming_context,
                exists=lambda candidate: storage_adapter.exists(ArtifactLocator.from_path(candidate)),
            )
            naming_metadata["resolved_filename"] = _resolved_filename
            return ArtifactLocator.from_path(target, relative_path=rel_path)

        def plan_local_file_storage(
            context: OperationContext,
            locator: ArtifactLocator,
        ) -> StoragePlan:
            """Build the storage plan for a managed local file."""
            return plan_storage(
                locator,
                target_kind="file",
                write_mode=cast(WriteMode, chosen_operation),
                ogcat_owned=True,
                adapter=_adapter_name(locator),
            )

        def collect_file_metadata(context: OperationContext, locator: ArtifactLocator) -> None:
            """Collect generic derived metadata from the written file."""
            locator_path = locator.as_path()
            if locator_path is not None:
                context.derived_metadata.update(extract_derived_metadata(locator_path))

        source_description = OperationSource(kind="local_file", path=source, descriptor=str(source))
        artifact_writer: ArtifactWriter = (
            CopyArtifactWriter() if chosen_operation == "copy" else MoveArtifactWriter()
        )
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
                source=source_description,
                locator_factory=resolve_local_file_locator,
                storage_plan_factory=plan_local_file_storage,
                artifact_writer=artifact_writer,
                derived_metadata_collector=collect_file_metadata,
            )

    def plan_artifact(
        self,
        path: str | Path | None = None,
        *,
        record_type: str | None = None,
        metadata: MetadataDict | None = None,
        locator: ArtifactLocator | None = None,
        target_kind: TargetKind = "file",
        write_mode: WriteMode | None = None,
        ogcat_owned: bool = True,
        storage_root: str | Path | None = None,
    ) -> StoragePlan:
        """Plan an artifact location without writing data or a catalog record.

        Args:
            path: Optional local source path used for naming and copy/move
                plans.
            record_type: Optional named schema to validate and use for naming.
            metadata: JSON-compatible user metadata.
            locator: Optional pre-resolved target locator. When omitted,
                schema naming templates are rendered under ``storage_root`` or
                this catalog's managed files root.
            target_kind: Whether the target is a file-like or directory-like
                artifact.
            write_mode: Desired materialisation mode. Defaults to ``"write"``
                for owned artifacts and ``"reference"`` otherwise.
            ogcat_owned: Whether ogcat should treat the target as managed.
            storage_root: Optional local root or fsspec URL root for rendered
                template targets.

        Returns:
            Planned storage decision.
        """
        metadata_input = {} if metadata is None else metadata
        schema = self._select_schema(record_type, require_known=record_type is not None)
        schema_name = self._schema_name(record_type)
        user_metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        resolved_record_type = "managed_artifact" if record_type is None else record_type
        resolved_write_mode = write_mode or ("write" if ogcat_owned else "reference")
        source_path = None if path is None else Path(path).expanduser().resolve()
        operation_id = uuid4().hex
        source = OperationSource(
            kind="planned_artifact" if source_path is None else "local_file",
            path=source_path,
            descriptor=None if source_path is None else str(source_path),
        )
        context = OperationContext(
            catalog_root=self.root,
            operation_id=operation_id,
            operation_type="plan_artifact",
            record_type=resolved_record_type,
            user_metadata=user_metadata,
            source=source,
            storage_mode=resolved_write_mode,
            original_path=source_path,
            original_filename=None if source_path is None else source_path.name,
            suffixes=[] if source_path is None else list(source_path.suffixes),
        )

        self.hook_manager.before_validate_metadata(context)
        validation_report = self._metadata_validation_report(
            schema=schema,
            metadata=context.user_metadata,
            record_type=record_type,
        )
        self.hook_manager.after_validate_metadata(context, validation_report)
        validation_report.raise_for_errors()

        planned_locator = locator or self._render_planned_locator(
            context=context,
            schema=schema,
            record_type=record_type,
            source_path=source_path,
            storage_root=storage_root,
        )
        context.planned_locators = [planned_locator]
        self.hook_manager.resolve_artifact_locator(context)
        canonical_locator = _artifact_locator_from_context(context)
        plan = plan_storage(
            canonical_locator,
            target_kind=target_kind,
            write_mode=resolved_write_mode,
            ogcat_owned=ogcat_owned,
            adapter=_adapter_name(canonical_locator),
        )
        context.storage_plan = plan
        return plan

    def add_artifact(
        self,
        *,
        record_type: str,
        locator: ArtifactLocator | None = None,
        storage_plan: StoragePlan | None = None,
        metadata: MetadataDict | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: MetadataDict | None = None,
        naming_metadata: MetadataDict | None = None,
        time_added: str | None = None,
        source: OperationSource | None = None,
        artifact_writer: ArtifactWriter | None = None,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Add an artifact record and optionally materialise planned storage.

        This is the minimal general record API. ``add_file()`` remains the
        managed ingest convenience wrapper that prepares a path-backed locator
        and delegates through the same lifecycle.

        Args:
            record_type: Logical type of record to create.
            locator: Artifact locator to store with the record. Required unless
                ``storage_plan`` is supplied.
            storage_plan: Optional planned storage decision to use instead of a
                standalone locator.
            metadata: JSON-compatible user metadata.
            storage_mode: Optional description such as ``"external"``.
            original_path: Optional source path or URI.
            original_filename: Optional source filename.
            suffixes: Optional suffix list for the source artifact.
            derived_metadata: Optional derived metadata to persist.
            naming_metadata: Optional naming metadata to persist.
            time_added: Optional timestamp override.
            source: Optional operation source for hooks and writers.
            artifact_writer: Optional writer that materialises data before the
                record is written.
            transaction: Optional caller-owned unit of work.

        Returns:
            Persisted or staged catalog record.

        Raises:
            TypeError: If metadata or writer inputs are invalid.
            ValueError: If validation fails or the transaction belongs to a
                different repository.
        """
        if locator is None and storage_plan is None:
            raise ValueError("add_artifact requires either locator or storage_plan.")
        if locator is not None and storage_plan is not None:
            raise ValueError("Pass either locator or storage_plan, not both.")
        if storage_plan is not None:
            locator = storage_plan.locator
            if storage_mode is None:
                storage_mode = storage_plan.write_mode
        assert locator is not None
        metadata_input = {} if metadata is None else metadata
        derived_metadata = {} if derived_metadata is None else dict(derived_metadata)
        schema = self._select_schema(record_type, require_known=False)
        schema_name = self._schema_name(record_type)
        metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        validated_source = _optional_operation_source(source)
        validated_artifact_writer = _validate_artifact_writer(artifact_writer)
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
                source=validated_source,
                artifact_writer=validated_artifact_writer,
                storage_plan=storage_plan,
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
                source=validated_source,
                artifact_writer=validated_artifact_writer,
                storage_plan=storage_plan,
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
        `add_artifact()`. Items are added one at a time so hooks and artifact
        writers run consistently for each record. Earlier items remain
        committed if a later item fails.

        Args:
            artifacts: List of dictionaries accepted by ``add_artifact()``.

        Returns:
            Persisted records in input order.
        """
        validated_items = [_validate_artifact_batch_item(item, index) for index, item in enumerate(artifacts)]

        records: list[CatalogRecord] = []
        for index, validated in enumerate(validated_items):
            storage_plan = _optional_storage_plan(validated.get("storage_plan"))
            locator: ArtifactLocator | None = None
            if storage_plan is None:
                try:
                    locator = _coerce_artifact_locator(validated["locator"])
                except TypeError as exc:
                    raise TypeError(f"artifact batch item {index}: invalid locator: {exc}") from exc
                except ValueError as exc:
                    raise ValueError(f"artifact batch item {index}: invalid locator: {exc}") from exc

            record_type = str(validated["record_type"])
            try:
                records.append(
                    self.add_artifact(
                        record_type=record_type,
                        locator=locator,
                        storage_plan=storage_plan,
                        metadata=validated.get("metadata"),  # type: ignore[arg-type]
                        storage_mode=(
                            None if validated.get("storage_mode") is None else str(validated["storage_mode"])
                        ),
                        original_path=validated.get("original_path"),  # type: ignore[arg-type]
                        original_filename=(
                            None
                            if validated.get("original_filename") is None
                            else str(validated["original_filename"])
                        ),
                        suffixes=_optional_string_list(validated.get("suffixes")),
                        derived_metadata=_optional_metadata(validated.get("derived_metadata")),
                        naming_metadata=_optional_metadata(validated.get("naming_metadata")),
                        time_added=(
                            None if validated.get("time_added") is None else str(validated["time_added"])
                        ),
                        source=_optional_operation_source(validated.get("source")),
                        artifact_writer=_optional_artifact_writer(validated.get("artifact_writer")),
                    )
                )
            except TypeError as exc:
                raise TypeError(f"artifact batch item {index}: {exc}") from exc
            except ValueError as exc:
                raise ValueError(f"artifact batch item {index}: {exc}") from exc
        return records

    @overload
    def search(
        self,
        query: SearchQuery | None = None,
        *,
        where: dict[str, object] | None = None,
        contains: dict[str, object] | None = None,
        regex: dict[str, str] | None = None,
        match: dict[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: Literal[False] = False,
    ) -> list[CatalogRecord]: ...

    @overload
    def search(
        self,
        *,
        query: SearchQuery | None = None,
        where: dict[str, object] | None = None,
        contains: dict[str, object] | None = None,
        regex: dict[str, str] | None = None,
        match: dict[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: Literal[True],
    ) -> CatalogRecordSet: ...

    def search(
        self,
        query: SearchQuery | None = None,
        *,
        where: dict[str, object] | None = None,
        contains: dict[str, object] | None = None,
        regex: dict[str, str] | None = None,
        match: dict[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: bool = False,
    ) -> list[CatalogRecord] | CatalogRecordSet:
        """Search catalog records using backend-neutral query semantics.

        Args:
            query: Optional pre-built search query.
            where: Equality filters.
            contains: Substring or list-membership filters.
            regex: Regular-expression filters.
            match: Glob or substring filters.
            exists: Fields that must be present.
            missing: Fields that must be absent.
            ignore_case: Whether string comparisons should be case-insensitive.
            as_record_set: Return a ``CatalogRecordSet`` instead of a list.

        Returns:
            Matching records, either as a list or record-set view.
        """
        results = self.repository.search(
            query=query,
            where=where,
            contains=contains,
            regex=regex,
            match=match,
            exists=exists,
            missing=missing,
            ignore_case=ignore_case,
            resolution_order=self.spec.field_resolution_order,
        )
        if as_record_set:
            return self.record_set(results)
        return results

    def record_set(self, records: Sequence[CatalogRecord]) -> CatalogRecordSet:
        """Wrap records in a sequence-like container.

        Args:
            records: Records to expose through ``CatalogRecordSet`` helpers.

        Returns:
            Record set using this catalog's field resolution order.
        """
        return CatalogRecordSet(records, resolution_order=self.spec.field_resolution_order)

    def describe(self) -> dict[str, object]:
        """Return a serialisable summary of catalog configuration and contents."""
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
        """Return serialisable metadata field descriptions for a schema."""
        schema = self._select_schema(record_type, require_known=record_type is not None)
        return [field_description.to_dict() for field_description in schema.metadata_fields]

    def list_record_fields(self) -> list[str]:
        """Return discoverable field paths present in stored records."""
        return self.record_set(self.repository.all()).field_paths()

    def unique_values(self, field: str) -> list[JsonValue]:
        """Return unique scalar values present for a field across stored records."""
        return self.record_set(self.repository.all()).unique_values(field)

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

    def add_record_schema(
        self,
        name: str,
        schema: RecordSchema | dict[str, object],
        *,
        overwrite: bool = False,
    ) -> None:
        """Add or replace a record schema in the catalog spec.

        Args:
            name: Record schema name.
            schema: Schema object or serialised schema dictionary.
            overwrite: Whether an existing schema may be replaced.

        Raises:
            ValueError: If the schema already exists and ``overwrite`` is false,
                or if the resulting spec is invalid.
            TypeError: If ``schema`` is not a valid schema object.
        """
        schema_name = str(name).strip()
        if not schema_name:
            raise ValueError("Record schema name cannot be empty.")
        if schema_name in self.spec.record_schemas and not overwrite:
            raise ValueError(f"Record schema already exists: {schema_name}")
        if isinstance(schema, RecordSchema):
            record_schema = schema
        elif isinstance(schema, dict):
            record_schema = RecordSchema.from_dict(schema)
        else:
            raise TypeError("schema must be a RecordSchema or dictionary.")

        record_schemas = dict(self.spec.record_schemas)
        record_schemas[schema_name] = record_schema
        self._replace_spec(record_schemas=record_schemas)

    def set_default_record_schema(self, name: str) -> None:
        """Set the default record schema by name."""
        schema_name = str(name).strip()
        if schema_name not in self.spec.record_schemas:
            raise ValueError(f"Unknown record schema: {schema_name}")
        self._replace_spec(default_record_schema=schema_name, default_schema=None)

    def update_spec(self, **fields: object) -> None:
        """Update simple catalog spec fields and persist ``catalog.json``.

        Supported fields are ``catalog_name``, ``default_operation``, and
        ``field_resolution_order``. ``files_root`` changes require a dedicated
        migration operation and are intentionally rejected here.
        """
        if "files_root" in fields:
            raise ValueError("Changing files_root requires a file-root migration operation.")
        allowed_fields = {"catalog_name", "default_operation", "field_resolution_order"}
        unknown_fields = sorted(field for field in fields if field not in allowed_fields)
        if unknown_fields:
            joined = ", ".join(unknown_fields)
            raise ValueError(f"Unsupported catalog spec field(s): {joined}")

        updates = dict(fields)
        if "default_operation" in updates and updates["default_operation"] not in {"copy", "move"}:
            raise ValueError("default_operation must be 'copy' or 'move'.")
        if "field_resolution_order" in updates:
            order = updates["field_resolution_order"]
            if not isinstance(order, list):
                raise TypeError("field_resolution_order must be a list.")
            field_resolution_order = [str(item) for item in order]
            supported_namespaces = {"top_level", "user_metadata", "derived_metadata"}
            invalid_namespaces = sorted(set(field_resolution_order) - supported_namespaces)
            if invalid_namespaces:
                joined = ", ".join(invalid_namespaces)
                raise ValueError(f"Unsupported field_resolution_order value(s): {joined}")
            updates["field_resolution_order"] = field_resolution_order

        self._replace_spec(**updates)

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

    def _render_planned_locator(
        self,
        *,
        context: OperationContext,
        schema: RecordSchema,
        record_type: str | None,
        source_path: Path | None,
        storage_root: str | Path | None,
    ) -> ArtifactLocator:
        """Render schema naming templates into a local or fsspec target locator."""
        directory_template = _require_template(schema.directory_template, field_name="directory_template")
        filename_template = _require_template(schema.filename_template, field_name="filename_template")
        naming_source = source_path or Path("artifact")
        naming_context = build_naming_context(
            record_id=context.operation_id,
            operation_id=context.operation_id,
            original_path=naming_source,
            metadata=context.user_metadata,
            date_added=_utc_timestamp()[:10],
        )

        if storage_root is not None and _is_urlpath_root(storage_root):
            root_url = str(storage_root).rstrip("/")
            fake_root = Path("/__ogcat_storage__")

            target, _rel_path, _resolved_filename = render_storage_location(
                files_root=fake_root,
                directory_template=directory_template,
                filename_template=filename_template,
                context=naming_context,
                exists=lambda _candidate: False,
            )
            relative_path = target.relative_to(fake_root).as_posix()
            return ArtifactLocator.from_urlpath(
                _join_urlpath(root_url, relative_path),
                relative_path=relative_path,
            )

        if storage_root is None:
            files_root = self.root / self.spec.files_root
        else:
            files_root = Path(storage_root).expanduser().resolve()
        storage_adapter = LocalStorageAdapter()
        target, rel_path, _resolved_filename = render_storage_location(
            files_root=files_root,
            directory_template=directory_template,
            filename_template=filename_template,
            context=naming_context,
            exists=lambda candidate: storage_adapter.exists(ArtifactLocator.from_path(candidate)),
        )
        relative_path = rel_path if storage_root is None else None
        return ArtifactLocator.from_path(target, relative_path=relative_path)

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
        source: OperationSource | None,
        artifact_writer: ArtifactWriter | None,
        storage_plan: StoragePlan | None,
        schema: RecordSchema,
    ) -> CatalogRecord:
        """Add an artifact record within an active transaction."""
        operation_source = source or OperationSource(
            kind="external",
            path=locator.as_path(),
            descriptor=locator.value,
        )
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
            source=operation_source,
            locator_factory=lambda context: locator,
            artifact_writer=artifact_writer,
            storage_plan_factory=(
                None
                if storage_plan is None
                else lambda context, canonical_locator: _storage_plan_with_locator(
                    storage_plan,
                    canonical_locator,
                )
            ),
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
        source: OperationSource,
        locator_factory: ArtifactLocatorFactory,
        storage_plan_factory: StoragePlanFactory | None = None,
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
            source=source,
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
            hook_context.storage_plan = (
                storage_plan_factory(hook_context, canonical_locator)
                if storage_plan_factory is not None
                else plan_storage(
                    canonical_locator,
                    write_mode="reference",
                    ogcat_owned=False,
                    adapter=_adapter_name(canonical_locator),
                )
            )
            storage_plan = hook_context.storage_plan
            if storage_plan is None:
                raise RuntimeError("Add operation did not produce a storage plan.")
            if artifact_writer is None:
                if storage_plan.write_mode != "reference":
                    raise ValueError(
                        f"Storage plan with write mode {storage_plan.write_mode!r} "
                        "requires an artifact_writer."
                    )
            else:
                artifact_writer.write(hook_context, hook_context.source, canonical_locator)
            if derived_metadata_collector is not None:
                derived_metadata_collector(hook_context, canonical_locator)
            self.hook_manager.extract_metadata(hook_context)
            self.hook_manager.before_record_write(hook_context)
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
        return self.spec.default_record_schema

    def _has_metadata_fields(self) -> bool:
        """Return whether any default or named schema describes metadata fields."""
        return any(schema.metadata_fields for schema in self.spec.record_schemas.values())

    def _replace_spec(self, **updates: object) -> None:
        """Replace the active spec after validation and persist it."""
        payload = {
            "catalog_name": self.spec.catalog_name,
            "db_backend": self.spec.db_backend,
            "db_path": self.spec.db_path,
            "files_root": self.spec.files_root,
            "default_operation": self.spec.default_operation,
            "field_resolution_order": list(self.spec.field_resolution_order),
            "default_record_schema": self.spec.default_record_schema,
            "default_schema": None,
            "record_schemas": dict(self.spec.record_schemas),
        }
        payload.update(updates)
        next_spec = CatalogSpec(**payload)  # type: ignore[arg-type]
        validation_report = validate_spec(next_spec)
        validation_report.raise_for_errors()
        next_spec.write(self.root / "catalog.json")
        self.spec = next_spec


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


def _storage_plan_with_locator(plan: StoragePlan, locator: ArtifactLocator) -> StoragePlan:
    """Return a storage plan adjusted to a hook-resolved canonical locator."""
    if plan.locator == locator:
        return plan
    return replace(plan, locator=locator, adapter=_adapter_name(locator))


def _adapter_name(locator: ArtifactLocator) -> str | None:
    """Return the storage adapter name implied by a locator."""
    if locator.kind == "path":
        return "local"
    if locator.kind == "urlpath":
        return "fsspec"
    return None


def _is_urlpath_root(value: str | Path) -> bool:
    """Return whether a storage root should be treated as an fsspec URL."""
    return isinstance(value, str) and "://" in value


def _join_urlpath(root_url: str, relative_path: str) -> str:
    """Join an fsspec URL root and relative path without local path coercion."""
    return f"{root_url.rstrip('/')}/{relative_path.lstrip('/')}"


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


def _optional_metadata(value: object) -> MetadataDict | None:
    """Return optional metadata for artifact batch forwarding."""
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"optional metadata value must be a dictionary, got {type(value).__name__}")


def _optional_string_list(value: object) -> list[str] | None:
    """Return an optional list of strings for artifact batch forwarding."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    raise TypeError(f"suffixes must be a list, got {type(value).__name__}")


def _optional_operation_source(value: object) -> OperationSource | None:
    """Return an optional operation source for artifact batch forwarding."""
    if value is None:
        return None
    if isinstance(value, OperationSource):
        return value
    raise TypeError(f"source must be an OperationSource, got {type(value).__name__}")


def _optional_artifact_writer(value: object) -> ArtifactWriter | None:
    """Return an optional artifact writer for artifact batch forwarding."""
    return _validate_artifact_writer(value)


def _optional_storage_plan(value: object) -> StoragePlan | None:
    """Return an optional storage plan for artifact batch forwarding."""
    if value is None:
        return None
    if isinstance(value, StoragePlan):
        return value
    raise TypeError(f"storage_plan must be a StoragePlan, got {type(value).__name__}")


def _validate_artifact_writer(value: object) -> ArtifactWriter | None:
    """Return an artifact writer when the optional value implements the writer protocol."""
    if value is None:
        return None
    if callable(getattr(value, "write", None)):
        return cast(ArtifactWriter, value)
    raise TypeError(f"artifact_writer must provide a callable write() method, got {type(value).__name__}")


def _validate_artifact_batch_item(item: object, index: int) -> dict[str, object]:
    """Validate one batch artifact item and return it as a dictionary."""
    if not isinstance(item, dict):
        raise TypeError(f"artifact batch item {index} must be a dictionary")

    missing_keys = [key for key in ["record_type"] if key not in item]
    if "locator" not in item and "storage_plan" not in item:
        missing_keys.append("locator or storage_plan")
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise ValueError(f"artifact batch item {index} is missing required key(s): {missing}")
    if "locator" in item and "storage_plan" in item:
        raise ValueError(f"artifact batch item {index} must not supply both locator and storage_plan")
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
