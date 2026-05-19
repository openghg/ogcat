"""Artifact capability registration and lookup."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from ogcat.artifact_claims import claim_key, facet_key, iter_claims, iter_facets
from ogcat.models import (
    ARTIFACT_SCHEMA_VERSION,
    CORE_ARTIFACT_NAMESPACE,
    INTERFACE_CLAIM_KIND,
    ArtifactClaim,
    ArtifactDescriptor,
    ArtifactFacet,
    MetadataDict,
    normalize_metadata,
)

ArtifactClaimInput: TypeAlias = ArtifactClaim | Mapping[str, object]
ArtifactFacetInput: TypeAlias = ArtifactFacet | Mapping[str, object]
ArtifactSchemaKey: TypeAlias = tuple[str, str, str, str]
CapabilityKey: TypeAlias = tuple[str, str, str, str]


class CapabilityKind(StrEnum):
    """Standard capability kinds recognized by the core registry."""

    READER = "reader"
    WRITER = "writer"
    CONVERTER = "converter"


class CapabilityError(Exception):
    """Base class for capability registry errors."""


class CapabilityRegistrationError(CapabilityError):
    """Raised when a capability cannot be registered."""


class CapabilityLookupError(CapabilityError):
    """Base class for capability lookup errors."""


class InvalidCapabilityLookupError(CapabilityLookupError):
    """Raised when lookup filters contain malformed claims, facets, or kinds."""


class MissingCapabilityError(CapabilityLookupError):
    """Raised when no registered capability satisfies a supported request."""


class UnsupportedInterfaceError(CapabilityLookupError):
    """Raised when a descriptor does not expose a requested interface claim."""


class AmbiguousCapabilityError(CapabilityLookupError):
    """Raised when a lookup request matches more than one capability."""

    def __init__(self, candidates: Iterable[str]) -> None:
        """Create an ambiguity error with deterministic candidate names."""
        self.candidates = tuple(sorted(candidates))
        super().__init__("Multiple capabilities match the request: " + ", ".join(self.candidates))


@dataclass(slots=True)
class ArtifactCapability:
    """Descriptor for an artifact capability implementation.

    Args:
        kind: Capability category, such as ``CapabilityKind.READER``.
        name: Namespace-local capability name.
        namespace: Stable namespace that owns the capability.
        version: Version of the capability contract.
        input_claims: Claims required on input descriptors.
        output_claims: Claims produced by the capability.
        required_facets: Facets required on input descriptors.
        options: JSON-compatible capability option metadata.
        metadata: JSON-compatible descriptive metadata.
        implementation: Opaque implementation object stored by the registry.
    """

    kind: CapabilityKind | str
    name: str
    namespace: str = CORE_ARTIFACT_NAMESPACE
    version: str = ARTIFACT_SCHEMA_VERSION
    input_claims: Iterable[ArtifactClaimInput] = field(default_factory=tuple)
    output_claims: Iterable[ArtifactClaimInput] = field(default_factory=tuple)
    required_facets: Iterable[ArtifactFacetInput] = field(default_factory=tuple)
    options: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    implementation: object | None = None

    def __post_init__(self) -> None:
        """Normalize fields used by registry matching."""
        self.kind = _coerce_kind(self.kind)
        self.name = _coerce_required_text(self.name, field_name="capability.name")
        self.namespace = _coerce_required_text(
            self.namespace,
            field_name=f"capability[{self.kind}:{self.name}].namespace",
        )
        self.version = _coerce_required_text(
            self.version,
            field_name=f"capability[{self.kind}:{self.name}].version",
        )
        self.input_claims = _normalize_claims(
            self.input_claims,
            field_name=f"capability[{self.kind}:{self.name}].input_claims",
        )
        self.output_claims = _normalize_claims(
            self.output_claims,
            field_name=f"capability[{self.kind}:{self.name}].output_claims",
        )
        self.required_facets = _normalize_facets(
            self.required_facets,
            field_name=f"capability[{self.kind}:{self.name}].required_facets",
        )
        self.options = normalize_metadata(
            self.options,
            field_name=f"capability[{self.kind}:{self.name}].options",
            label="capability options",
        )
        self.metadata = normalize_metadata(
            self.metadata,
            field_name=f"capability[{self.kind}:{self.name}].metadata",
            label="capability metadata",
        )

    @property
    def key(self) -> CapabilityKey:
        """Return the stable registry key for this capability."""
        return (self.namespace, _kind_value(self.kind), self.name, self.version)

    @property
    def display_name(self) -> str:
        """Return a deterministic user-facing capability identifier."""
        return f"{self.namespace}:{_kind_value(self.kind)}:{self.name}@{self.version}"


class CapabilityRegistry:
    """In-memory registry for artifact capabilities."""

    def __init__(self, capabilities: Iterable[ArtifactCapability] = ()) -> None:
        """Create a registry and register initial capabilities.

        Args:
            capabilities: Capabilities to register in lookup order.
        """
        self._capabilities: dict[CapabilityKey, ArtifactCapability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: ArtifactCapability) -> ArtifactCapability:
        """Register a capability and return it for decorator-style usage.

        Args:
            capability: Capability descriptor to register.

        Returns:
            The registered capability.

        Raises:
            CapabilityRegistrationError: If the capability is invalid or duplicated.
        """
        if not isinstance(capability, ArtifactCapability):
            raise CapabilityRegistrationError(
                f"capability must be an ArtifactCapability, got {type(capability).__name__}"
            )

        _validate_capability_contract(capability)
        if capability.key in self._capabilities:
            raise CapabilityRegistrationError(f"capability is already registered: {capability.display_name}")
        self._capabilities[capability.key] = capability
        return capability

    def list(self) -> tuple[ArtifactCapability, ...]:
        """Return registered capabilities in insertion order."""
        return tuple(self._capabilities.values())

    def find(
        self,
        *,
        kind: CapabilityKind | str | None = None,
        name: str | None = None,
        namespace: str | None = None,
        version: str | None = None,
        descriptor: ArtifactDescriptor | None = None,
        input_claims: Iterable[ArtifactClaimInput] = (),
        output_claims: Iterable[ArtifactClaimInput] = (),
        required_facets: Iterable[ArtifactFacetInput] = (),
    ) -> tuple[ArtifactCapability, ...]:
        """Find capabilities matching the supplied filters.

        Args:
            kind: Optional capability kind filter.
            name: Optional capability name filter.
            namespace: Optional capability namespace filter.
            version: Optional capability version filter.
            descriptor: Optional artifact descriptor capabilities must support.
            input_claims: Input claims the capability must require.
            output_claims: Output claims the capability must produce.
            required_facets: Required facets the capability must declare.

        Returns:
            Matching capabilities in registration order.

        Raises:
            InvalidCapabilityLookupError: If lookup claim or facet filters are malformed.
        """
        try:
            kind_filter = None if kind is None else _kind_text(kind, field_name="kind")
            input_keys = _claim_keys(input_claims, field_name="input_claims")
            output_keys = _claim_keys(output_claims, field_name="output_claims")
            required_facet_filters = _normalize_facets(required_facets, field_name="required_facets")
            descriptor_claim_keys: frozenset[ArtifactSchemaKey] | None = None
            descriptor_facets: tuple[MetadataDict, ...] | None = None
            if descriptor is not None:
                descriptor_claim_keys = frozenset(claim_key(claim) for claim in iter_claims(descriptor))
                descriptor_facets = tuple(iter_facets(descriptor))

            return tuple(
                capability
                for capability in self._capabilities.values()
                if _capability_matches(
                    capability,
                    kind=kind_filter,
                    name=name,
                    namespace=namespace,
                    version=version,
                    descriptor_claim_keys=descriptor_claim_keys,
                    descriptor_facets=descriptor_facets,
                    input_keys=input_keys,
                    output_keys=output_keys,
                    required_facet_filters=required_facet_filters,
                )
            )
        except (TypeError, ValueError) as exc:
            raise InvalidCapabilityLookupError(f"Invalid capability lookup request: {exc}") from exc

    def select(
        self,
        *,
        kind: CapabilityKind | str | None = None,
        name: str | None = None,
        namespace: str | None = None,
        version: str | None = None,
        descriptor: ArtifactDescriptor | None = None,
        input_claims: Iterable[ArtifactClaimInput] = (),
        output_claims: Iterable[ArtifactClaimInput] = (),
        required_facets: Iterable[ArtifactFacetInput] = (),
    ) -> ArtifactCapability:
        """Select exactly one capability for an explicit request.

        Raises:
            UnsupportedInterfaceError: If a requested interface claim is absent
                from ``descriptor``.
            MissingCapabilityError: If no capability supports the request.
            AmbiguousCapabilityError: If more than one capability supports the
                request.
            InvalidCapabilityLookupError: If lookup claim or facet filters are malformed.
        """
        try:
            normalized_input_claims = _normalize_claims(input_claims, field_name="input_claims")
            if descriptor is not None:
                _require_requested_interfaces(descriptor, normalized_input_claims)
        except (TypeError, ValueError) as exc:
            raise InvalidCapabilityLookupError(f"Invalid capability lookup request: {exc}") from exc

        matches = self.find(
            kind=kind,
            name=name,
            namespace=namespace,
            version=version,
            descriptor=descriptor,
            input_claims=normalized_input_claims,
            output_claims=output_claims,
            required_facets=required_facets,
        )
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise MissingCapabilityError("No capability matches the request.")
        raise AmbiguousCapabilityError(capability.display_name for capability in matches)


def _capability_matches(
    capability: ArtifactCapability,
    *,
    kind: str | None,
    name: str | None,
    namespace: str | None,
    version: str | None,
    descriptor_claim_keys: frozenset[ArtifactSchemaKey] | None,
    descriptor_facets: tuple[MetadataDict, ...] | None,
    input_keys: frozenset[ArtifactSchemaKey],
    output_keys: frozenset[ArtifactSchemaKey],
    required_facet_filters: tuple[MetadataDict, ...],
) -> bool:
    """Return whether a capability satisfies registry filters."""
    capability_input_keys = _claim_keys(capability.input_claims, field_name="capability.input_claims")
    capability_output_keys = _claim_keys(capability.output_claims, field_name="capability.output_claims")
    capability_required_facets = _normalize_facets(
        capability.required_facets,
        field_name="capability.required_facets",
    )
    if kind is not None and _kind_value(capability.kind) != kind:
        return False
    if name is not None and capability.name != name:
        return False
    if namespace is not None and capability.namespace != namespace:
        return False
    if version is not None and capability.version != version:
        return False
    if not input_keys.issubset(capability_input_keys):
        return False
    if not output_keys.issubset(capability_output_keys):
        return False
    if not _facets_satisfy(required_facet_filters, candidates=capability_required_facets):
        return False
    if descriptor_claim_keys is not None and not capability_input_keys.issubset(descriptor_claim_keys):
        return False
    return descriptor_facets is None or _facets_satisfy(
        capability_required_facets, candidates=descriptor_facets
    )


def _validate_capability_contract(capability: ArtifactCapability) -> None:
    """Validate kind-specific capability declaration requirements."""
    kind = _kind_value(capability.kind)
    if kind not in {member.value for member in CapabilityKind}:
        raise CapabilityRegistrationError(
            f"capability kind must be one of reader, writer, converter; got {kind!r}"
        )

    input_keys = _claim_keys(capability.input_claims, field_name="capability.input_claims")
    output_keys = _claim_keys(capability.output_claims, field_name="capability.output_claims")
    if kind == CapabilityKind.READER.value and not _has_interface_key(input_keys):
        raise CapabilityRegistrationError(
            f"reader capability {capability.display_name} must declare at least one input interface claim"
        )
    if kind == CapabilityKind.WRITER.value and not output_keys:
        raise CapabilityRegistrationError(
            f"writer capability {capability.display_name} must declare at least one output claim"
        )
    if kind == CapabilityKind.CONVERTER.value:
        if not input_keys:
            raise CapabilityRegistrationError(
                f"converter capability {capability.display_name} must declare at least one input claim"
            )
        if not output_keys:
            raise CapabilityRegistrationError(
                f"converter capability {capability.display_name} must declare at least one output claim"
            )


def _has_interface_key(keys: Iterable[ArtifactSchemaKey]) -> bool:
    """Return whether any schema key is an interface claim key."""
    return any(kind == INTERFACE_CLAIM_KIND for _, kind, _, _ in keys)


def _require_requested_interfaces(
    descriptor: ArtifactDescriptor,
    input_claims: tuple[MetadataDict, ...],
) -> None:
    """Raise when explicitly requested interface claims are absent."""
    requested = tuple(claim for claim in input_claims if claim["kind"] == INTERFACE_CLAIM_KIND)
    if not requested:
        return
    descriptor_claim_keys = frozenset(claim_key(claim) for claim in iter_claims(descriptor))
    for claim in requested:
        if claim_key(claim) not in descriptor_claim_keys:
            raise UnsupportedInterfaceError(
                "Descriptor does not expose requested interface claim: "
                + _schema_display_name(claim_key(claim))
            )


def _normalize_claims(
    claims: Iterable[ArtifactClaimInput],
    *,
    field_name: str,
) -> tuple[MetadataDict, ...]:
    """Normalize claim inputs through ``ArtifactClaim``."""
    normalized = tuple(
        _normalize_claim(claim, field_name=f"{field_name}[{index}]") for index, claim in enumerate(claims)
    )
    _reject_duplicate_schema_keys(
        (claim_key(claim) for claim in normalized),
        field_name=field_name,
    )
    return normalized


def _normalize_claim(claim: ArtifactClaimInput, *, field_name: str) -> MetadataDict:
    """Normalize one claim input through ``ArtifactClaim``."""
    if isinstance(claim, ArtifactClaim):
        return claim.to_dict()
    if isinstance(claim, Mapping):
        return ArtifactClaim.from_dict(claim, field_name=field_name).to_dict()
    raise TypeError(f"{field_name} must be an ArtifactClaim or dictionary, got {type(claim).__name__}")


def _normalize_facets(
    facets: Iterable[ArtifactFacetInput],
    *,
    field_name: str,
) -> tuple[MetadataDict, ...]:
    """Normalize facet inputs through ``ArtifactFacet``."""
    normalized = tuple(
        _normalize_facet(facet, field_name=f"{field_name}[{index}]") for index, facet in enumerate(facets)
    )
    _reject_duplicate_schema_keys(
        (facet_key(facet) for facet in normalized),
        field_name=field_name,
    )
    return normalized


def _normalize_facet(facet: ArtifactFacetInput, *, field_name: str) -> MetadataDict:
    """Normalize one facet input through ``ArtifactFacet``."""
    if isinstance(facet, ArtifactFacet):
        return facet.to_dict()
    if isinstance(facet, Mapping):
        return ArtifactFacet.from_dict(facet, field_name=field_name).to_dict()
    raise TypeError(f"{field_name} must be an ArtifactFacet or dictionary, got {type(facet).__name__}")


def _claim_keys(
    claims: Iterable[ArtifactClaimInput],
    *,
    field_name: str,
) -> frozenset[ArtifactSchemaKey]:
    """Return normalized lookup keys for claim inputs."""
    return frozenset(claim_key(claim) for claim in _normalize_claims(claims, field_name=field_name))


def _facet_keys(
    facets: Iterable[ArtifactFacetInput],
    *,
    field_name: str,
) -> frozenset[ArtifactSchemaKey]:
    """Return normalized lookup keys for facet inputs."""
    return frozenset(facet_key(facet) for facet in _normalize_facets(facets, field_name=field_name))


def _facets_satisfy(
    requirements: Iterable[MetadataDict],
    *,
    candidates: Iterable[MetadataDict],
) -> bool:
    """Return whether candidates cover all required facet envelopes and metadata."""
    candidate_tuple = tuple(candidates)
    return all(
        any(_facet_satisfies(requirement, candidate) for candidate in candidate_tuple)
        for requirement in requirements
    )


def _facet_satisfies(requirement: MetadataDict, candidate: MetadataDict) -> bool:
    """Return whether a candidate facet satisfies one required facet."""
    if facet_key(requirement) != facet_key(candidate):
        return False
    required_metadata = requirement.get("metadata", {})
    candidate_metadata = candidate.get("metadata", {})
    if not isinstance(required_metadata, Mapping) or not isinstance(candidate_metadata, Mapping):
        return candidate_metadata == required_metadata
    return _metadata_contains(candidate_metadata, required_metadata)


def _metadata_contains(candidate: Mapping[str, object], required: Mapping[str, object]) -> bool:
    """Return whether candidate metadata contains the required metadata subset."""
    for key, required_value in required.items():
        if key not in candidate:
            return False
        if not _metadata_value_satisfies(candidate[key], required_value):
            return False
    return True


def _metadata_value_satisfies(candidate_value: object, required_value: object) -> bool:
    """Return whether one candidate metadata value satisfies one required value."""
    if isinstance(candidate_value, Mapping) and isinstance(required_value, Mapping):
        return _metadata_contains(candidate_value, required_value)
    return candidate_value == required_value


def _reject_duplicate_schema_keys(
    keys: Iterable[ArtifactSchemaKey],
    *,
    field_name: str,
) -> None:
    """Raise when a claim or facet sequence contains duplicate schema keys."""
    seen: set[ArtifactSchemaKey] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"{field_name} contains duplicate schema key: {_schema_display_name(key)}")
        seen.add(key)


def _schema_display_name(key: ArtifactSchemaKey) -> str:
    """Return a deterministic display name for a claim or facet key."""
    namespace, kind, name, version = key
    return f"{namespace}:{kind}:{name}@{version}"


def _coerce_kind(kind: CapabilityKind | str) -> CapabilityKind | str:
    """Normalize known capability kinds to ``CapabilityKind`` members."""
    kind_text = _kind_text(kind, field_name="capability.kind")
    try:
        return CapabilityKind(kind_text)
    except ValueError:
        return kind_text


def _kind_text(kind: CapabilityKind | str, *, field_name: str) -> str:
    """Return a non-empty capability kind string."""
    if isinstance(kind, CapabilityKind):
        return kind.value
    return _coerce_required_text(kind, field_name=field_name)


def _kind_value(kind: CapabilityKind | str) -> str:
    """Return the string value for a normalized capability kind."""
    if isinstance(kind, CapabilityKind):
        return kind.value
    return kind


def _coerce_required_text(value: object, *, field_name: str) -> str:
    """Coerce a required string field and reject empty values."""
    if value is None:
        raise TypeError(f"{field_name} must not be None")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value
    if not text.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return text


__all__ = [
    "AmbiguousCapabilityError",
    "ArtifactCapability",
    "CapabilityError",
    "CapabilityKind",
    "CapabilityLookupError",
    "CapabilityRegistrationError",
    "CapabilityRegistry",
    "InvalidCapabilityLookupError",
    "MissingCapabilityError",
    "UnsupportedInterfaceError",
]
