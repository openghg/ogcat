"""Catalog specification objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Literal

from ogcat.models import MetadataFieldDescription


@dataclass(slots=True)
class CatalogSpec:
    """Self-describing configuration for a catalog."""

    catalog_name: str
    db_backend: str = "tinydb"
    db_path: str = "db.json"
    files_root: str = "files"
    directory_template: str = "{year_added}/{original_stem}"
    filename_template: str = "{title_slug|original_stem}{original_suffix}"
    default_operation: Literal["copy", "move"] = "copy"
    field_resolution_order: list[str] = field(
        default_factory=lambda: ["top_level", "user_metadata", "derived_metadata"]
    )
    metadata_fields: list[MetadataFieldDescription] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert the spec to a serialisable dictionary."""
        return {
            "catalog_name": self.catalog_name,
            "db_backend": self.db_backend,
            "db_path": self.db_path,
            "files_root": self.files_root,
            "directory_template": self.directory_template,
            "filename_template": self.filename_template,
            "default_operation": self.default_operation,
            "field_resolution_order": list(self.field_resolution_order),
            "metadata_fields": [field_description.to_dict() for field_description in self.metadata_fields],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CatalogSpec":
        """Build a spec from a dictionary."""
        metadata_fields = [
            _coerce_metadata_field_description(item)
            for item in data.get("metadata_fields", [])
        ]
        return cls(
            catalog_name=str(data["catalog_name"]),
            db_backend=str(data.get("db_backend", "tinydb")),
            db_path=str(data.get("db_path", "db.json")),
            files_root=str(data.get("files_root", "files")),
            directory_template=str(data.get("directory_template", "{year_added}/{original_stem}")),
            filename_template=str(
                data.get("filename_template", "{title_slug|original_stem}{original_suffix}")
            ),
            default_operation=data.get("default_operation", "copy"),  # type: ignore[arg-type]
            field_resolution_order=[str(item) for item in data.get("field_resolution_order", [])]
            or ["top_level", "user_metadata", "derived_metadata"],
            metadata_fields=metadata_fields,
        )

    def write(self, path: Path) -> None:
        """Write the spec JSON to disk."""
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "CatalogSpec":
        """Read a spec JSON file from disk."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def _coerce_metadata_field_description(value: object) -> MetadataFieldDescription:
    """Coerce JSON-like metadata field descriptions into typed objects."""
    if isinstance(value, MetadataFieldDescription):
        return value
    if not isinstance(value, dict):
        raise TypeError("metadata_fields entries must be dictionaries")
    return MetadataFieldDescription.from_dict(value)  # type: ignore[arg-type]
