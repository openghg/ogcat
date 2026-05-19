"""Core data models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePath
from typing import TypeAlias, cast

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MetadataDict: TypeAlias = dict[str, JsonValue]

DATA_ARTIFACT_ID = "data"
CORE_ARTIFACT_NAMESPACE = "ogcat.core"
ARTIFACT_SCHEMA_VERSION = "1"
DATA_TYPE_CLAIM_KIND = "data_type"
REPRESENTATION_CLAIM_KIND = "representation"
INTERFACE_CLAIM_KIND = "interface"
STANDARD_CLAIM_KINDS = frozenset(
    {
        DATA_TYPE_CLAIM_KIND,
        REPRESENTATION_CLAIM_KIND,
        INTERFACE_CLAIM_KIND,
    }
)
ARTIFACT_EVIDENCE_VALUES = frozenset(
    {
        "declared",
        "inferred",
        "probed",
        "validated",
        "stale",
        "failed",
    }
)
ARTIFACT_CONFIDENCE_VALUES = ARTIFACT_EVIDENCE_VALUES
ARTIFACT_SCHEMA_FIELDS = frozenset(
    {
        "kind",
        "name",
        "namespace",
        "version",
        "evidence",
        "confidence",
        "metadata",
    }
)
# Descriptive vocabulary from ADR 0002; descriptor roles remain open strings.
STANDARD_ARTIFACT_ROLES = frozenset(
    {
        "data_artifact",
        "auxiliary_artifact",
        "view_link",
        "manifest",
        "preview",
        "log",
        "derived_artifact",
        "replica",
        "cache_copy",
        "archive_copy",
    }
)
ArtifactClaimInput: TypeAlias = "ArtifactClaim | Mapping[str, object]"
ArtifactFacetInput: TypeAlias = "ArtifactFacet | Mapping[str, object]"


def normalize_metadata(
    metadata: object,
    *,
    field_name: str = "metadata",
    label: str | None = None,
) -> MetadataDict:
    """Return metadata as a JSON-compatible dictionary.

    Args:
        metadata: Mapping to normalize recursively.
        field_name: Path used in nested error messages.
        label: Optional user-facing label used for the top-level mapping error.

    Returns:
        Normalized metadata with string keys and JSON-compatible values.

    Raises:
        TypeError: If metadata is not a mapping or contains unsupported values.
        ValueError: If two keys collide after string normalization.
    """
    if not isinstance(metadata, Mapping):
        error_label = label or field_name
        raise TypeError(f"{error_label} must be a dictionary, got {type(metadata).__name__}")

    normalized: MetadataDict = {}
    for key, value in metadata.items():
        normalized_key = str(key)
        if normalized_key in normalized:
            raise ValueError(
                f"{field_name} contains duplicate key after string normalization: {normalized_key!r}"
            )
        normalized[normalized_key] = normalize_metadata_value(
            value,
            field_name=f"{field_name}.{normalized_key}",
        )
    return normalized


def normalize_metadata_value(value: object, *, field_name: str = "metadata") -> JsonValue:
    """Return one metadata value as a JSON-compatible value.

    Args:
        value: Value to normalize recursively.
        field_name: Path used in error messages.

    Returns:
        JSON-compatible normalized value.

    Raises:
        TypeError: If the value cannot be represented safely as JSON metadata.
        ValueError: If nested mapping keys collide after string normalization.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return cast(JsonValue, value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, Mapping):
        return normalize_metadata(value, field_name=field_name)
    if isinstance(value, list | tuple):
        return [
            normalize_metadata_value(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, set | frozenset):
        normalized_items = [
            normalize_metadata_value(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
        return sorted(normalized_items, key=_json_sort_key)

    item = getattr(value, "item", None)
    if callable(item):
        try:
            item_value = item()
        except Exception as exc:
            raise TypeError(
                f"{field_name} must be JSON-compatible; {type(value).__name__}.item() "
                f"could not produce a scalar value"
            ) from exc
        if item_value is not value:
            return normalize_metadata_value(item_value, field_name=field_name)

    raise TypeError(
        f"{field_name} must be JSON-compatible; got {type(value).__name__}. "
        "Supported values are scalars, dates, datetimes, pathlib paths, mappings, "
        "lists, tuples, sets, frozensets, and objects with item() returning a supported scalar."
    )


def _json_sort_key(value: JsonValue) -> str:
    """Return a deterministic sort key for normalized set members."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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

    def __post_init__(self) -> None:
        """Normalize schema example metadata when supplied directly."""
        if self.example is not None:
            self.example = normalize_metadata_value(
                self.example,
                field_name=f"metadata_fields.{self.name}.example",
            )

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
    def from_path(cls, path: str | Path, *, relative_path: str | None = None) -> ArtifactLocator:
        """Build a locator for a local path-backed artifact.

        Args:
            path: Local filesystem path.
            relative_path: Optional catalog-relative path.

        Returns:
            Path-backed artifact locator.
        """
        return cls(kind="path", value=str(path), relative_path=relative_path)

    @classmethod
    def path(cls, path: str | Path, *, relative_path: str | None = None) -> ArtifactLocator:
        """Build a local path locator.

        This compatibility alias is kept for existing code. Prefer
        :meth:`from_path` in new code.
        """
        return cls.from_path(path, relative_path=relative_path)

    @classmethod
    def from_urlpath(cls, urlpath: str, *, relative_path: str | None = None) -> ArtifactLocator:
        """Build a locator for an fsspec-addressable URL path.

        Args:
            urlpath: URL path understood by fsspec, such as ``s3://...``.
            relative_path: Optional storage-root-relative path.

        Returns:
            URL-path-backed artifact locator.
        """
        return cls(kind="urlpath", value=str(urlpath), relative_path=relative_path)

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
class ArtifactClaim:
    """Namespaced claim about an artifact's data type, representation, or interface.

    Args:
        kind: Claim category. Core recognizes ``"data_type"``, ``"representation"``,
            and ``"interface"``, but stores other non-empty strings for plugin-owned
            claim categories.
        name: Namespaced claim name, such as ``"bytes"``, ``"netcdf"``, or
            ``"xarray-dataset"``.
        namespace: Stable namespace that owns the claim name.
        version: Version of the namespace-local claim schema.
        evidence: How the claim was produced.
        confidence: Confidence/status term for the claim. Defaults to ``evidence``.
        metadata: JSON-compatible structured details for this claim.
    """

    kind: str
    name: str
    namespace: str = CORE_ARTIFACT_NAMESPACE
    version: str = ARTIFACT_SCHEMA_VERSION
    evidence: str = "declared"
    confidence: str | None = None
    metadata: MetadataDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize claim fields to the supported JSON-safe shape."""
        self.kind = _coerce_required_schema_text(self.kind, field_name="claim.kind")
        self.name = _coerce_required_schema_text(self.name, field_name="claim.name")
        self.namespace = _coerce_required_schema_text(
            self.namespace,
            field_name=f"claim[{self.kind}:{self.name}].namespace",
        )
        self.version = _coerce_required_schema_text(
            self.version,
            field_name=f"claim[{self.kind}:{self.name}].version",
        )
        self.evidence = _coerce_artifact_vocabulary(
            self.evidence,
            field_name=f"claim[{self.kind}:{self.name}].evidence",
            allowed=ARTIFACT_EVIDENCE_VALUES,
        )
        self.confidence = _coerce_artifact_vocabulary(
            self.evidence if self.confidence is None else self.confidence,
            field_name=f"claim[{self.kind}:{self.name}].confidence",
            allowed=ARTIFACT_CONFIDENCE_VALUES,
        )
        self.metadata = normalize_metadata(
            self.metadata,
            field_name=f"claim[{self.kind}:{self.name}].metadata",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the claim to its JSON-compatible dictionary shape."""
        return {
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "metadata": normalize_metadata(
                self.metadata,
                field_name=f"claim[{self.kind}:{self.name}].metadata",
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object], *, field_name: str = "claim") -> ArtifactClaim:
        """Build an artifact claim from a plain dictionary."""
        return cls(
            kind=_required_schema_mapping_text(data, "kind", field_name=field_name),
            name=_required_schema_mapping_text(data, "name", field_name=field_name),
            namespace=_schema_mapping_text_with_default(
                data,
                "namespace",
                field_name=field_name,
                default=CORE_ARTIFACT_NAMESPACE,
            ),
            version=_schema_mapping_text_with_default(
                data,
                "version",
                field_name=field_name,
                default=ARTIFACT_SCHEMA_VERSION,
            ),
            evidence=_schema_mapping_text_with_default(
                data,
                "evidence",
                field_name=field_name,
                default="declared",
            ),
            confidence=_optional_schema_mapping_text(
                data,
                "confidence",
                field_name=field_name,
                default=None,
            ),
            metadata=_schema_metadata_with_extension_fields(data, field_name=field_name),
        )


class DataTypeClaim(ArtifactClaim):
    """Artifact claim describing the external or source data type."""

    def __init__(
        self,
        name: str,
        *,
        namespace: str = CORE_ARTIFACT_NAMESPACE,
        version: str = ARTIFACT_SCHEMA_VERSION,
        evidence: str = "declared",
        confidence: str | None = None,
        metadata: MetadataDict | None = None,
    ) -> None:
        """Create a data type claim."""
        super().__init__(
            kind=DATA_TYPE_CLAIM_KIND,
            name=name,
            namespace=namespace,
            version=version,
            evidence=evidence,
            confidence=confidence,
            metadata={} if metadata is None else metadata,
        )


class RepresentationClaim(ArtifactClaim):
    """Artifact claim describing a storage or encoding representation."""

    def __init__(
        self,
        name: str,
        *,
        namespace: str = CORE_ARTIFACT_NAMESPACE,
        version: str = ARTIFACT_SCHEMA_VERSION,
        evidence: str = "declared",
        confidence: str | None = None,
        metadata: MetadataDict | None = None,
    ) -> None:
        """Create a representation claim."""
        super().__init__(
            kind=REPRESENTATION_CLAIM_KIND,
            name=name,
            namespace=namespace,
            version=version,
            evidence=evidence,
            confidence=confidence,
            metadata={} if metadata is None else metadata,
        )


class InterfaceClaim(ArtifactClaim):
    """Artifact claim describing an access interface exposed by an artifact."""

    def __init__(
        self,
        name: str,
        *,
        namespace: str = CORE_ARTIFACT_NAMESPACE,
        version: str = ARTIFACT_SCHEMA_VERSION,
        evidence: str = "declared",
        confidence: str | None = None,
        metadata: MetadataDict | None = None,
    ) -> None:
        """Create an interface claim."""
        super().__init__(
            kind=INTERFACE_CLAIM_KIND,
            name=name,
            namespace=namespace,
            version=version,
            evidence=evidence,
            confidence=confidence,
            metadata={} if metadata is None else metadata,
        )


@dataclass(slots=True)
class ArtifactFacet:
    """Namespaced structured fact about an artifact or claim.

    Args:
        kind: Facet category, such as ``"stat"``, ``"suffix"``, or a
            plugin-owned category.
        name: Namespace-local facet name.
        namespace: Stable namespace that owns the facet name.
        version: Version of the namespace-local facet schema.
        evidence: How the facet was produced.
        confidence: Confidence/status term for the facet. Defaults to ``evidence``.
        metadata: JSON-compatible structured fact payload.
    """

    kind: str
    name: str
    namespace: str = CORE_ARTIFACT_NAMESPACE
    version: str = ARTIFACT_SCHEMA_VERSION
    evidence: str = "declared"
    confidence: str | None = None
    metadata: MetadataDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize facet fields to the supported JSON-safe shape."""
        self.kind = _coerce_required_schema_text(self.kind, field_name="facet.kind")
        self.name = _coerce_required_schema_text(self.name, field_name="facet.name")
        self.namespace = _coerce_required_schema_text(
            self.namespace,
            field_name=f"facet[{self.kind}:{self.name}].namespace",
        )
        self.version = _coerce_required_schema_text(
            self.version,
            field_name=f"facet[{self.kind}:{self.name}].version",
        )
        self.evidence = _coerce_artifact_vocabulary(
            self.evidence,
            field_name=f"facet[{self.kind}:{self.name}].evidence",
            allowed=ARTIFACT_EVIDENCE_VALUES,
        )
        self.confidence = _coerce_artifact_vocabulary(
            self.evidence if self.confidence is None else self.confidence,
            field_name=f"facet[{self.kind}:{self.name}].confidence",
            allowed=ARTIFACT_CONFIDENCE_VALUES,
        )
        self.metadata = normalize_metadata(
            self.metadata,
            field_name=f"facet[{self.kind}:{self.name}].metadata",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the facet to its JSON-compatible dictionary shape."""
        return {
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "metadata": normalize_metadata(
                self.metadata,
                field_name=f"facet[{self.kind}:{self.name}].metadata",
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object], *, field_name: str = "facet") -> ArtifactFacet:
        """Build an artifact facet from a plain dictionary."""
        kind = _required_schema_mapping_text(data, "kind", field_name=field_name)
        return cls(
            kind=kind,
            name=_schema_mapping_text_with_default(
                data,
                "name",
                field_name=field_name,
                default=kind,
            ),
            namespace=_schema_mapping_text_with_default(
                data,
                "namespace",
                field_name=field_name,
                default=CORE_ARTIFACT_NAMESPACE,
            ),
            version=_schema_mapping_text_with_default(
                data,
                "version",
                field_name=field_name,
                default=ARTIFACT_SCHEMA_VERSION,
            ),
            evidence=_schema_mapping_text_with_default(
                data,
                "evidence",
                field_name=field_name,
                default="declared",
            ),
            confidence=_optional_schema_mapping_text(
                data,
                "confidence",
                field_name=field_name,
                default=None,
            ),
            metadata=_schema_metadata_with_extension_fields(data, field_name=field_name),
        )


Representation = RepresentationClaim
Facet = ArtifactFacet


@dataclass(slots=True)
class ArtifactDescriptor:
    """Persistent descriptor for one artifact owned by a catalog record.

    Args:
        id: Record-local artifact identifier.
        role: Artifact role, such as ``"data_artifact"`` or ``"view_link"``.
        locator: Optional locator for physical or resolvable artifacts.
        state: Lightweight lifecycle or availability state.
        relationship: JSON-compatible relationship metadata.
        claims: Artifact claims normalized to explicit JSON-compatible dictionaries.
        facets: Artifact facets normalized to explicit JSON-compatible dictionaries.
    """

    id: str
    role: str
    locator: ArtifactLocator | None = None
    state: str = "available"
    relationship: MetadataDict = field(default_factory=dict)
    claims: list[ArtifactClaimInput] = field(default_factory=list)
    facets: list[ArtifactFacetInput] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize descriptor fields to the supported JSON-safe shape."""
        self.id = str(self.id)
        self.role = str(self.role)
        self.state = str(self.state)
        if not self.id:
            raise ValueError("artifact descriptor id cannot be empty")
        if not self.role.strip():
            raise ValueError("artifact descriptor role cannot be empty")
        if self.locator is not None and not isinstance(self.locator, ArtifactLocator):
            raise TypeError(
                "artifact descriptor locator must be an ArtifactLocator or None, "
                f"got {type(self.locator).__name__}"
            )
        self.relationship = _coerce_required_metadata_dict(
            self.relationship,
            field_name=f"artifacts[{self.id}].relationship",
        )
        self.claims = _coerce_artifact_claims(
            self.claims,
            field_name=f"artifacts[{self.id}].claims",
        )
        self.facets = _coerce_artifact_facets(
            self.facets,
            field_name=f"artifacts[{self.id}].facets",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the artifact descriptor to a plain dictionary."""
        return cast(
            dict[str, JsonValue],
            {
                "id": self.id,
                "role": self.role,
                "locator": None if self.locator is None else self.locator.to_dict(),
                "state": self.state,
                "relationship": normalize_metadata(
                    self.relationship,
                    field_name=f"artifacts[{self.id}].relationship",
                ),
                "claims": [
                    _coerce_artifact_claim(
                        claim,
                        field_name=f"artifacts[{self.id}].claims[{index}]",
                    )
                    for index, claim in enumerate(self.claims)
                ],
                "facets": [
                    _coerce_artifact_facet(
                        facet,
                        field_name=f"artifacts[{self.id}].facets[{index}]",
                    )
                    for index, facet in enumerate(self.facets)
                ],
            },
        )

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> ArtifactDescriptor:
        """Build an artifact descriptor from a plain dictionary."""
        if "id" not in data:
            raise ValueError("artifact descriptor dictionary is missing required key: id")
        if "role" not in data:
            raise ValueError("artifact descriptor dictionary is missing required key: role")
        raw_id = data["id"]
        raw_role = data["role"]
        return cls(
            id="" if raw_id is None else str(raw_id),
            role="" if raw_role is None else str(raw_role),
            locator=_coerce_optional_locator(data.get("locator")),
            state="available" if data.get("state") is None else str(data["state"]),
            relationship=_coerce_required_metadata_dict(
                data.get("relationship", {}),
                field_name=f"artifacts[{data['id']}].relationship",
            ),
            claims=_coerce_artifact_claims(
                data.get("claims", []),
                field_name=f"artifacts[{data['id']}].claims",
            ),
            facets=_coerce_artifact_facets(
                data.get("facets", []),
                field_name=f"artifacts[{data['id']}].facets",
            ),
        )


@dataclass(slots=True)
class CatalogRecord:
    """A single catalogued artifact record.

    Args:
        catalog: Catalog name.
        time_added: ISO 8601 record creation timestamp.
        id: Repository-assigned record identifier.
        record_type: Logical record type.
        locator: Artifact locator.
        artifacts: Artifact descriptors owned by this record.
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
    artifacts: list[ArtifactDescriptor] = field(default_factory=list)
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
        self.user_metadata = normalize_metadata(self.user_metadata, field_name="user_metadata")
        self.derived_metadata = normalize_metadata(
            self.derived_metadata,
            field_name="derived_metadata",
        )
        self.naming_metadata = normalize_metadata(
            self.naming_metadata,
            field_name="naming_metadata",
        )
        if not self.locator.value and self.stored_abspath is not None:
            self.locator = ArtifactLocator.path(
                self.stored_abspath,
                relative_path=self.stored_relpath,
            )
        self.artifacts = _coerce_artifact_descriptors(self.artifacts)
        data_locator = _first_data_artifact_locator(self.artifacts)
        if data_locator is not None:
            self.locator = data_locator
            _sync_path_fields_from_locator(self)
        elif not self.artifacts and _locator_has_value(self.locator):
            self.artifacts = [_data_artifact_descriptor(self.locator)]
            _fill_missing_path_fields_from_locator(self)
        else:
            _fill_missing_path_fields_from_locator(self)

    def path(self) -> Path | None:
        """Return a local path for path-backed records."""
        return self.locator.as_path()

    def to_dict(self) -> dict[str, JsonValue]:
        """Convert the record to a plain dictionary."""
        user_metadata = normalize_metadata(self.user_metadata, field_name="user_metadata")
        derived_metadata = normalize_metadata(self.derived_metadata, field_name="derived_metadata")
        naming_metadata = normalize_metadata(self.naming_metadata, field_name="naming_metadata")
        return cast(
            dict[str, JsonValue],
            {
                "id": self.id,
                "catalog": self.catalog,
                "record_type": self.record_type,
                "locator": self.locator.to_dict(),
                "artifacts": [artifact.to_dict() for artifact in self.artifacts],
                "stored_abspath": self.stored_abspath,
                "stored_relpath": self.stored_relpath,
                "storage_mode": self.storage_mode,
                "time_added": self.time_added,
                "original_path": self.original_path,
                "original_filename": self.original_filename,
                "suffixes": self.suffixes,
                "user_metadata": user_metadata,
                "derived_metadata": derived_metadata,
                "naming_metadata": naming_metadata,
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
            artifacts=_coerce_artifact_descriptors(data.get("artifacts", [])),
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


def _coerce_optional_locator(value: object) -> ArtifactLocator | None:
    """Coerce optional artifact descriptor locator data."""
    if value is None:
        return None
    if isinstance(value, ArtifactLocator):
        return value
    if isinstance(value, Mapping):
        return ArtifactLocator.from_dict(dict(cast(dict[str, JsonValue], value)))
    raise TypeError(f"artifact descriptor locator must be a dictionary or None, got {type(value).__name__}")


def _coerce_artifact_descriptors(value: object) -> list[ArtifactDescriptor]:
    """Coerce artifact descriptor input to descriptor objects."""
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise TypeError(f"artifacts must be a list of artifact descriptors, got {type(value).__name__}")

    descriptors: list[ArtifactDescriptor] = []
    seen_ids: set[str] = set()
    data_artifact_seen = False
    for index, item in enumerate(value):
        if isinstance(item, ArtifactDescriptor):
            descriptor = item
        elif isinstance(item, Mapping):
            descriptor = ArtifactDescriptor.from_dict(dict(cast(dict[str, JsonValue], item)))
        else:
            raise TypeError(
                f"artifacts[{index}] must be an ArtifactDescriptor or dictionary, got {type(item).__name__}"
            )
        if descriptor.id in seen_ids:
            raise ValueError(f"artifacts contains duplicate artifact id: {descriptor.id!r}")
        if descriptor.role == "data_artifact":
            if data_artifact_seen:
                raise ValueError("artifacts contains multiple data_artifact descriptors")
            data_artifact_seen = True
        seen_ids.add(descriptor.id)
        descriptors.append(descriptor)
    return descriptors


def _coerce_required_metadata_dict(value: object, *, field_name: str) -> MetadataDict:
    """Coerce a descriptor metadata mapping and fail when the shape is wrong."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a dictionary, got {type(value).__name__}")
    return normalize_metadata(value, field_name=field_name)


def _coerce_artifact_claims(value: object, *, field_name: str) -> list[ArtifactClaimInput]:
    """Coerce artifact claim input to normalized claim dictionaries."""
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise TypeError(
            f"{field_name} must be a list of ArtifactClaim objects or dictionaries, "
            f"got {type(value).__name__}"
        )

    claims = [
        _coerce_artifact_claim(item, field_name=f"{field_name}[{index}]") for index, item in enumerate(value)
    ]
    return cast(list[ArtifactClaimInput], claims)


def _coerce_artifact_claim(value: object, *, field_name: str) -> MetadataDict:
    """Coerce one artifact claim input to a normalized claim dictionary."""
    if isinstance(value, ArtifactClaim):
        return value.to_dict()
    if isinstance(value, Mapping):
        return ArtifactClaim.from_dict(value, field_name=field_name).to_dict()
    raise TypeError(f"{field_name} must be an ArtifactClaim or dictionary, got {type(value).__name__}")


def _coerce_artifact_facets(value: object, *, field_name: str) -> list[ArtifactFacetInput]:
    """Coerce artifact facet input to normalized facet dictionaries."""
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise TypeError(
            f"{field_name} must be a list of ArtifactFacet objects or dictionaries, "
            f"got {type(value).__name__}"
        )

    facets = [
        _coerce_artifact_facet(item, field_name=f"{field_name}[{index}]") for index, item in enumerate(value)
    ]
    return cast(list[ArtifactFacetInput], facets)


def _coerce_artifact_facet(value: object, *, field_name: str) -> MetadataDict:
    """Coerce one artifact facet input to a normalized facet dictionary."""
    if isinstance(value, ArtifactFacet):
        return value.to_dict()
    if isinstance(value, Mapping):
        return ArtifactFacet.from_dict(value, field_name=field_name).to_dict()
    raise TypeError(f"{field_name} must be an ArtifactFacet or dictionary, got {type(value).__name__}")


def _required_schema_mapping_text(
    data: Mapping[str, object],
    key: str,
    *,
    field_name: str,
) -> str:
    """Return a required artifact schema text field from a mapping."""
    if key not in data:
        raise ValueError(f"{field_name} is missing required key: {key}")
    return _coerce_required_schema_text(data[key], field_name=f"{field_name}.{key}")


def _optional_schema_mapping_text(
    data: Mapping[str, object],
    key: str,
    *,
    field_name: str,
    default: str | None,
) -> str | None:
    """Return an optional artifact schema text field from a mapping."""
    if key not in data or data[key] is None:
        return default
    return _coerce_required_schema_text(data[key], field_name=f"{field_name}.{key}")


def _schema_mapping_text_with_default(
    data: Mapping[str, object],
    key: str,
    *,
    field_name: str,
    default: str,
) -> str:
    """Return an optional artifact schema text field with a non-null default."""
    value = _optional_schema_mapping_text(data, key, field_name=field_name, default=default)
    if value is None:
        return default
    return value


def _coerce_required_schema_text(value: object, *, field_name: str) -> str:
    """Coerce a required artifact schema identifier to non-empty text."""
    if value is None:
        raise ValueError(f"{field_name} cannot be None")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} cannot be empty")
    return text


def _coerce_artifact_vocabulary(
    value: object,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    """Validate one artifact schema vocabulary value."""
    text = _coerce_required_schema_text(value, field_name=field_name)
    if text not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of {allowed_values}; got {text!r}")
    return text


def _schema_metadata_with_extension_fields(
    data: Mapping[str, object],
    *,
    field_name: str,
) -> MetadataDict:
    """Return explicit metadata plus legacy extension fields folded into it."""
    raw_metadata = data.get("metadata", {})
    if raw_metadata is None:
        metadata: MetadataDict = {}
    else:
        metadata = _coerce_required_metadata_dict(
            raw_metadata,
            field_name=f"{field_name}.metadata",
        )

    extra_fields: dict[str, object] = {}
    for key, value in data.items():
        normalized_key = str(key)
        if normalized_key in ARTIFACT_SCHEMA_FIELDS:
            continue
        if normalized_key in extra_fields:
            raise ValueError(
                f"{field_name} contains duplicate extension key after string normalization: "
                f"{normalized_key!r}"
            )
        extra_fields[normalized_key] = value

    if not extra_fields:
        return metadata

    extras = normalize_metadata(extra_fields, field_name=field_name)
    for key, value in extras.items():
        if key in metadata:
            raise ValueError(f"{field_name}.metadata duplicates top-level extension key: {key!r}")
        metadata[key] = value
    return metadata


def _first_data_artifact_locator(artifacts: list[ArtifactDescriptor]) -> ArtifactLocator | None:
    """Return the data-artifact locator in a descriptor list."""
    for artifact in artifacts:
        if artifact.role == "data_artifact" and artifact.locator is not None:
            return artifact.locator
    return None


def _sync_path_fields_from_locator(record: CatalogRecord) -> None:
    """Replace legacy path fields from the current compatibility locator."""
    locator_path = record.locator.as_path()
    if locator_path is None:
        record.stored_abspath = None
        record.stored_relpath = None
        return
    record.stored_abspath = str(locator_path)
    record.stored_relpath = record.locator.relative_path


def _fill_missing_path_fields_from_locator(record: CatalogRecord) -> None:
    """Populate missing legacy path fields from the current compatibility locator."""
    locator_path = record.locator.as_path()
    if locator_path is not None and record.stored_abspath is None:
        record.stored_abspath = str(locator_path)
    if (
        record.locator.kind == "path"
        and record.locator.relative_path is not None
        and record.stored_relpath is None
    ):
        record.stored_relpath = record.locator.relative_path


def _locator_has_value(locator: ArtifactLocator) -> bool:
    """Return whether a locator describes a meaningful artifact location."""
    return bool(locator.value.strip())


def _data_artifact_descriptor(locator: ArtifactLocator) -> ArtifactDescriptor:
    """Build the compatibility data artifact descriptor for a locator."""
    return ArtifactDescriptor(id=DATA_ARTIFACT_ID, role="data_artifact", locator=locator)


def _coerce_string_list(value: JsonValue) -> list[str]:
    """Coerce a JSON list value to a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _coerce_metadata_dict(value: JsonValue) -> MetadataDict:
    """Coerce a JSON object value to metadata."""
    if not isinstance(value, dict):
        return {}
    return normalize_metadata(value)
