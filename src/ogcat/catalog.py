"""Main catalog API."""

from __future__ import annotations

import getpass
import os
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, overload
from uuid import uuid4

from ogcat.audit import (
    AuditEvent,
    AuditSink,
    JsonlAuditSink,
)
from ogcat.catalog_application import CatalogApplication
from ogcat.classification import CLASSIFICATION_METADATA_KEY, collection_classification_metadata
from ogcat.hooks import (
    ArtifactWriter,
    HookLifecycleEvent,
    HookManager,
    OperationContext,
    OperationSource,
    coerce_hook_iterable,
    validate_hook_objects,
)
from ogcat.models import ArtifactLocator, CatalogRecord, JsonValue, MetadataDict, normalize_metadata
from ogcat.operation_helpers import (
    artifact_locator_from_context,
    naming_metadata_from_storage_plan,
    normalize_metadata_for_schema,
)
from ogcat.operation_runner import (
    AddOperationRequest,
    AddOperationRunner,
    OperationRunner,
    OperationServices,
)
from ogcat.plugins import PluginRegistry
from ogcat.record_set import CatalogRecordSet
from ogcat.reference_planning import plan_reference_locator
from ogcat.replicas import (
    ReplicaMode,
    ReplicaViewPlan,
    plan_replica_view,
)
from ogcat.repository import CatalogRepository
from ogcat.search import SearchQuery
from ogcat.spec import CatalogSpec, RecordSchema
from ogcat.storage import (
    StoragePlan,
    TargetKind,
    WriteMode,
)
from ogcat.storage_planning import (
    PrimaryLocation,
    PrimaryStoragePlanningContext,
    plan_primary_storage,
)
from ogcat.tinydb_repository import TinyDbCatalogRepository
from ogcat.transactions import UnitOfWork
from ogcat.validation import ValidationReport, validate_metadata, validate_spec

PluginInput = PluginRegistry | Iterable[object] | None
HookInput = HookManager | Iterable[object] | None
MetadataUpdateMode = Literal["replace", "shallow_merge"]


