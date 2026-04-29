"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias, cast

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MetadataDict: TypeAlias = dict[str, JsonValue]


@dataclass(slots=True)
class MetadataFieldDescription:
    """Lightweight description of an important metadata field.

    Args:
        name: Metadata key.
        description: Human-readable description for docs and CLI output.
        example: Optional JSON-compatible example value.
        required: Whether the field must be present during ingest.
        value_types: Optional type labels used by validation.
    """

    name: str
    description: str
    example: JsonValue | None = None
    required: bool = False
    value_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the field description to a plain dictionary."""
        payload: dict[str, JsonValue] = {
            "name": self.name,
            "description": self.description,
            "example": self.example,
            "required": self.required,
        }
        if self.value_types:
            payload["type"] = list(self.value_types)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> MetadataFieldDescription:
        """Build a field description from a plain dictionary."""
        value_types = data.get("type", [])
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            example=data.get("example"),
            required=bool(data.get("required", False)),
            value_types=_coerce_string_list(value_types),
        )


@dataclass(slots=True)
class ArtifactLocator:
    """Minimal locator for a catalogued artifact.

    Args:
        kind: Locator kind, such as ``"path"``, ``"uri"``, or ``"opaque"``.
        value: Locator value, usually a path or URI string.
        relative_path: Optional path relative to a catalog-managed root.
    """

    kind: str
    value: str
    relative_path: str | None = None

    @classmethod
    def path(cls, path: str | Path, *, relative_path: str | None = None) -> ArtifactLocator:
        """Build a locator for a local path-backed artifact.

        Args:
            path: Local filesystem path.
            relative_path: Optional catalog-relative path.

        Returns:
            Path-backed artifact locator.
        """
        return cls(kind="path", value=str(path), relative_path=relative_path)

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the locator to a plain dictionary."""
        return {
            "kind": self.kind,
            "value": self.value,
            "relative_path": self.relative_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> ArtifactLocator:
        """Build a locator from a plain dictionary."""
        if "kind" not in data:
            raise ValueError("locator dictionary is missing required key: kind")
        if "value" not in data:
            raise ValueError("locator dictionary is missing required key: value")
        raw_value = data["value"]
        return cls(
            kind=str(data["kind"]),
            value="" if raw_value is None else str(raw_value),
            relative_path=(None if data.get("relative_path") is None else str(data["relative_path"])),
        )

    def as_path(self) -> Path | None:
        """Return the locator as a path when the locator is path-backed."""
        if self.kind != "path":
            return None
        if not self.value.strip():
            return None
        return Path(self.value)


@dataclass(slots=True)
class CatalogRecord:
    """A single catalogued artifact record.

    Args:
        catalog: Catalog name.
        time_added: ISO 8601 record creation timestamp.
        id: Repository-assigned record identifier.
        record_type: Logical record type.
        locator: Artifact locator.
        stored_abspath: Backwards-compatible absolute path for path records.
        stored_relpath: Backwards-compatible catalog-relative path.
        storage_mode: Storage mode such as ``"copy"``, ``"move"``, or
            ``"external"``.
        original_path: Original source path or URI.
        original_filename: Original source filename.
        suffixes: Source suffixes.
        user_metadata: User-supplied metadata.
        derived_metadata: Extracted or hook-supplied metadata.
        naming_metadata: Metadata used for storage template rendering.
    """

    catalog: str
    time_added: str
    id: str | None = None
    record_type: str = "managed_file"
    locator: ArtifactLocator = field(default_factory=lambda: ArtifactLocator(kind="opaque", value=""))
    stored_abspath: str | None = None
    stored_relpath: str | None = None
    storage_mode: str | None = None
    original_path: str | None = None
    original_filename: str | None = None
    suffixes: list[str] = field(default_factory=list)
    user_metadata: MetadataDict = field(default_factory=dict)
    derived_metadata: MetadataDict = field(default_factory=dict)
    naming_metadata: MetadataDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep compatibility path fields aligned with the locator when possible."""
        if not self.locator.value and self.stored_abspath is not None:
            self.locator = ArtifactLocator.path(
                self.stored_abspath,
                relative_path=self.stored_relpath,
            )
        locator_path = self.locator.as_path()
        if locator_path is not None and self.stored_abspath is None:
            self.stored_abspath = str(locator_path)
        if self.locator.relative_path is not None and self.stored_relpath is None:
            self.stored_relpath = self.locator.relative_path

    def path(self) -> Path | None:
        """Return a local path for path-backed records."""
        return self.locator.as_path()

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the record to a plain dictionary."""
        return cast(
            dict[str, JsonValue],
            {
                "id": self.id,
                "catalog": self.catalog,
                "record_type": self.record_type,
                "locator": self.locator.to_dict(),
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
            },
        )

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> CatalogRecord:
        """Build a record from a plain dictionary."""
        locator_data = data.get("locator")
        locator = _coerce_locator(
            locator_data,
            stored_abspath=data.get("stored_abspath"),
            stored_relpath=data.get("stored_relpath"),
        )
        record_type = data.get("record_type")
        raw_id = data.get("id")
        return cls(
            id=None if raw_id is None else str(raw_id),
            catalog=str(data["catalog"]),
            time_added=str(data["time_added"]),
            record_type="managed_file" if record_type is None else str(record_type),
            locator=locator,
            stored_abspath=(None if data.get("stored_abspath") is None else str(data["stored_abspath"])),
            stored_relpath=(None if data.get("stored_relpath") is None else str(data["stored_relpath"])),
            storage_mode=(None if data.get("storage_mode") is None else str(data["storage_mode"])),
            original_path=(None if data.get("original_path") is None else str(data["original_path"])),
            original_filename=(
                None if data.get("original_filename") is None else str(data["original_filename"])
            ),
            suffixes=_coerce_string_list(data.get("suffixes", [])),
            user_metadata=_coerce_metadata_dict(data.get("user_metadata", {})),
            derived_metadata=_coerce_metadata_dict(data.get("derived_metadata", {})),
            naming_metadata=_coerce_metadata_dict(data.get("naming_metadata", {})),
        )


def _coerce_locator(
    value: JsonValue | None,
    *,
    stored_abspath: JsonValue | None,
    stored_relpath: JsonValue | None,
) -> ArtifactLocator:
    """Coerce locator data, including legacy path-only records."""
    if isinstance(value, ArtifactLocator):
        return value
    if isinstance(value, dict):
        return ArtifactLocator.from_dict(value)
    if stored_abspath is None:
        return ArtifactLocator(kind="opaque", value="")
    return ArtifactLocator.path(
        str(stored_abspath),
        relative_path=None if stored_relpath is None else str(stored_relpath),
    )


def _coerce_string_list(value: JsonValue) -> list[str]:
    """Coerce a JSON list value to a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _coerce_metadata_dict(value: JsonValue) -> MetadataDict:
    """Coerce a JSON object value to metadata."""
    if not isinstance(value, dict):
        return {}
    return dict(value)
