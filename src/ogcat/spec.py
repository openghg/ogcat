"""Catalog specification objects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ogcat.models import MetadataFieldDescription

DEFAULT_DIRECTORY_TEMPLATE = "{year_added}/{original_stem}"
DEFAULT_FILENAME_TEMPLATE = "{title_slug|original_stem}{original_suffix}"


@dataclass(slots=True)
class RecordSchema:
    """Lightweight metadata and naming schema for one record type."""

    description: str = ""
    directory_template: str | None = None
    filename_template: str | None = None
    metadata_fields: list[MetadataFieldDescription] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert the schema to a serialisable dictionary."""
        payload: dict[str, object] = {
            "metadata_fields": [
                field_description.to_dict() for field_description in self.metadata_fields
            ],
        }
        if self.description:
            payload["description"] = self.description
        if self.directory_template is not None:
            payload["directory_template"] = self.directory_template
        if self.filename_template is not None:
            payload["filename_template"] = self.filename_template
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RecordSchema:
        """Build a record schema from a dictionary."""
        metadata_fields = [
            _coerce_metadata_field_description(item)
            for item in _coerce_object_list(data.get("metadata_fields"), field_name="metadata_fields")
        ]
        return cls(
            description=str(data.get("description", "")),
            directory_template=(
                None if data.get("directory_template") is None else str(data["directory_template"])
            ),
            filename_template=(
                None if data.get("filename_template") is None else str(data["filename_template"])
            ),
            metadata_fields=metadata_fields,
        )

    def with_fallbacks(self, fallback: RecordSchema) -> RecordSchema:
        """Return this schema with missing naming templates filled from a fallback schema."""
        return RecordSchema(
            description=self.description,
            directory_template=self.directory_template or fallback.directory_template,
            filename_template=self.filename_template or fallback.filename_template,
            metadata_fields=list(self.metadata_fields),
        )

    def required_field_names(self) -> list[str]:
        """Return required metadata field names for this schema."""
        return [
            field_description.name
            for field_description in self.metadata_fields
            if field_description.required
        ]


@dataclass(slots=True)
class CatalogSpec:
    """Self-describing configuration for a catalog."""

    catalog_name: str
    db_backend: str = "tinydb"
    db_path: str = "db.json"
    files_root: str = "files"
    directory_template: str = DEFAULT_DIRECTORY_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    default_operation: Literal["copy", "move"] = "copy"
    field_resolution_order: list[str] = field(
        default_factory=lambda: ["top_level", "user_metadata", "derived_metadata"]
    )
    metadata_fields: list[MetadataFieldDescription] = field(default_factory=list)
    default_schema: RecordSchema | None = None
    record_schemas: dict[str, RecordSchema] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep legacy top-level defaults aligned with the explicit default schema."""
        self.record_schemas = dict(self.record_schemas)
        if self.default_schema is None:
            self.default_schema = RecordSchema(
                directory_template=self.directory_template,
                filename_template=self.filename_template,
                metadata_fields=list(self.metadata_fields),
            )
            return

        if self.default_schema.directory_template is None:
            self.default_schema.directory_template = self.directory_template
        else:
            self.directory_template = self.default_schema.directory_template

        if self.default_schema.filename_template is None:
            self.default_schema.filename_template = self.filename_template
        else:
            self.filename_template = self.default_schema.filename_template

        if self.default_schema.metadata_fields:
            self.metadata_fields = list(self.default_schema.metadata_fields)
        else:
            self.default_schema.metadata_fields = list(self.metadata_fields)

    def to_dict(self) -> dict[str, object]:
        """Convert the spec to a serialisable dictionary."""
        default_schema = self.default_schema or RecordSchema(
            directory_template=self.directory_template,
            filename_template=self.filename_template,
            metadata_fields=list(self.metadata_fields),
        )
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
            "default_schema": default_schema.to_dict(),
            "record_schemas": {
                record_type: schema.to_dict() for record_type, schema in self.record_schemas.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CatalogSpec:
        """Build a spec from a dictionary."""
        metadata_fields = [
            _coerce_metadata_field_description(item)
            for item in _coerce_object_list(data.get("metadata_fields"), field_name="metadata_fields")
        ]
        default_schema = _coerce_record_schema(data.get("default_schema"), field_name="default_schema")
        record_schemas = _coerce_record_schema_mapping(data.get("record_schemas"))

        directory_template = str(data.get("directory_template", DEFAULT_DIRECTORY_TEMPLATE))
        filename_template = str(data.get("filename_template", DEFAULT_FILENAME_TEMPLATE))
        if default_schema is not None:
            if "directory_template" not in data and default_schema.directory_template is not None:
                directory_template = default_schema.directory_template
            if "filename_template" not in data and default_schema.filename_template is not None:
                filename_template = default_schema.filename_template
            if "metadata_fields" not in data:
                metadata_fields = list(default_schema.metadata_fields)

        return cls(
            catalog_name=str(data["catalog_name"]),
            db_backend=str(data.get("db_backend", "tinydb")),
            db_path=str(data.get("db_path", "db.json")),
            files_root=str(data.get("files_root", "files")),
            directory_template=directory_template,
            filename_template=filename_template,
            default_operation=data.get("default_operation", "copy"),  # type: ignore[arg-type]
            field_resolution_order=[
                str(item)
                for item in _coerce_object_list(
                    data.get("field_resolution_order"),
                    field_name="field_resolution_order",
                )
            ]
            or ["top_level", "user_metadata", "derived_metadata"],
            metadata_fields=metadata_fields,
            default_schema=default_schema,
            record_schemas=record_schemas,
        )

    def get_schema(self, record_type: str | None = None) -> RecordSchema:
        """Return the effective schema for a record type.

        Args:
            record_type: Optional record type. When omitted, the broad default schema is returned.

        Raises:
            KeyError: If a non-default record type has no schema.
        """
        default_schema = self.default_schema or RecordSchema(
            directory_template=self.directory_template,
            filename_template=self.filename_template,
            metadata_fields=list(self.metadata_fields),
        )
        if record_type is None:
            return default_schema
        if record_type not in self.record_schemas:
            raise KeyError(f"Unknown record schema: {record_type}")
        return self.record_schemas[record_type].with_fallbacks(default_schema)

    def list_record_schemas(self) -> list[str]:
        """Return available named record schema names."""
        return sorted(self.record_schemas)

    def write(self, path: Path) -> None:
        """Write the spec JSON to disk."""
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> CatalogSpec:
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


def _coerce_record_schema(value: object, *, field_name: str) -> RecordSchema | None:
    """Coerce a JSON-like schema object into a typed record schema."""
    if value is None:
        return None
    if isinstance(value, RecordSchema):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a dictionary")
    return RecordSchema.from_dict(value)  # type: ignore[arg-type]


def _coerce_record_schema_mapping(value: object) -> dict[str, RecordSchema]:
    """Coerce a JSON-like mapping of record type to schema."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("record_schemas must be a dictionary")

    schemas: dict[str, RecordSchema] = {}
    for record_type, raw_schema in value.items():
        schema = _coerce_record_schema(raw_schema, field_name=f"record_schemas.{record_type}")
        if schema is None:
            raise TypeError(f"record_schemas.{record_type} must be a dictionary")
        schemas[str(record_type)] = schema
    return schemas


def _coerce_object_list(value: object, *, field_name: str) -> list[object]:
    """Coerce a JSON-like list value to a plain object list."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    return value