@dataclass(slots=True)
class Catalog:
    """User-facing API bound to one catalog root.

    Args:
        root: Root directory containing ``catalog.json``, ``db.json``, and
            managed files.
        spec: Catalog specification loaded from or written to ``catalog.json``.
        repository: Record storage backend.
        hook_manager: Long-lived hook registry.
        audit_sink: Sink for structured operation audit events.
        audit_user_id: User id recorded on audit events.
    """

    root: Path
    spec: CatalogSpec
    repository: CatalogRepository
    hook_manager: HookManager = field(default_factory=HookManager)
    audit_sink: AuditSink | None = None
    audit_user_id: str | None = None

    @classmethod
    def create(
        cls,
        root: str | Path,
        spec: CatalogSpec,
        *,
        plugins: PluginInput = None,
        hooks: HookInput = None,
        audit_sink: AuditSink | None = None,
        audit_user_id: str | None = None,
    ) -> Catalog:
        """Create a catalog directory and write its specification.

        Args:
            root: Directory to create or reuse for the catalog.
            spec: Catalog specification to persist.
            plugins: Optional plugin registry, or iterable of hook objects,
                used to build a hook manager.
            hooks: Optional hook manager, or iterable of hook objects. Pass
                either ``plugins`` or ``hooks``.
            audit_sink: Optional audit sink. Defaults to a catalog-local JSONL
                sink under ``.ogcat/logs/events.jsonl``.
            audit_user_id: Optional user id to record on audit events.

        Returns:
            Open catalog instance bound to ``root``.

        Raises:
            ValueError: If the configured backend is unsupported, or both
                ``plugins`` and ``hooks`` are supplied.
        """
        hook_manager = _coerce_hook_manager(plugins=plugins, hooks=hooks)
        root_path = Path(root).expanduser().resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        spec.write(root_path / "catalog.json")
        (root_path / spec.files_root).mkdir(parents=True, exist_ok=True)
        (root_path / spec.objects_root).mkdir(parents=True, exist_ok=True)
        repository = _open_repository(root_path, spec)
        return cls(
            root=root_path,
            spec=spec,
            repository=repository,
            hook_manager=hook_manager,
            audit_sink=_coerce_audit_sink(root_path, audit_sink),
            audit_user_id=_resolve_audit_user_id(audit_user_id),
        )

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        plugins: PluginInput = None,
        hooks: HookInput = None,
        audit_sink: AuditSink | None = None,
        audit_user_id: str | None = None,
    ) -> Catalog:
        """Open an existing catalog from disk.

        Args:
            root: Existing catalog root containing ``catalog.json``.
            plugins: Optional plugin registry, or iterable of hook objects,
                used to build a hook manager.
            hooks: Optional hook manager, or iterable of hook objects. Pass
                either ``plugins`` or ``hooks``.
            audit_sink: Optional audit sink. Defaults to a catalog-local JSONL
                sink under ``.ogcat/logs/events.jsonl``.
            audit_user_id: Optional user id to record on audit events.

        Returns:
            Open catalog instance bound to ``root``.

        Raises:
            FileNotFoundError: If ``catalog.json`` is missing.
            ValueError: If the configured backend is unsupported, or both
                ``plugins`` and ``hooks`` are supplied.
        """
        hook_manager = _coerce_hook_manager(plugins=plugins, hooks=hooks)
        root_path = Path(root).expanduser().resolve()
        spec = CatalogSpec.read(root_path / "catalog.json")
        repository = _open_repository(root_path, spec)
        return cls(
            root=root_path,
            spec=spec,
            repository=repository,
            hook_manager=hook_manager,
            audit_sink=_coerce_audit_sink(root_path, audit_sink),
            audit_user_id=_resolve_audit_user_id(audit_user_id),
        )

    def add_file(
        self,
        path: str | Path,
        metadata: Mapping[Any, Any] | None = None,
        operation: str | None = None,
        record_type: str | None = None,
        primary_location: PrimaryLocation = "uuid",
    ) -> CatalogRecord:
        """Add a local file using managed copy or move.

        Args:
            path: Source file to ingest.
            metadata: JSON-compatible user metadata.
            operation: ``"copy"`` or ``"move"``. Defaults to the catalog spec.
            record_type: Optional named schema to validate against.
            primary_location: ``"uuid"`` stores the primary artifact under a
                UUID path and creates a template symlink replica. ``"template"``
                stores the primary artifact at the rendered template path.

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
        resolved_primary_location = _coerce_primary_location(primary_location)

        timestamp = _utc_timestamp()
        return self._application().add_file(
            source=source,
            metadata=metadata,
            schema=schema,
            schema_record_type=record_type,
            record_type=resolved_record_type,
            directory_template=directory_template,
            filename_template=filename_template,
            operation=chosen_operation,
            primary_location=resolved_primary_location,
            time_added=timestamp,
        )

    def plan_artifact_storage(
        self,
        path: str | Path | None = None,
        *,
        record_type: str | None = None,
        metadata: Mapping[Any, Any] | None = None,
        locator: ArtifactLocator | None = None,
        target_kind: TargetKind = "file",
        write_mode: WriteMode | None = None,
        ogcat_owned: bool = True,
        storage_root: str | Path | None = None,
        primary_location: PrimaryLocation = "uuid",
    ) -> StoragePlan:
        """Plan artifact storage without writing data or a catalog record.

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
            primary_location: ``"uuid"`` plans a UUID primary path.
                ``"template"`` plans the rendered schema template as the
                primary path. Ignored when ``locator`` is supplied.

        Returns:
            Planned storage decision.
        """
        metadata_input = {} if metadata is None else metadata
        schema = self._select_schema(record_type, require_known=record_type is not None)
        schema_name = self._schema_name(record_type)
        user_metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        resolved_record_type = "managed_artifact" if record_type is None else record_type
        resolved_write_mode = write_mode or ("write" if ogcat_owned else "reference")
        resolved_primary_location = _coerce_primary_location(primary_location)
        source_path = None if path is None else Path(path).expanduser().resolve()
        timestamp = _utc_timestamp()
        operation_id = uuid4().hex
        source = OperationSource(
            kind="planned_artifact" if source_path is None else "local_file",
            path=source_path,
            descriptor=None if source_path is None else str(source_path),
        )
        context = OperationContext(
            catalog_root=self.root,
            operation_id=operation_id,
            operation_type="plan_artifact_storage",
            record_type=resolved_record_type,
            user_metadata=user_metadata,
            source=source,
            storage_mode=resolved_write_mode,
            original_path=source_path,
            original_filename=None if source_path is None else source_path.name,
            suffixes=[] if source_path is None else list(source_path.suffixes),
        )

        hook_dispatcher = self.hook_manager.dispatcher()
        hook_dispatcher.before_validate_metadata(context)
        context.user_metadata = normalize_metadata_for_schema(
            context.user_metadata,
            schema_name=schema_name,
        )
        validation_report = self._metadata_validation_report(
            schema=schema,
            metadata=context.user_metadata,
            record_type=record_type,
        )
        hook_dispatcher.after_validate_metadata(context, validation_report)
        validation_report.raise_for_errors()

        directory_template = (
            _require_template(schema.directory_template, field_name="directory_template")
            if locator is None
            else schema.directory_template or ""
        )
        filename_template = (
            _require_template(schema.filename_template, field_name="filename_template")
            if locator is None
            else schema.filename_template or ""
        )
        primary = plan_primary_storage(
            PrimaryStoragePlanningContext(
                catalog_root=self.root,
                files_root=self.root / self.spec.files_root,
                objects_root=self.root / self.spec.objects_root,
                operation_id=context.operation_id,
                metadata=context.user_metadata,
                directory_template=directory_template,
                filename_template=filename_template,
                source_path=source_path,
                storage_root=storage_root,
                date_added=timestamp[:10],
                primary_location="user_provided" if locator is not None else resolved_primary_location,
                locator=locator,
            )
        )
        planned_locator = primary.locator
        context.planned_locators = [planned_locator]
        hook_dispatcher.resolve_artifact_locator(context)
        canonical_locator = artifact_locator_from_context(context)
        plan = primary.to_storage_plan(
            locator=canonical_locator,
            target_kind=target_kind,
            write_mode=resolved_write_mode,
            ogcat_owned=ogcat_owned,
            time_added=timestamp,
        )
        context.storage_plan = plan
        return plan

    def add_artifact(
        self,
        *,
        record_type: str,
        locator: ArtifactLocator | None = None,
        storage_plan: StoragePlan | None = None,
        metadata: Mapping[Any, Any] | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: Mapping[Any, Any] | None = None,
        naming_metadata: Mapping[Any, Any] | None = None,
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
            if time_added is None:
                time_added = storage_plan.time_added
            if naming_metadata is None:
                naming_metadata = naming_metadata_from_storage_plan(storage_plan)
        assert locator is not None
        metadata_input = {} if metadata is None else metadata
        derived_metadata = (
            {}
            if derived_metadata is None
            else normalize_metadata(derived_metadata, field_name="derived_metadata")
        )
        naming_metadata = (
            None
            if naming_metadata is None
            else normalize_metadata(naming_metadata, field_name="naming_metadata")
        )
        schema = self._select_schema(record_type, require_known=False)
        schema_name = self._schema_name(record_type)
        metadata = _coerce_metadata_input(metadata_input, schema_name=schema_name)
        validated_source = _optional_operation_source(source)
        validated_artifact_writer = _validate_artifact_writer(artifact_writer)
        application = self._application()
        if transaction is not None:
            if transaction.repository is not self.repository:
                raise ValueError("Transaction is bound to a different catalog repository.")
            return application.add_artifact(
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
            return application.add_artifact(
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

    def add_reference(
        self,
        reference: str | Path | ArtifactLocator | None = None,
        *,
        uri: str | None = None,
        urlpath: str | None = None,
        record_type: str = "external_reference",
        metadata: Mapping[Any, Any] | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: Mapping[Any, Any] | None = None,
        naming_metadata: Mapping[Any, Any] | None = None,
        time_added: str | None = None,
        source: OperationSource | None = None,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Record an existing path or locator without materialising storage.

        ``add_reference()`` is a convenience wrapper around ``add_artifact()``
        for artifacts that already exist. It records a reference only: no file
        is copied, moved, created, or required to live under the catalog's
        managed files root.

        Args:
            reference: Local filesystem path, URI-like string, or explicit
                artifact locator.
            uri: Optional explicit URI reference. Pass exactly one of
                ``reference``, ``uri``, or ``urlpath``.
            urlpath: Optional explicit fsspec-style URL-path reference. Pass
                exactly one of ``reference``, ``uri``, or ``urlpath``.
            record_type: Logical type of record to create.
            metadata: JSON-compatible user metadata.
            original_path: Optional source path or URI override. Inferred for
                local path references when omitted.
            original_filename: Optional source filename override. Inferred for
                local path references when omitted.
            suffixes: Optional source suffix list override. Inferred for local
                path references when omitted.
            derived_metadata: Optional derived metadata to persist.
            naming_metadata: Optional naming metadata to persist.
            time_added: Optional timestamp override.
            source: Optional operation source for hooks.
            transaction: Optional caller-owned unit of work.

        Returns:
            Persisted or staged reference record.
        """
        reference_plan = plan_reference_locator(reference, uri=uri, urlpath=urlpath)
        locator = reference_plan.locator
        local_path = reference_plan.local_path
        resolved_original_path = original_path
        resolved_original_filename = original_filename
        resolved_suffixes = suffixes
        if local_path is not None:
            if resolved_original_path is None:
                resolved_original_path = local_path
            if resolved_original_filename is None:
                resolved_original_filename = local_path.name
            if resolved_suffixes is None:
                resolved_suffixes = local_path.suffixes

        return self.add_artifact(
            record_type=record_type,
            locator=locator,
            metadata=metadata,
            storage_mode="reference",
            original_path=resolved_original_path,
            original_filename=resolved_original_filename,
            suffixes=resolved_suffixes,
            derived_metadata=derived_metadata,
            naming_metadata=naming_metadata,
            time_added=time_added,
            source=source,
            transaction=transaction,
        )

    def add_collection(
        self,
        collection: str | Path | ArtifactLocator | None = None,
        *,
        uri: str | None = None,
        urlpath: str | None = None,
        record_type: str = "collection",
        metadata: Mapping[Any, Any] | None = None,
        collection_pattern: str = "*",
        member_format: str | None = None,
        member_suffixes: Sequence[str] | None = None,
        reader_hint: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: Mapping[Any, Any] | None = None,
        naming_metadata: Mapping[Any, Any] | None = None,
        time_added: str | None = None,
        source: OperationSource | None = None,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Record a directory-backed logical collection as one artifact.

        Collection semantics are explicit: a plain directory reference remains
        a directory unless callers opt into this method.

        Args:
            collection: Local directory, URI-like string, or explicit artifact
                locator for the collection root.
            uri: Optional explicit URI collection root. Pass exactly one of
                ``collection``, ``uri``, or ``urlpath``.
            urlpath: Optional fsspec-style URL-path collection root. Pass
                exactly one of ``collection``, ``uri``, or ``urlpath``.
            record_type: Logical type of record to create.
            metadata: JSON-compatible user metadata.
            collection_pattern: Relative pattern describing intended members,
                for example ``"*.nc"``.
            member_format: Optional format label for collection members.
            member_suffixes: Optional suffixes expected for collection members.
            reader_hint: Optional human-readable downstream reader hint.
            original_path: Optional source path or URI override. Inferred for
                local collection roots when omitted.
            original_filename: Optional source filename override. Inferred for
                local collection roots when omitted.
            suffixes: Optional source suffix list override. Inferred for local
                collection roots when omitted.
            derived_metadata: Optional derived metadata to persist alongside
                collection classification.
            naming_metadata: Optional naming metadata to persist.
            time_added: Optional timestamp override.
            source: Optional operation source for hooks.
            transaction: Optional caller-owned unit of work.

        Returns:
            Persisted or staged collection record.

        Raises:
            ValueError: If a local collection root is not an existing directory
                or if collection metadata is invalid.
        """
        reference_plan = plan_reference_locator(collection, uri=uri, urlpath=urlpath)
        locator = reference_plan.locator
        local_path = reference_plan.local_path
        if local_path is not None:
            _require_collection_directory(local_path)

        resolved_original_path = original_path
        resolved_original_filename = original_filename
        resolved_suffixes = suffixes
        if local_path is not None:
            if resolved_original_path is None:
                resolved_original_path = local_path
            if resolved_original_filename is None:
                resolved_original_filename = local_path.name
            if resolved_suffixes is None:
                resolved_suffixes = local_path.suffixes

        collection_classification = collection_classification_metadata(
            collection_pattern=collection_pattern,
            member_format=member_format,
            member_suffixes=member_suffixes,
            reader_hint=reader_hint,
        )
        resolved_derived_metadata = _with_collection_classification(
            derived_metadata,
            collection_classification=collection_classification,
        )
        return self.add_artifact(
            record_type=record_type,
            locator=locator,
            metadata=metadata,
            storage_mode="reference",
            original_path=resolved_original_path,
            original_filename=resolved_original_filename,
            suffixes=resolved_suffixes,
            derived_metadata=resolved_derived_metadata,
            naming_metadata=naming_metadata,
            time_added=time_added,
            source=source,
            transaction=transaction,
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

    def audit_events(
        self,
        *,
        user_id: str | None = None,
        operation_id: str | None = None,
        record_id: str | None = None,
        level: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[AuditEvent]:
        """Return catalog-local audit events matching optional filters.

        Args:
            user_id: Optional user id filter.
            operation_id: Optional operation id filter.
            record_id: Optional record id filter.
            level: Optional event severity filter.
            event_type: Optional lifecycle event type filter.
            limit: Optional number of most recent matching events to return.

        Returns:
            Matching audit events in log order.
        """
        sink = self.audit_sink
        if isinstance(sink, JsonlAuditSink):
            return sink.read_events(
                user_id=user_id,
                operation_id=operation_id,
                record_id=record_id,
                level=level,
                event_type=event_type,
                limit=limit,
            )
        return JsonlAuditSink(self.root).read_events(
            user_id=user_id,
            operation_id=operation_id,
            record_id=record_id,
            level=level,
            event_type=event_type,
            limit=limit,
        )

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

    def plan_view(
        self,
        root: str | Path,
        template: str,
        *,
        mode: ReplicaMode = "symlink",
        query: SearchQuery | None = None,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
    ) -> ReplicaViewPlan:
        """Plan a generated replica view without mutating records or files.

        Args:
            root: Root directory for the generated view.
            template: Combined path template relative to ``root``.
            mode: Replica materialisation mode. Only ``"symlink"`` is
                supported.
            query: Optional pre-built search query.
            where: Equality filters.
            contains: Substring or list-membership filters.
            regex: Regular-expression filters.
            match: Glob or substring filters.
            exists: Fields that must be present.
            missing: Fields that must be absent.
            ignore_case: Whether string comparisons should be case-insensitive.

        Returns:
            Dry-run replica view plan.
        """
        records = self.search(
            query=query,
            where=where,
            contains=contains,
            regex=regex,
            match=match,
            exists=exists,
            missing=missing,
            ignore_case=ignore_case,
            as_record_set=False,
        )
        return plan_replica_view(root=root, template=template, records=records, mode=mode)

    @overload
    def search(
        self,
        query: SearchQuery | None = None,
        *,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: Literal[True] = True,
    ) -> CatalogRecordSet: ...

    @overload
    def search(
        self,
        query: SearchQuery | None = None,
        *,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: Literal[False],
    ) -> list[CatalogRecord]: ...

    def search(
        self,
        query: SearchQuery | None = None,
        *,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        as_record_set: bool = True,
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
            as_record_set: Return a ``CatalogRecordSet``. Pass ``False`` for a list.

        Returns:
            Matching records, as a record-set view by default or a list when requested.
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

    def get_one(
        self,
        query: SearchQuery | None = None,
        *,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        allow_many: bool = False,
    ) -> CatalogRecord:
        """Return one matching record, raising clear errors for ambiguous searches."""
        records = self.search(
            query=query,
            where=where,
            contains=contains,
            regex=regex,
            match=match,
            exists=exists,
            missing=missing,
            ignore_case=ignore_case,
        )
        if not records:
            raise ValueError("get_one found no records matching the search filters.")
        if len(records) > 1 and not allow_many:
            raise ValueError(
                f"get_one found multiple records ({len(records)}) matching the search filters. "
                "Refine the filters or pass allow_many=True."
            )
        return records[0]

    def record_set(self, records: Sequence[CatalogRecord]) -> CatalogRecordSet:
        """Wrap records in a sequence-like container.

        Args:
            records: Records to expose through ``CatalogRecordSet`` helpers.

        Returns:
            Record set using this catalog's field resolution order.
        """
        schema_display_fields = {
            record_type: self.spec.get_schema(record_type).display_fields
            for record_type in self.spec.list_record_schemas()
        }
        return CatalogRecordSet(
            records,
            resolution_order=self.spec.field_resolution_order,
            schema_display_fields=schema_display_fields,
            default_display_fields=self.spec.get_schema().display_fields,
        )

    def describe(self) -> dict[str, object]:
        """Return a serialisable summary of catalog configuration and contents."""
        db_path = self.root / self.spec.db_path
        files_root = self.root / self.spec.files_root
        objects_root = self.root / self.spec.objects_root
        default_schema = self.spec.get_schema()
        return {
            "catalog_name": self.spec.catalog_name,
            "root_path": str(self.root),
            "backend": self.spec.db_backend,
            "database_path": str(db_path),
            "files_root": str(files_root),
            "objects_root": str(objects_root),
            "default_operation": self.spec.default_operation,
            "directory_template": default_schema.directory_template,
            "filename_template": default_schema.filename_template,
            "field_resolution_order": list(self.spec.field_resolution_order),
            "record_count": len(self.repository.all()),
            "has_metadata_fields": self._has_metadata_fields(),
            "record_schemas": self.list_record_schemas(),
        }

    def list_metadata_fields(self, record_type: str | None = None) -> list[dict[str, JsonValue]]:
        """Return serialisable schema-declared metadata field descriptions."""
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

    def get(self, record_id: object) -> CatalogRecord | None:
        """Get a record by id after coercing public input with ``str()``."""
        return self.repository.get(_coerce_record_id(record_id))

    def path(self, record_id: object) -> Path | None:
        """Return the stored path for a path-backed record, if present."""
        record = self.get(record_id)
        if record is None:
            return None
        return record.path()

    def update_metadata(
        self,
        record_id: object,
        metadata: Mapping[Any, Any],
        mode: MetadataUpdateMode = "replace",
        *,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Update a record's user metadata through validation and storage.

        Args:
            record_id: Existing record id.
            metadata: Replacement metadata or top-level metadata updates.
            mode: ``"replace"`` replaces the whole user metadata dictionary.
                ``"shallow_merge"`` applies a top-level dictionary update;
                nested dictionaries are replaced as values, not recursively
                merged.
            transaction: Optional caller-owned transaction. When supplied, the
                previous record version is restored if the transaction rolls
                back.

        Returns:
            Updated catalog record.

        Raises:
            TypeError: If metadata is not a dictionary.
            ValueError: If the update mode is unsupported, or schema metadata
                validation fails.
            KeyError: If the record id does not exist.
        """
        return self._update_metadata_namespace(
            record_id=record_id,
            metadata=metadata,
            mode=mode,
            namespace="user_metadata",
            transaction=transaction,
        )

    def update_derived_metadata(
        self,
        record_id: object,
        derived_metadata: Mapping[Any, Any],
        mode: MetadataUpdateMode = "replace",
        *,
        transaction: UnitOfWork | None = None,
    ) -> CatalogRecord:
        """Update a record's derived metadata through normalization and storage.

        Args:
            record_id: Existing record id.
            derived_metadata: Replacement derived metadata or top-level updates.
            mode: ``"replace"`` replaces the whole derived metadata dictionary.
                ``"shallow_merge"`` applies a top-level dictionary update;
                nested dictionaries are replaced as values, not recursively
                merged.
            transaction: Optional caller-owned transaction. When supplied, the
                previous record version is restored if the transaction rolls
                back.

        Returns:
            Updated catalog record.

        Raises:
            TypeError: If derived metadata is not a dictionary.
            ValueError: If the update mode is unsupported.
            KeyError: If the record id does not exist.
        """
        return self._update_metadata_namespace(
            record_id=record_id,
            metadata=derived_metadata,
            mode=mode,
            namespace="derived_metadata",
            transaction=transaction,
        )

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
        ``field_resolution_order``. Storage root changes require a dedicated
        migration operation and are intentionally rejected here.
        """
        forbidden_storage_roots = {"files_root", "objects_root"}.intersection(fields)
        if forbidden_storage_roots:
            joined = ", ".join(sorted(forbidden_storage_roots))
            raise ValueError(f"Changing {joined} requires a storage-root migration operation.")
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
        derived_metadata: Mapping[Any, Any] | None = None,
        naming_metadata: Mapping[Any, Any] | None = None,
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
        user_metadata = {} if metadata is None else normalize_metadata(metadata, field_name="metadata")
        normalized_derived_metadata = (
            {}
            if derived_metadata is None
            else normalize_metadata(derived_metadata, field_name="derived_metadata")
        )
        normalized_naming_metadata = (
            {}
            if naming_metadata is None
            else normalize_metadata(naming_metadata, field_name="naming_metadata")
        )

        return CatalogRecord(
            id=record_id,
            catalog=self.spec.catalog_name,
            time_added=resolved_time_added,
            record_type=record_type,
            locator=locator,
            stored_abspath=str(locator_path) if locator_path is not None else None,
            stored_relpath=locator.relative_path if locator.kind == "path" else None,
            storage_mode=storage_mode,
            original_path=resolved_original_path,
            original_filename=original_filename,
            suffixes=[] if suffixes is None else list(suffixes),
            user_metadata=user_metadata,
            derived_metadata=normalized_derived_metadata,
            naming_metadata=normalized_naming_metadata,
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
        source: OperationSource | None,
        artifact_writer: ArtifactWriter | None,
        storage_plan: StoragePlan | None,
        schema: RecordSchema,
    ) -> CatalogRecord:
        """Add an artifact record within an active transaction."""
        return self._application().add_artifact(
            transaction=transaction,
            commit=commit,
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
            source=source,
            artifact_writer=artifact_writer,
            storage_plan=storage_plan,
            schema=schema,
        )

    def _emit_audit(self, event: AuditEvent) -> None:
        """Emit an audit event without failing the catalog operation."""
        if self.audit_sink is None:
            return
        try:
            self.audit_sink.emit(event)
        except Exception as exc:
            warnings.warn(
                f"audit logging failed: {type(exc).__name__}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    def _emit_operation_audit(
        self,
        context: OperationContext,
        *,
        event_type: str,
        level: str = "info",
        message: str,
        details: Mapping[str, object] | None = None,
        exception: BaseException | None = None,
        locator: ArtifactLocator | None = None,
    ) -> None:
        """Emit one audit event for an operation context."""
        raw_event_details = _operation_audit_details(context)
        if details is not None:
            raw_event_details.update(details)
        event_details = normalize_metadata(raw_event_details, field_name="audit.details")
        locator_details = _audit_locator(locator or _first_planned_locator(context))
        if exception is None:
            event = AuditEvent(
                operation_id=context.operation_id,
                level=level,
                event_type=event_type,
                user_id=self.audit_user_id,
                catalog_id=self.spec.catalog_name,
                catalog_path=str(self.root),
                record_id=context.record_id,
                locator=locator_details,
                message=message,
                details=event_details,
            )
        else:
            event = AuditEvent.from_exception(
                operation_id=context.operation_id,
                event_type=event_type,
                user_id=self.audit_user_id,
                catalog_id=self.spec.catalog_name,
                catalog_path=str(self.root),
                record_id=context.record_id,
                locator=locator_details,
                message=message,
                details=event_details,
                exception=exception,
            )
        self._emit_audit(event)

    def _emit_hook_lifecycle_audit(self, event: HookLifecycleEvent) -> None:
        """Convert a hook lifecycle event into a structured audit event."""
        details: dict[str, object] = {
            "phase": event.phase.name,
            "hook_method": event.phase.name,
            "hook_count": event.hook_count,
            "hook_phase": event.stage,
        }
        if event.stage == "completed":
            details["warnings_added"] = event.warnings_added

        if event.stage == "started":
            self._emit_operation_audit(
                event.context,
                event_type="hook",
                message=f"{event.phase.label} hooks started.",
                details=details,
            )
            return
        if event.stage == "failed":
            self._emit_operation_audit(
                event.context,
                event_type="hook",
                level="error",
                message=f"{event.phase.label} hooks failed.",
                details=details,
                exception=event.error,
            )
            return

        self._emit_operation_audit(
            event.context,
            event_type="hook",
            level="warning" if event.warnings_added else "info",
            message=(
                f"{event.phase.label} hooks completed with warnings."
                if event.warnings_added
                else f"{event.phase.label} hooks completed."
            ),
            details=details,
        )

    def _application(self) -> CatalogApplication:
        """Build the internal application service for catalog operations."""
        return CatalogApplication(self)

    def _build_add_operation_runner(self, request: AddOperationRequest) -> OperationRunner:
        """Build the runner used for one internal add operation."""
        return AddOperationRunner(dependencies=self._operation_runner_dependencies(), request=request)

    def _operation_runner_dependencies(self) -> OperationServices:
        """Build catalog-owned dependencies for an internal operation runner."""
        return OperationServices(
            catalog_root=self.root,
            hook_manager=self.hook_manager,
            schema_name=self._schema_name,
            metadata_validation_report=self._metadata_validation_report,
            build_artifact_record=self._build_artifact_record,
            emit_operation_audit=self._emit_operation_audit,
            emit_hook_lifecycle_audit=self._emit_hook_lifecycle_audit,
        )

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

    def _update_metadata_namespace(
        self,
        *,
        record_id: object,
        metadata: Mapping[Any, Any],
        mode: MetadataUpdateMode,
        namespace: Literal["user_metadata", "derived_metadata"],
        transaction: UnitOfWork | None,
    ) -> CatalogRecord:
        """Update one metadata namespace using explicit replace or shallow merge."""
        update_mode = _coerce_metadata_update_mode(mode)
        record = self._require_record(record_id)
        if namespace == "user_metadata":
            schema_name = self._schema_name(record.record_type)
            normalized_input = _coerce_metadata_input(metadata, schema_name=schema_name)
            updated_metadata = _merge_metadata(
                current=record.user_metadata,
                updates=normalized_input,
                mode=update_mode,
            )
            updated_metadata = normalize_metadata_for_schema(
                updated_metadata,
                schema_name=schema_name,
            )
            schema = self._select_schema(record.record_type, require_known=False)
            self._validate_metadata(
                schema=schema,
                metadata=updated_metadata,
                record_type=record.record_type,
            )
            updated_record = replace(record, user_metadata=updated_metadata)
        else:
            normalized_input = normalize_metadata(metadata, field_name="derived_metadata")
            updated_metadata = _merge_metadata(
                current=record.derived_metadata,
                updates=normalized_input,
                mode=update_mode,
            )
            updated_record = replace(
                record,
                derived_metadata=normalize_metadata(
                    updated_metadata,
                    field_name="derived_metadata",
                ),
            )
        return self._stage_or_commit_record_update(updated_record, transaction=transaction)

    def _require_record(self, record_id: object) -> CatalogRecord:
        """Return an existing record or raise a clear missing-record error."""
        resolved_record_id = _coerce_record_id(record_id)
        record = self.repository.get(resolved_record_id)
        if record is None:
            raise KeyError(f"Record not found: {resolved_record_id}")
        return record

    def _stage_or_commit_record_update(
        self,
        record: CatalogRecord,
        *,
        transaction: UnitOfWork | None,
    ) -> CatalogRecord:
        """Apply a record replacement through a caller-owned or internal transaction."""
        if transaction is not None:
            if transaction.repository is not self.repository:
                raise ValueError("Transaction is bound to a different catalog repository.")
            return transaction.update_staged_record(record)
        with self.transaction() as unit_of_work:
            updated = unit_of_work.update_staged_record(record)
            unit_of_work.commit()
            return updated

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
            "objects_root": self.spec.objects_root,
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


def _coerce_primary_location(value: object) -> PrimaryLocation:
    """Return a supported primary placement policy."""
    if value in {"uuid", "template"}:
        return cast(PrimaryLocation, value)
    raise ValueError("primary_location must be 'uuid' or 'template'.")


def _open_repository(root: Path, spec: CatalogSpec) -> CatalogRepository:
    """Create the configured repository for a catalog spec."""
    if spec.db_backend != "tinydb":
        raise ValueError(f"Unsupported db_backend: {spec.db_backend}")
    return TinyDbCatalogRepository(root / spec.db_path)


def _coerce_audit_sink(root: Path, audit_sink: AuditSink | None) -> AuditSink:
    """Return an audit sink for a catalog root."""
    if audit_sink is not None:
        return audit_sink
    return JsonlAuditSink(root)


def _resolve_audit_user_id(audit_user_id: str | None) -> str | None:
    """Resolve the audit user id from explicit input, environment, or OS user."""
    if audit_user_id is not None:
        return str(audit_user_id)
    for env_name in ("OGCAT_USER_ID", "OGCAT_USER"):
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
    try:
        return getpass.getuser()
    except OSError:
        return None


def _coerce_hook_manager(
    *,
    plugins: PluginInput,
    hooks: HookInput,
) -> HookManager:
    """Resolve optional plugin or hook registration inputs."""
    if hooks is not None and plugins is not None:
        raise ValueError("Pass either plugins or hooks, not both.")
    if hooks is not None:
        if isinstance(hooks, HookManager):
            validate_hook_objects(hooks.hooks, label="hooks")
            return hooks
        return HookManager(coerce_hook_iterable(hooks, label="hooks"))
    if plugins is not None:
        if isinstance(plugins, PluginRegistry):
            validate_hook_objects(plugins.hooks, label="plugins")
            return plugins.hook_manager()
        registry = PluginRegistry(coerce_hook_iterable(plugins, label="plugins"))
        return registry.hook_manager()
    return HookManager()


def _coerce_record_id(record_id: object) -> str:
    """Return a repository id string from public record-id input."""
    if record_id is None:
        raise TypeError("record_id must not be None")
    return str(record_id)


def _coerce_metadata_update_mode(mode: object) -> MetadataUpdateMode:
    """Return a supported metadata update mode."""
    if mode in {"replace", "shallow_merge"}:
        return cast(MetadataUpdateMode, mode)
    raise ValueError("metadata update mode must be 'replace' or 'shallow_merge'.")


def _merge_metadata(
    *,
    current: MetadataDict,
    updates: MetadataDict,
    mode: MetadataUpdateMode,
) -> MetadataDict:
    """Return metadata after an explicit replace or top-level-only merge."""
    if mode == "replace":
        return dict(updates)
    return {**current, **updates}


def _coerce_metadata_input(
    metadata: object,
    *,
    schema_name: str,
) -> MetadataDict:
    """Copy user metadata after preserving existing non-dictionary errors."""
    return normalize_metadata_for_schema(metadata, schema_name=schema_name)


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
    return normalize_metadata(value, field_name="metadata", label="optional metadata value")


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


def _require_collection_directory(path: Path) -> None:
    """Raise when a local collection root is not an accessible directory."""
    try:
        is_directory = path.is_dir()
    except OSError as exc:
        raise ValueError(f"Collection path is not an accessible directory: {path}") from exc
    if not is_directory:
        raise ValueError(f"Collection path must be an existing directory: {path}")


def _with_collection_classification(
    derived_metadata: Mapping[Any, Any] | None,
    *,
    collection_classification: MetadataDict,
) -> MetadataDict:
    """Return derived metadata with collection classification enforced."""
    resolved = (
        {}
        if derived_metadata is None
        else normalize_metadata(derived_metadata, field_name="derived_metadata")
    )
    existing_classification = resolved.get(CLASSIFICATION_METADATA_KEY)
    if isinstance(existing_classification, Mapping):
        merged_classification = {
            **existing_classification,
            **collection_classification,
        }
    else:
        merged_classification = dict(collection_classification)
    resolved[CLASSIFICATION_METADATA_KEY] = normalize_metadata(
        merged_classification,
        field_name=f"derived_metadata.{CLASSIFICATION_METADATA_KEY}",
    )
    return resolved


def _operation_audit_details(context: OperationContext) -> dict[str, object]:
    """Return sanitized operation context details for audit events."""
    source = context.source
    return {
        "operation_type": context.operation_type,
        "record_type": context.record_type,
        "storage_mode": context.storage_mode,
        "record_id": context.record_id,
        "metadata_keys": sorted(context.user_metadata),
        "derived_metadata_keys": sorted(context.derived_metadata),
        "source": {
            "kind": source.kind,
            "path": None if source.path is None else str(source.path),
            "descriptor": source.descriptor,
            "metadata_keys": sorted(source.metadata),
            "payload_present": source.payload is not None,
        },
        "original_path": None if context.original_path is None else str(context.original_path),
        "original_filename": context.original_filename,
        "suffixes": list(context.suffixes),
        "hook_warning_count": len(context.warnings),
    }


def _audit_locator(locator: ArtifactLocator | None) -> MetadataDict | None:
    """Return a JSON-compatible locator summary for audit events."""
    if locator is None:
        return None
    return normalize_metadata(locator.to_dict(), field_name="locator")


def _first_planned_locator(context: OperationContext) -> ArtifactLocator | None:
    """Return the first planned locator when one exists."""
    if not context.planned_locators:
        return None
    return context.planned_locators[0]
