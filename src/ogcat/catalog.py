"""Main catalog API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil

from ogcat.models import CatalogRecord, MetadataDict
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
    def create(cls, root: str | Path, spec: CatalogSpec) -> "Catalog":
        """Create a new catalog directory and write its specification."""
        root_path = Path(root).expanduser().resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        spec.write(root_path / "catalog.json")
        (root_path / spec.files_root).mkdir(parents=True, exist_ok=True)
        repository = _open_repository(root_path, spec)
        return cls(root=root_path, spec=spec, repository=repository)

    @classmethod
    def open(cls, root: str | Path) -> "Catalog":
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
        timestamp = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
        timestamp = timestamp.replace("+00:00", "Z")
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

        record = CatalogRecord(
            id=record_id,
            catalog=self.spec.catalog_name,
            stored_abspath=str(target),
            stored_relpath=rel_path,
            storage_mode=storage_mode,
            time_added=timestamp,
            original_path=str(source),
            original_filename=source.name,
            suffixes=source.suffixes,
            user_metadata=metadata,
            derived_metadata={},
            naming_metadata={
                "directory_template": self.spec.directory_template,
                "filename_template": self.spec.filename_template,
                "resolved_filename": resolved_filename,
            },
        )
        self.repository.insert(record)
        return record

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
        """Return the stored path for a record, if present."""
        record = self.get(record_id)
        if record is None:
            return None
        return Path(record.stored_abspath)


def _open_repository(root: Path, spec: CatalogSpec) -> CatalogRepository:
    """Create the configured repository for a catalog spec."""
    if spec.db_backend != "tinydb":
        raise ValueError(f"Unsupported db_backend: {spec.db_backend}")
    return TinyDbCatalogRepository(root / spec.db_path)
