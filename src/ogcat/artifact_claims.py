"""Helpers for querying artifact claims and facets."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TypeAlias

from ogcat.models import ArtifactClaim, ArtifactDescriptor, ArtifactFacet, JsonValue, MetadataDict

ArtifactSchemaKey: TypeAlias = tuple[str, str, str, str]


def iter_claims(
    descriptor: ArtifactDescriptor,
    *,
    kind: str | None = None,
    name: str | None = None,
    namespace: str | None = None,
    version: str | None = None,
) -> Iterator[MetadataDict]:
    """Yield normalized claims from a descriptor that match optional filters.

    Args:
        descriptor: Artifact descriptor to inspect.
        kind: Optional claim kind filter.
        name: Optional claim name filter.
        namespace: Optional claim namespace filter.
        version: Optional claim version filter.

    Yields:
        Normalized JSON-compatible claim dictionaries.
    """
    for claim in descriptor.claims:
        normalized = _normalize_claim(claim)
        if _matches_envelope(
            normalized,
            kind=kind,
            name=name,
            namespace=namespace,
            version=version,
        ):
            yield normalized


def has_claim(
    descriptor: ArtifactDescriptor,
    *,
    kind: str | None = None,
    name: str | None = None,
    namespace: str | None = None,
    version: str | None = None,
) -> bool:
    """Return whether a descriptor has at least one matching claim."""
    return (
        next(
            iter_claims(
                descriptor,
                kind=kind,
                name=name,
                namespace=namespace,
                version=version,
            ),
            None,
        )
        is not None
    )


def claim_key(claim: ArtifactClaim | Mapping[str, object]) -> ArtifactSchemaKey:
    """Return the stable lookup key for a claim.

    The key order is ``(namespace, kind, name, version)`` so registry lookup can
    group by owner namespace before interpreting the namespace-local name.
    """
    normalized = _normalize_claim(claim)
    return _schema_key(normalized)


def iter_facets(
    descriptor: ArtifactDescriptor,
    *,
    kind: str | None = None,
    name: str | None = None,
    namespace: str | None = None,
    version: str | None = None,
) -> Iterator[MetadataDict]:
    """Yield normalized facets from a descriptor that match optional filters.

    Args:
        descriptor: Artifact descriptor to inspect.
        kind: Optional facet kind filter.
        name: Optional facet name filter.
        namespace: Optional facet namespace filter.
        version: Optional facet version filter.

    Yields:
        Normalized JSON-compatible facet dictionaries.
    """
    for facet in descriptor.facets:
        normalized = _normalize_facet(facet)
        if _matches_envelope(
            normalized,
            kind=kind,
            name=name,
            namespace=namespace,
            version=version,
        ):
            yield normalized


def has_facet(
    descriptor: ArtifactDescriptor,
    *,
    kind: str | None = None,
    name: str | None = None,
    namespace: str | None = None,
    version: str | None = None,
) -> bool:
    """Return whether a descriptor has at least one matching facet."""
    return (
        next(
            iter_facets(
                descriptor,
                kind=kind,
                name=name,
                namespace=namespace,
                version=version,
            ),
            None,
        )
        is not None
    )


def facet_key(facet: ArtifactFacet | Mapping[str, object]) -> ArtifactSchemaKey:
    """Return the stable lookup key for a facet.

    The key order is ``(namespace, kind, name, version)`` so callers can group
    facts by owner namespace before interpreting namespace-local names.
    """
    normalized = _normalize_facet(facet)
    return _schema_key(normalized)


def _normalize_claim(claim: ArtifactClaim | Mapping[str, object]) -> MetadataDict:
    """Return one claim as a normalized JSON-compatible dictionary."""
    if isinstance(claim, ArtifactClaim):
        return claim.to_dict()
    return ArtifactClaim.from_dict(claim).to_dict()


def _normalize_facet(facet: ArtifactFacet | Mapping[str, object]) -> MetadataDict:
    """Return one facet as a normalized JSON-compatible dictionary."""
    if isinstance(facet, ArtifactFacet):
        return facet.to_dict()
    return ArtifactFacet.from_dict(facet).to_dict()


def _matches_envelope(
    item: Mapping[str, JsonValue],
    *,
    kind: str | None,
    name: str | None,
    namespace: str | None,
    version: str | None,
) -> bool:
    """Return whether a normalized claim or facet envelope matches filters."""
    return (
        (kind is None or item["kind"] == kind)
        and (name is None or item["name"] == name)
        and (namespace is None or item["namespace"] == namespace)
        and (version is None or item["version"] == version)
    )


def _schema_key(item: Mapping[str, JsonValue]) -> ArtifactSchemaKey:
    """Return a lookup key from a normalized claim or facet envelope."""
    return (
        str(item["namespace"]),
        str(item["kind"]),
        str(item["name"]),
        str(item["version"]),
    )
