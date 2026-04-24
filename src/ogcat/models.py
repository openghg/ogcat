"""Core data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
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
    def from_dict(cls, data: dict[str, JsonValue]) -> MetadataFieldDescription:
        """Build a field description from a plain dictionary."""
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            example=data.get("example"),
            required=bool(data.get("required", False)),
        )


@dataclass(slots=True)
class ArtifactLocator:
    """Minimal locator for a catalogued artifact."""

    kind: str
    value: str
    relative_path: str | None = None

    @classmethod
    def path(cls, path: str | Path, *, relative_path: str | None = None) -> ArtifactLocator:
        """Build a locator for a local path-backed artifact."""
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
        return cls(
            kind=str(data["kind"]),
            value=str(data["value"]),
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
    """A single catalogued artifact record."""

    id: str
    catalog: str
    time_added: str
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
        return {
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
        }

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
        return cls(
            id=str(data["id"]),
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
            suffixes=[str(x) for x in data.get("suffixes", [])],
            user_metadata=dict(data.get("user_metadata", {})),
            derived_metadata=dict(data.get("derived_metadata", {})),
            naming_metadata=dict(data.get("naming_metadata", {})),
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
