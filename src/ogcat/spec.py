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
    """Lightweight metadata and naming schema for one record type.

    Args:
        description: Human-readable schema description.
        directory_template: Optional storage directory template.
        filename_template: Optional storage filename template.
        metadata_fields: Described metadata fields.
        allow_unknown_metadata: Whether fields outside ``metadata_fields`` are
            allowed during strict validation.
    """

    description: str = ""
    directory_template: str | None = None
    filename_template: str | None = None
    metadata_fields: list[MetadataFieldDescription] = field(default_factory=list)
    allow_unknown_metadata: bool = True

    def to_dict(self) -> dict[str, object]:
        """Convert the schema to a serialisable dictionary."""
        payload: dict[str, object] = {
            "metadata_fields": [field_description.to_dict() for field_description in self.metadata_fields],
        }
        if self.description:
            payload["description"] = self.description
        if self.directory_template is not None:
            payload["directory_template"] = self.directory_template
        if self.filename_template is not None:
            payload["filename_template"] = self.filename_template
        if not self.allow_unknown_metadata:
            payload["allow_unknown_metadata"] = False
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RecordSchema:
        """Build a record schema from a dictionary."""
        metadata_fields = [
            _coerce_metadata_field_description(item)
            for item in _coerce_object_list(data.get("metadata_fields"), field_name="metadata_fields")
        ]
        return cls(
            description=_coerce_optional_string(data.get("description"), default=""),
            directory_template=(
                None if data.get("directory_template") is None else str(data["directory_template"])
            ),
            filename_template=(
                None if data.get("filename_template") is None else str(data["filename_template"])
            ),
            metadata_fields=metadata_fields,
            allow_unknown_metadata=bool(data.get("allow_unknown_metadata", True)),
        )

    def with_fallbacks(self, fallback: RecordSchema) -> RecordSchema:
        """Return this schema with missing naming templates filled from a fallback schema."""
        return RecordSchema(
            description=self.description,
            directory_template=(
                fallback.directory_template if self.directory_template is None else self.directory_template
            ),
            filename_template=(
                fallback.filename_template if self.filename_template is None else self.filename_template
            ),
            metadata_fields=list(self.metadata_fields),
            allow_unknown_metadata=self.allow_unknown_metadata,
        )

    def required_field_names(self) -> list[str]:
        """Return required metadata field names for this schema."""
        return [
            field_description.name for field_description in self.metadata_fields if field_description.required
        ]


@dataclass(slots=True)
class CatalogSpec:
    """Self-describing configuration for a catalog.

    Args:
        catalog_name: Human-readable catalog name.
        db_backend: Repository backend identifier. Only ``"tinydb"`` is
            supported today.
        db_path: Database path relative to the catalog root.
        files_root: Managed file root relative to the catalog root.
        default_operation: Default managed-file operation.
        field_resolution_order: Namespace order for flattened search fields.
        default_record_schema: Name of the fallback schema in ``record_schemas``.
        default_schema: Optional constructor convenience for the fallback schema.
        record_schemas: Named schemas for record types.
    """

    catalog_name: str
    db_backend: str = "tinydb"
    db_path: str = "db.json"
    files_root: str = "files"
    default_operation: Literal["copy", "move"] = "copy"
    field_resolution_order: list[str] = field(
        default_factory=lambda: ["top_level", "user_metadata", "derived_metadata"]
    )
    default_record_schema: str = "default"
    default_schema: RecordSchema | dict[str, object] | None = None
    record_schemas: dict[str, RecordSchema] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fill required defaults on the configured fallback schema."""
        self.default_record_schema = str(self.default_record_schema).strip()
        if not self.default_record_schema:
            raise ValueError("default_record_schema cannot be empty.")
        record_schemas = _coerce_record_schema_mapping(self.record_schemas)
        default_schema = _coerce_record_schema(self.default_schema, field_name="default_schema")
        if default_schema is not None:
            if self.default_record_schema in record_schemas:
                raise ValueError(
                    "Pass either default_schema or record_schemas[default_record_schema], not both."
                )
            record_schemas[self.default_record_schema] = default_schema
        elif self.default_record_schema not in record_schemas:
            record_schemas[self.default_record_schema] = _default_record_schema()

        record_schemas[self.default_record_schema] = record_schemas[
            self.default_record_schema
        ].with_fallbacks(_default_record_schema())
        self.record_schemas = {
            self.default_record_schema: record_schemas[self.default_record_schema],
            **{
                schema_name: schema
                for schema_name, schema in record_schemas.items()
                if schema_name != self.default_record_schema
            },
        }
        self.default_schema = self.record_schemas[self.default_record_schema]

    def to_dict(self) -> dict[str, object]:
        """Convert the spec to a serialisable dictionary."""
        return {
            "catalog_name": self.catalog_name,
            "db_backend": self.db_backend,
            "db_path": self.db_path,
            "files_root": self.files_root,
            "default_operation": self.default_operation,
            "field_resolution_order": list(self.field_resolution_order),
            "default_record_schema": self.default_record_schema,
            "record_schemas": {
                record_type: schema.to_dict() for record_type, schema in self.record_schemas.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CatalogSpec:
        """Build a spec from a dictionary."""
        record_schemas = _coerce_record_schema_mapping(data.get("record_schemas"))

        return cls(
            catalog_name=str(data["catalog_name"]),
            db_backend=str(data.get("db_backend", "tinydb")),
            db_path=str(data.get("db_path", "db.json")),
            files_root=str(data.get("files_root", "files")),
            default_operation=data.get("default_operation", "copy"),  # type: ignore[arg-type]
            field_resolution_order=[
                str(item)
                for item in _coerce_object_list(
                    data.get("field_resolution_order"),
                    field_name="field_resolution_order",
                )
            ]
            or ["top_level", "user_metadata", "derived_metadata"],
            default_record_schema=str(data.get("default_record_schema", "default")),
            record_schemas=record_schemas,
        )

    def get_schema(self, record_type: str | None = None) -> RecordSchema:
        """Return the effective schema for a record type.

        Args:
            record_type: Optional record type. When omitted, the broad default schema is returned.

        Raises:
            ValueError: If a non-default record type has no schema.
        """
        if record_type is None:
            return self.record_schemas[self.default_record_schema]
        if record_type not in self.record_schemas:
            raise ValueError(f"Unknown record schema: {record_type}")
        return self.record_schemas[record_type].with_fallbacks(
            self.record_schemas[self.default_record_schema]
        )

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


def _default_record_schema() -> RecordSchema:
    """Return the broad default schema used for generic ingest."""
    return RecordSchema(
        description="Generic fallback schema for heterogeneous files.",
        directory_template=DEFAULT_DIRECTORY_TEMPLATE,
        filename_template=DEFAULT_FILENAME_TEMPLATE,
    )


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
        schema_name = str(record_type)
        if schema_name in schemas:
            raise ValueError(
                f"record_schemas contains duplicate schema name after string coercion: {schema_name}"
            )
        schema = _coerce_record_schema(
            raw_schema,
            field_name=f"record_schemas[{record_type!r}]",
        )
        if schema is None:
            raise TypeError(f"record_schemas[{record_type!r}] must be a dictionary")
        schemas[schema_name] = schema
    return schemas


def _coerce_optional_string(value: object, *, default: str) -> str:
    """Coerce optional JSON string-like values without turning null into 'None'."""
    if value is None:
        return default
    return str(value)


def _coerce_object_list(value: object, *, field_name: str) -> list[object]:
    """Coerce a JSON-like list value to a plain object list."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    return value
