"""Core data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MetadataDict: TypeAlias = dict[str, JsonValue]


@dataclass(slots=True)
class MetadataFieldDescription:
    """Lightweight description of an important metadata field."""

    name: str
    description: str
    example: JsonValue | None = None
    required: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the field description to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "MetadataFieldDescription":
        """Build a field description from a plain dictionary."""
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            example=data.get("example"),
            required=bool(data.get("required", False)),
        )


@dataclass(slots=True)
class CatalogRecord:
    """A single catalogued file record."""

    id: str
    catalog: str
    stored_abspath: str
    stored_relpath: str
    storage_mode: str
    time_added: str
    original_path: str | None = None
    original_filename: str | None = None
    suffixes: list[str] = field(default_factory=list)
    user_metadata: MetadataDict = field(default_factory=dict)
    derived_metadata: MetadataDict = field(default_factory=dict)
    naming_metadata: MetadataDict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the record to a plain dictionary."""
        return {
            "id": self.id,
            "catalog": self.catalog,
            "stored_abspath": self.stored_abspath,
            "stored_relpath": self.stored_relpath,
            "storage_mode": self.storage_mode,
            "time_added": self.time_added,
            "original_path": self.original_path,
            "original_filename": self.original_filename,
            "suffixes": self.suffixes,
            "user_metadata": self.user_metadata,
            "derived_metadata": self.derived_metadata,
            "naming_metadata": self.naming_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "CatalogRecord":
        """Build a record from a plain dictionary."""
        return cls(
            id=str(data["id"]),
            catalog=str(data["catalog"]),
            stored_abspath=str(data["stored_abspath"]),
            stored_relpath=str(data["stored_relpath"]),
            storage_mode=str(data["storage_mode"]),
            time_added=str(data["time_added"]),
            original_path=(None if data.get("original_path") is None else str(data["original_path"])),
            original_filename=(
                None if data.get("original_filename") is None else str(data["original_filename"])
            ),
            suffixes=[str(x) for x in data.get("suffixes", [])],
            user_metadata=dict(data.get("user_metadata", {})),
            derived_metadata=dict(data.get("derived_metadata", {})),
            naming_metadata=dict(data.get("naming_metadata", {})),
        )
