"""Main catalog API."""

from __future__ import annotations

import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ogcat.extractors import extract_derived_metadata
from ogcat.models import ArtifactLocator, CatalogRecord, JsonValue, MetadataDict
from ogcat.naming import build_naming_context, render_storage_location
from ogcat.repository import CatalogRepository
from ogcat.spec import CatalogSpec
from ogcat.tinydb_repository import TinyDbCatalogRepository


@dataclass(slots=True)
class Catalog:
    """User-facing catalog API bound to a catalog root."""

    root: Path
    spec: CatalogSpec
    repository: CatalogRepository

    @classmethod
    def create(cls, root: str | Path, spec: CatalogSpec) -> Catalog:
        """Create a new catalog directory and write its specification."""
        root_path = Path(root).expanduser().resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        spec.write(root_path / "catalog.json")
        (root_path / spec.files_root).mkdir(parents=True, exist_ok=True)
        repository = _open_repository(root_path, spec)
        return cls(root=root_path, spec=spec, repository=repository)

    @classmethod
    def open(cls, root: str | Path) -> Catalog:
        """Open an existing catalog from disk."""
        root_path = Path(root).expanduser().resolve()
        spec = CatalogSpec.read(root_path / "catalog.json")
        repository = _open_repository(root_path, spec)
        return cls(root=root_path, spec=spec, repository=repository)

    def add_file(
        self,
        path: str | Path,
        metadata: MetadataDict | None = None,
        operation: str | None = None,
    ) -> CatalogRecord:
        """Add a file to the catalog using managed copy or move."""
        source = Path(path).expanduser().resolve()
        metadata = metadata or {}
        chosen_operation = operation or self.spec.default_operation
        if chosen_operation not in {"copy", "move"}:
            raise ValueError(f"Unsupported operation: {chosen_operation}")

        timestamp = _utc_timestamp()
        date_added = timestamp[:10]
        draft_record = self._build_artifact_record(
            record_type="managed_file",
            locator=ArtifactLocator(kind="opaque", value=""),
            metadata=metadata,
            storage_mode=chosen_operation,
            original_path=source,
            original_filename=source.name,
            suffixes=source.suffixes,
            time_added=timestamp,
        )
        persisted_record = self.repository.insert(draft_record)
        record_id = _require_record_id(persisted_record)

        context = build_naming_context(
            record_id=record_id,
            original_path=source,
            metadata=metadata,
            date_added=date_added,
        )
        files_root = self.root / self.spec.files_root
        target, rel_path, resolved_filename = render_storage_location(
            files_root=files_root,
            directory_template=self.spec.directory_template,
            filename_template=self.spec.filename_template,
            context=context,
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            if chosen_operation == "copy":
                shutil.copy2(source, target)
                storage_mode = "copy"
            else:
                shutil.move(str(source), str(target))
                storage_mode = "move"

            # NOTE: derived metadata is not used for location and filename creation
            derived_metadata = extract_derived_metadata(target)
            locator = ArtifactLocator.path(target, relative_path=rel_path)
            record = self._build_artifact_record(
                record_id=record_id,
                record_type="managed_file",
                locator=locator,
                metadata=metadata,
                storage_mode=storage_mode,
                original_path=source,
                original_filename=source.name,
                suffixes=source.suffixes,
                derived_metadata=derived_metadata,
                naming_metadata={
                    "directory_template": self.spec.directory_template,
                    "filename_template": self.spec.filename_template,
                    "resolved_filename": resolved_filename,
                },
                time_added=timestamp,
            )
            self.repository.update(record)
            return record
        except Exception:
            with suppress(Exception):
                self.repository.delete(record_id)
            raise

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
    ) -> CatalogRecord:
        """Add an artifact record without performing any file operation.

        This is the minimal general record API. `add_file()` remains the managed
        ingest convenience wrapper that prepares a path-backed locator and then
        delegates here.
        """
        record = self._build_artifact_record(
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
        )
        return self.repository.insert(record)

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

            records.append(
                self._build_artifact_record(
                    record_type=str(validated["record_type"]),
                    locator=locator,
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
        return {
            "catalog_name": self.spec.catalog_name,
            "root_path": str(self.root),
            "backend": self.spec.db_backend,
            "database_path": str(db_path),
            "files_root": str(files_root),
            "default_operation": self.spec.default_operation,
            "directory_template": self.spec.directory_template,
            "filename_template": self.spec.filename_template,
            "field_resolution_order": list(self.spec.field_resolution_order),
            "record_count": len(self.repository.all()),
            "has_metadata_fields": bool(self.spec.metadata_fields),
        }

    def list_metadata_fields(self) -> list[dict[str, JsonValue]]:
        """Return serialisable metadata field descriptions."""
        return [field_description.to_dict() for field_description in self.spec.metadata_fields]

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


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp string for record creation."""
    timestamp = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return timestamp.replace("+00:00", "Z")


def _open_repository(root: Path, spec: CatalogSpec) -> CatalogRepository:
    """Create the configured repository for a catalog spec."""
    if spec.db_backend != "tinydb":
        raise ValueError(f"Unsupported db_backend: {spec.db_backend}")
    return TinyDbCatalogRepository(root / spec.db_path)


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
