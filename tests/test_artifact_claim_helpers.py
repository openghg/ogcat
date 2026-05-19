"""Artifact claim and facet helper tests."""

from __future__ import annotations

from ogcat import (
    ArtifactDescriptor,
    ArtifactFacet,
    DataTypeClaim,
    InterfaceClaim,
    claim_key,
    facet_key,
    has_claim,
    has_facet,
    iter_claims,
    iter_facets,
)


def test_iter_claims_filters_by_kind_name_namespace_and_version() -> None:
    """Claim helpers should return normalized claims matching all supplied filters."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            InterfaceClaim("bytes"),
            DataTypeClaim("zarr", namespace="zarr.dev", evidence="inferred"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray", version="2026.4"),
        ],
    )

    assert [claim["name"] for claim in iter_claims(descriptor, kind="interface")] == [
        "bytes",
        "xarray-dataset",
    ]
    assert list(
        iter_claims(
            descriptor,
            kind="interface",
            name="xarray-dataset",
            namespace="pydata.xarray",
            version="2026.4",
        )
    ) == [
        {
            "kind": "interface",
            "name": "xarray-dataset",
            "namespace": "pydata.xarray",
            "version": "2026.4",
            "evidence": "declared",
            "confidence": "declared",
            "metadata": {},
        }
    ]


def test_has_claim_and_claim_key_normalize_raw_dicts() -> None:
    """Claim helpers should work with descriptor-normalized legacy dictionaries."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[{"kind": "representation", "name": "directory"}],
    )
    claim = next(iter_claims(descriptor))

    assert has_claim(descriptor, kind="representation", name="directory")
    assert not has_claim(descriptor, kind="interface", name="directory")
    assert claim_key(claim) == ("ogcat.core", "representation", "directory", "1")


def test_iter_facets_filters_and_keys_facets() -> None:
    """Facet helpers should return normalized facets and stable registry keys."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        facets=[
            ArtifactFacet(
                kind="collection",
                name="members",
                evidence="declared",
                metadata={"pattern": "*.nc", "member_format": "netcdf"},
            ),
            {"kind": "suffix", "name": "suffixes", "suffixes": [".zarr"]},
        ],
    )

    collection_facets = list(iter_facets(descriptor, kind="collection"))

    assert has_facet(descriptor, kind="suffix", name="suffixes")
    assert collection_facets[0]["metadata"] == {"pattern": "*.nc", "member_format": "netcdf"}
    assert facet_key(collection_facets[0]) == ("ogcat.core", "collection", "members", "1")
