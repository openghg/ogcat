"""Main catalog API."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ogcat.extractors import extract_derived_metadata
from ogcat.models import ArtifactLocator, CatalogRecord, MetadataDict
from ogcat.naming import build_naming_context, render_storage_location
from ogcat.repository import CatalogRepository
from ogcat.search import matches_record
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

    def _next_record_id(self) -> str:
        """Generate the next simple sequential record id."""
        prefix = "rec"
        existing_ids = [record.id for record in self.repository.all()]
        max_number = 0
        for record_id in existing_ids:
            if record_id.startswith(f"{prefix}_"):
                try:
                    max_number = max(max_number, int(record_id.split("_", 1)[1]))
                except ValueError:
                    continue
        return f"{prefix}_{max_number + 1:06d}"

    def add_file(
        self,
        path: str | Path,
        metadata: MetadataDict | None = None,
        operation: str | None = None,
    ) -> CatalogRecord:
        """Add a file to the catalog using managed copy or move."""
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Source file does not exist: {source}")

        metadata = metadata or {}
        chosen_operation = operation or self.spec.default_operation
        if chosen_operation not in {"copy", "move"}:
            raise ValueError(f"Unsupported operation: {chosen_operation}")

        record_id = self._next_record_id()
        timestamp = _utc_timestamp()
        date_added = timestamp[:10]

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

        if chosen_operation == "copy":
            shutil.copy2(source, target)
            storage_mode = "copy"
        else:
            shutil.move(str(source), str(target))
            storage_mode = "move"

        # NOTE: derived metadata is not used for location and filename creation
        derived_metadata = extract_derived_metadata(target)
        locator = ArtifactLocator.path(target, relative_path=rel_path)

        return self.add_artifact(
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
            record_id=record_id,
            time_added=timestamp,
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
        record_id: str | None = None,
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
            record_id=record_id,
            time_added=time_added,
        )
        self.repository.insert(record)
        return record

    def add_artifacts(
        self,
        artifacts: list[dict[str, object]],
    ) -> list[CatalogRecord]:
        """Add multiple artifact records.

        Each item should provide the same keyword-style fields accepted by
        `add_artifact()`. This keeps the public artifact API small while allowing
        batch-oriented callers to avoid one-at-a-time repository writes.
        """
        records = [
            self._build_artifact_record(
                record_type=str(item["record_type"]),
                locator=_coerce_artifact_locator(item["locator"]),
                metadata=item.get("metadata"),  # type: ignore[arg-type]
                storage_mode=(
                    None if item.get("storage_mode") is None else str(item["storage_mode"])
                ),
                original_path=item.get("original_path"),  # type: ignore[arg-type]
                original_filename=(
                    None
                    if item.get("original_filename") is None
                    else str(item["original_filename"])
                ),
                suffixes=item.get("suffixes"),  # type: ignore[arg-type]
                derived_metadata=item.get("derived_metadata"),  # type: ignore[arg-type]
                naming_metadata=item.get("naming_metadata"),  # type: ignore[arg-type]
                record_id=None if item.get("record_id") is None else str(item["record_id"]),
                time_added=None if item.get("time_added") is None else str(item["time_added"]),
            )
            for item in artifacts
        ]
        self.repository.insert_many(records)
        return records

    def search(
        self,
        *,
        where: dict[str, object] | None = None,
        contains: dict[str, str] | None = None,
        regex: dict[str, str] | None = None,
        ignore_case: bool = False,
    ) -> list[CatalogRecord]:
        """Search catalog records using equality, substring, and regex filters."""
        records = self.repository.all()
        return [
            record
            for record in records
            if matches_record(
                record,
                where=where,
                contains=contains,
                regex=regex,
                ignore_case=ignore_case,
                resolution_order=self.spec.field_resolution_order,
            )
        ]

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

    def list_metadata_fields(self) -> list[dict[str, object]]:
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
        record_type: str,
        locator: ArtifactLocator,
        metadata: MetadataDict | None = None,
        storage_mode: str | None = None,
        original_path: str | Path | None = None,
        original_filename: str | None = None,
        suffixes: list[str] | None = None,
        derived_metadata: MetadataDict | None = None,
        naming_metadata: MetadataDict | None = None,
        record_id: str | None = None,
        time_added: str | None = None,
    ) -> CatalogRecord:
        """Build an artifact record without persisting it."""
        resolved_record_id = record_id or self._next_record_id()
        resolved_time_added = time_added or _utc_timestamp()
        if original_path is None:
            resolved_original_path = None
        elif isinstance(original_path, Path):
            resolved_original_path = str(original_path)
        else:
            resolved_original_path = original_path

        return CatalogRecord(
            id=resolved_record_id,
            catalog=self.spec.catalog_name,
            time_added=resolved_time_added,
            record_type=record_type,
            locator=locator,
            stored_abspath=str(locator.as_path()) if locator.as_path() is not None else None,
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
