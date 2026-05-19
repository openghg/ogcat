from __future__ import annotations

import pytest

from ogcat import (
    AmbiguousCapabilityError,
    ArtifactCapability,
    ArtifactDescriptor,
    ArtifactFacet,
    CapabilityKind,
    CapabilityRegistrationError,
    CapabilityRegistry,
    DataTypeClaim,
    InterfaceClaim,
    MissingCapabilityError,
    PluginRegistry,
    UnsupportedInterfaceError,
)


def test_registry_registers_lists_finds_and_selects_capabilities() -> None:
    """Registry lookups should use normalized claim and facet keys."""
    input_claim = InterfaceClaim("bytes")
    output_claim = InterfaceClaim("table")
    required_facet = ArtifactFacet("suffix", "csv")
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[input_claim],
        facets=[required_facet],
    )
    capability = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="csv-reader",
        input_claims=[input_claim],
        output_claims=[output_claim],
        required_facets=[required_facet],
        implementation=object(),
    )
    registry = CapabilityRegistry()

    registered = registry.register(capability)

    assert registered is capability
    assert registry.list() == (capability,)
    assert registry.find(
        kind="reader",
        name="csv-reader",
        namespace=capability.namespace,
        version=capability.version,
        output_claims=[output_claim],
        required_facets=[required_facet],
    ) == (capability,)
    assert (
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[input_claim],
            output_claims=[output_claim],
        )
        is capability
    )


def test_multiple_interface_claims_select_different_reader_capabilities() -> None:
    """Explicit interface claims should disambiguate reader selection."""
    bytes_claim = InterfaceClaim("bytes")
    xarray_claim = InterfaceClaim("xarray-dataset", namespace="pydata.xarray")
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[bytes_claim, xarray_claim],
    )
    bytes_reader = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="bytes-reader",
        input_claims=[bytes_claim],
    )
    xarray_reader = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="xarray-reader",
        input_claims=[xarray_claim],
    )
    registry = CapabilityRegistry([bytes_reader, xarray_reader])

    assert (
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[bytes_claim],
        )
        is bytes_reader
    )
    assert (
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[xarray_claim],
        )
        is xarray_reader
    )


def test_descriptor_lookup_uses_artifact_claims_not_record_type() -> None:
    """Capability lookup should depend on descriptor claims only."""
    claim = DataTypeClaim("netcdf", namespace="org.unidata")
    interface = InterfaceClaim("xarray-dataset", namespace="pydata.xarray")
    descriptor = ArtifactDescriptor(
        id="sidecar",
        role="auxiliary_artifact",
        claims=[claim, interface],
    )
    capability = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="netcdf-reader",
        input_claims=[claim, interface],
    )
    registry = CapabilityRegistry([capability])

    assert (
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[interface],
        )
        is capability
    )


def test_select_raises_unsupported_interface_when_descriptor_lacks_request() -> None:
    """Requested interface claims must be present on the descriptor."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[InterfaceClaim("bytes")],
    )
    registry = CapabilityRegistry(
        [
            ArtifactCapability(
                kind=CapabilityKind.READER,
                name="table-reader",
                input_claims=[InterfaceClaim("table")],
            )
        ]
    )

    with pytest.raises(UnsupportedInterfaceError, match="interface:table"):
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[InterfaceClaim("table")],
        )


def test_select_raises_missing_when_supported_request_has_no_capability() -> None:
    """Supported descriptor interfaces without matching capabilities should fail as missing."""
    table_claim = InterfaceClaim("table")
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[table_claim],
    )
    registry = CapabilityRegistry()

    with pytest.raises(MissingCapabilityError):
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[table_claim],
        )


def test_select_raises_ambiguous_with_sorted_candidate_names() -> None:
    """Multiple matching capabilities should raise deterministic candidates."""
    claim = InterfaceClaim("bytes")
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[claim],
    )
    alpha = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="alpha-reader",
        namespace="example.alpha",
        input_claims=[claim],
    )
    beta = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="beta-reader",
        namespace="example.beta",
        input_claims=[claim],
    )
    registry = CapabilityRegistry([beta, alpha])

    with pytest.raises(AmbiguousCapabilityError) as exc_info:
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=descriptor,
            input_claims=[claim],
        )

    assert exc_info.value.candidates == (
        "example.alpha:reader:alpha-reader@1",
        "example.beta:reader:beta-reader@1",
    )


def test_duplicate_and_invalid_registration_raise_registration_errors() -> None:
    """Registry registration should reject duplicate keys and invalid objects."""
    capability = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="bytes-reader",
        input_claims=[InterfaceClaim("bytes")],
    )
    registry = CapabilityRegistry([capability])

    with pytest.raises(CapabilityRegistrationError, match="already registered"):
        registry.register(
            ArtifactCapability(
                kind=CapabilityKind.READER,
                name="bytes-reader",
                input_claims=[InterfaceClaim("bytes")],
                implementation=object(),
            )
        )

    with pytest.raises(CapabilityRegistrationError, match="ArtifactCapability"):
        registry.register(object())  # type: ignore[arg-type]

    with pytest.raises(CapabilityRegistrationError, match="input interface claim"):
        CapabilityRegistry([ArtifactCapability(kind=CapabilityKind.READER, name="unusable-reader")])


def test_plugin_registry_keeps_capabilities_separate_from_hooks() -> None:
    """PluginRegistry should expose plugin-owned capability namespaces."""
    calls: list[str] = []

    class Hook:
        def before_commit(self, context: object) -> None:
            calls.append("before_commit")

    claim = InterfaceClaim("custom-table", namespace="example.plugin")
    capability = ArtifactCapability(
        kind=CapabilityKind.READER,
        name="custom-reader",
        namespace="example.plugin",
        input_claims=[claim],
    )
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[claim],
    )
    plugins = PluginRegistry([Hook()])

    assert plugins.register_capability(capability) is capability

    assert len(plugins.hooks) == 1
    assert calls == []
    assert plugins.list_capabilities() == (capability,)
    assert (
        plugins.capability_registry().select(
            kind=CapabilityKind.READER,
            namespace="example.plugin",
            descriptor=descriptor,
            input_claims=[claim],
        )
        is capability
    )
