"""Tests for bundled stdlib plugin capability examples."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ogcat.artifact_claims import has_claim, has_facet
from ogcat.bundled_plugins.stdlib_io import (
    DEFAULT_TEXT_ENCODING,
    bytes_artifact_descriptor,
    delimited_table_artifact_descriptor,
    delimiter_from_descriptor,
    encoding_facet,
    encoding_from_descriptor,
    json_artifact_descriptor,
    register_stdlib_capabilities,
    stdlib_capabilities,
    temporary_text_artifact_descriptor,
    text_artifact_descriptor,
)
from ogcat.capabilities import ArtifactCapability, CapabilityKind, CapabilityRegistry, MissingCapabilityError
from ogcat.models import (
    ArtifactDescriptor,
    ArtifactFacet,
    ArtifactLocator,
    ArtifactWriteResult,
    DataTypeClaim,
    InterfaceClaim,
)
from ogcat.plugins import PluginRegistry


def test_bytes_reader_and_writer_examples_round_trip_bytes(tmp_path: Path) -> None:
    """Bytes capabilities should use Path.read_bytes and Path.write_bytes."""
    path = tmp_path / "payload.bin"
    descriptor = bytes_artifact_descriptor(path)
    registry = _registry()

    writer = _implementation(_select(registry, descriptor, operation="write", interface="bytes"))
    write_result = writer.write(descriptor, b"abc\x00def")
    result = write_result.artifact
    reader = _implementation(_select(registry, result, operation="read", interface="bytes"))

    assert isinstance(write_result, ArtifactWriteResult)
    assert path.read_bytes() == b"abc\x00def"
    assert reader.read(result) == b"abc\x00def"
    assert has_facet(result, kind="locator", name="path", namespace="ogcat.stdlib")


def test_text_reader_and_writer_examples_use_encoding_facets(tmp_path: Path) -> None:
    """Text capabilities should honor ASCII and UTF-8 encoding facets."""
    registry = _registry()
    ascii_descriptor = text_artifact_descriptor(tmp_path / "ascii.txt", encoding="ascii")
    utf8_descriptor = text_artifact_descriptor(tmp_path / "utf8.txt", encoding="utf-8")
    override_descriptor = text_artifact_descriptor(tmp_path / "override.txt", encoding="ascii")

    writer = _implementation(_select(registry, ascii_descriptor, operation="write", interface="text"))
    ascii_result = writer.write(ascii_descriptor, "plain ascii").artifact
    utf8_result = writer.write(utf8_descriptor, "hello 🙂").artifact
    override_result = writer.write(override_descriptor, "hello 🙂", encoding="utf-8").artifact
    reader = _implementation(_select(registry, utf8_result, operation="read", interface="text"))

    assert (tmp_path / "ascii.txt").read_bytes() == b"plain ascii"
    assert reader.read(ascii_result) == "plain ascii"
    assert reader.read(utf8_result) == "hello 🙂"
    assert reader.read(override_result) == "hello 🙂"
    assert encoding_from_descriptor(override_result) == "utf-8"
    assert has_facet(ascii_result, kind="locator", name="path", namespace="ogcat.stdlib")
    assert has_facet(ascii_result, kind="encoding", name="charset", namespace="ogcat.stdlib")
    assert has_facet(utf8_result, kind="encoding", name="charset", namespace="ogcat.stdlib")


def test_delimited_table_reader_and_writer_examples_support_csv_and_options(
    tmp_path: Path,
) -> None:
    """Delimited table capabilities should support CSV and alternate delimiters."""
    registry = _registry()
    csv_descriptor = delimited_table_artifact_descriptor(tmp_path / "table.csv")
    pipe_descriptor = delimited_table_artifact_descriptor(tmp_path / "table.psv", delimiter="|")
    override_descriptor = delimited_table_artifact_descriptor(tmp_path / "override.psv")
    rows = [{"station": "mhd", "value": "410.2"}, {"station": "tac", "value": "419.5"}]

    writer = _implementation(_select(registry, csv_descriptor, operation="write", interface="table"))
    csv_result = writer.write(csv_descriptor, rows).artifact
    pipe_result = writer.write(pipe_descriptor, rows, delimiter="|").artifact
    override_result = writer.write(override_descriptor, rows, delimiter="|").artifact
    reader = _implementation(_select(registry, csv_result, operation="read", interface="table"))
    pipe_reader = _implementation(_select(registry, pipe_result, operation="read", interface="table"))
    override_reader = _implementation(_select(registry, override_result, operation="read", interface="table"))

    assert reader.read(csv_result) == rows
    assert pipe_reader.read(pipe_result) == rows
    assert override_reader.read(override_result) == rows
    assert delimiter_from_descriptor(override_result) == "|"
    assert has_facet(csv_result, kind="locator", name="path", namespace="ogcat.stdlib")
    assert (tmp_path / "table.psv").read_text(encoding="utf-8").splitlines()[0] == "station|value"


def test_delimited_table_reader_rejects_extra_columns_and_normalizes_missing_values(
    tmp_path: Path,
) -> None:
    """Delimited table rows should keep the advertised dict[str, str] shape."""
    registry = _registry()
    reader = _implementation(
        _select(
            registry,
            delimited_table_artifact_descriptor(tmp_path / "valid.csv"),
            operation="read",
            interface="table",
        )
    )
    missing_descriptor = delimited_table_artifact_descriptor(tmp_path / "missing.csv")
    missing_path = missing_descriptor.locator.as_path() if missing_descriptor.locator is not None else None
    assert missing_path is not None
    missing_path.write_text("name,value\nalpha\n", encoding="utf-8")

    extra_descriptor = delimited_table_artifact_descriptor(tmp_path / "extra.csv")
    extra_path = extra_descriptor.locator.as_path() if extra_descriptor.locator is not None else None
    assert extra_path is not None
    extra_path.write_text("name,value\nalpha,1,unexpected\n", encoding="utf-8")

    assert reader.read(missing_descriptor) == [{"name": "alpha", "value": ""}]
    with pytest.raises(ValueError, match="more columns"):
        reader.read(extra_descriptor)


def test_json_reader_and_writer_examples_round_trip_documents(tmp_path: Path) -> None:
    """JSON capabilities should use json.dump and json.load."""
    registry = _registry()
    descriptor = json_artifact_descriptor(tmp_path / "document.json")
    document = {"name": "example", "values": [1, 2, 3], "unicode": "🙂"}

    writer = _implementation(_select(registry, descriptor, operation="write", interface="json"))
    result = writer.write(descriptor, document).artifact
    reader = _implementation(_select(registry, result, operation="read", interface="json"))

    assert reader.read(result) == document
    assert has_claim(result, kind="data_type", name="json", namespace="iana.media-types")


def test_delimited_table_to_json_converter_composes_reader_and_writer(tmp_path: Path) -> None:
    """A CSV table can be read through table claims and written as JSON."""
    registry = _registry()
    source = delimited_table_artifact_descriptor(tmp_path / "input.csv")
    source_path = source.locator.as_path() if source.locator is not None else None
    assert source_path is not None
    source_path.write_text("station,value\nmhd,410.2\ntac,419.5\n", encoding="utf-8")
    output = json_artifact_descriptor(tmp_path / "output.json", id="json-output")

    converter = _implementation(
        registry.select(
            kind=CapabilityKind.CONVERTER,
            name="delimited-table-to-json-converter",
            descriptor=source,
            input_claims=[InterfaceClaim("table")],
            output_claims=[InterfaceClaim("json")],
        )
    )
    source_reader = _implementation(_select(registry, source, operation="read", interface="table"))
    json_writer = _implementation(_select(registry, output, operation="write", interface="json"))
    json_document = converter.convert(source_reader.read(source))
    write_result = json_writer.write(
        output,
        json_document,
        encoding=encoding_from_descriptor(output),
    )
    result = write_result.artifact
    reader = _implementation(_select(registry, result, operation="read", interface="json"))

    assert isinstance(write_result, ArtifactWriteResult)
    assert reader.read(result) == [
        {"station": "mhd", "value": "410.2"},
        {"station": "tac", "value": "419.5"},
    ]
    assert has_claim(result, kind="data_type", name="json", namespace="iana.media-types")
    assert has_claim(result, kind="interface", name="json")
    assert has_facet(result, kind="locator", name="path", namespace="ogcat.stdlib")


def test_emoticon_to_emoji_converter_writes_utf8_selectable_text(tmp_path: Path) -> None:
    """The converter should turn ASCII emoticons into UTF-8 emoji text metadata."""
    registry = _registry()
    source = text_artifact_descriptor(tmp_path / "source.txt", encoding="ascii")
    source_path = source.locator.as_path() if source.locator is not None else None
    assert source_path is not None
    source_path.write_text("done :)", encoding="ascii")
    output = temporary_text_artifact_descriptor(tmp_path / "output.txt")

    converter = _implementation(
        _select(
            registry,
            source,
            operation="convert",
            interface="text",
            name="emoticon-to-emoji-text-converter",
        )
    )
    source_reader = _implementation(_select(registry, source, operation="read", interface="text"))
    writer = _implementation(_select(registry, output, operation="write", interface="text"))
    converted = converter.convert(source_reader.read(source))
    result = writer.write(output, converted, encoding=DEFAULT_TEXT_ENCODING).artifact
    reader = _implementation(_select(registry, result, operation="read", interface="text"))

    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "done 🙂"
    assert reader.read(result) == "done 🙂"
    assert result.relationship["catalogued"] is False
    assert has_claim(result, kind="interface", name="text")
    assert has_facet(result, kind="encoding", name="charset", namespace="ogcat.stdlib")


def test_pig_latin_converter_writes_utf8_selectable_text(tmp_path: Path) -> None:
    """The Pig Latin converter should produce normal selectable text output."""
    registry = _registry()
    source = text_artifact_descriptor(tmp_path / "plain.txt", encoding="utf-8")
    source_path = source.locator.as_path() if source.locator is not None else None
    assert source_path is not None
    source_path.write_text("hello apple sky", encoding="utf-8")
    output = temporary_text_artifact_descriptor(tmp_path / "pig.txt")

    converter = _implementation(
        _select(
            registry,
            source,
            operation="convert",
            interface="text",
            name="pig-latin-text-converter",
        )
    )
    source_reader = _implementation(_select(registry, source, operation="read", interface="text"))
    writer = _implementation(_select(registry, output, operation="write", interface="text"))
    converted = converter.convert(source_reader.read(source))
    result = writer.write(output, converted, encoding=DEFAULT_TEXT_ENCODING).artifact
    reader = _implementation(_select(registry, result, operation="read", interface="text"))

    assert reader.read(result) == "ellohay appleway skyay"
    assert has_claim(result, kind="interface", name="text")
    assert has_facet(result, kind="encoding", name="charset", namespace="ogcat.stdlib")


def test_text_converters_return_composable_runtime_values() -> None:
    """Text converters should compose before a writer sinks the final value."""
    registry = _registry()
    emoji_converter = _implementation(
        registry.select(
            kind=CapabilityKind.CONVERTER,
            name="emoticon-to-emoji-text-converter",
            input_claims=[InterfaceClaim("text")],
            output_claims=[InterfaceClaim("text")],
        )
    )
    pig_latin_converter = _implementation(
        registry.select(
            kind=CapabilityKind.CONVERTER,
            name="pig-latin-text-converter",
            input_claims=[InterfaceClaim("text")],
            output_claims=[InterfaceClaim("text")],
        )
    )

    converted = pig_latin_converter.convert(emoji_converter.convert("hello :)"))

    assert converted == "ellohay 🙂"


def test_pig_latin_can_filter_csv_text_but_not_claim_table_output(tmp_path: Path) -> None:
    """CSV can be treated as text, but Pig Latin does not produce CSV/table output."""
    registry = _registry()
    source = delimited_table_artifact_descriptor(tmp_path / "input.csv")
    source_path = source.locator.as_path() if source.locator is not None else None
    assert source_path is not None
    source_path.write_text("name,value\nhello,world\n", encoding="utf-8")
    output = temporary_text_artifact_descriptor(tmp_path / "pig.csv.txt")

    converter = _implementation(
        _select(
            registry,
            source,
            operation="convert",
            interface="text",
            name="pig-latin-text-converter",
        )
    )
    source_reader = _implementation(_select(registry, source, operation="read", interface="text"))
    writer = _implementation(_select(registry, output, operation="write", interface="text"))
    converted = converter.convert(source_reader.read(source))
    result = writer.write(output, converted, encoding=DEFAULT_TEXT_ENCODING).artifact
    reader = _implementation(_select(registry, result, operation="read", interface="text"))

    assert reader.read(result) == "amenay,aluevay\nellohay,orldway\n"
    for unsupported_output in (
        InterfaceClaim("table"),
        DataTypeClaim("csv", namespace="iana.media-types"),
    ):
        with pytest.raises(MissingCapabilityError):
            registry.select(
                kind="converter",
                name="pig-latin-text-converter",
                descriptor=source,
                input_claims=[InterfaceClaim("text")],
                output_claims=[unsupported_output],
            )


def test_csv_artifact_can_be_selected_as_bytes_text_or_table(tmp_path: Path) -> None:
    """One CSV descriptor should be readable through bytes, text, and table interfaces."""
    path = tmp_path / "multi.csv"
    path.write_text("name,value\nalpha,1\n", encoding="utf-8")
    descriptor = delimited_table_artifact_descriptor(path)
    registry = _registry()

    bytes_reader = _implementation(_select(registry, descriptor, operation="read", interface="bytes"))
    text_reader = _implementation(_select(registry, descriptor, operation="read", interface="text"))
    table_reader = _implementation(_select(registry, descriptor, operation="read", interface="table"))

    assert bytes_reader.read(descriptor) == b"name,value\nalpha,1\n"
    assert text_reader.read(descriptor) == "name,value\nalpha,1\n"
    assert table_reader.read(descriptor) == [{"name": "alpha", "value": "1"}]


def test_stdlib_runtime_helpers_ignore_same_name_facets_from_other_namespaces(tmp_path: Path) -> None:
    """Runtime helpers should consume the same namespaced facets selection used."""
    registry = _registry()
    text_descriptor = text_artifact_descriptor(tmp_path / "utf8.txt", encoding="utf-8")
    text_descriptor.facets = [
        ArtifactFacet(
            "encoding",
            "charset",
            namespace="example.plugin",
            metadata={"encoding": "ascii"},
        ),
        *text_descriptor.facets,
    ]
    text_path = text_descriptor.locator.as_path() if text_descriptor.locator is not None else None
    assert text_path is not None
    text_path.write_text("hello 🙂", encoding="utf-8")

    table_descriptor = delimited_table_artifact_descriptor(tmp_path / "table.csv", delimiter=",")
    table_descriptor.facets = [
        ArtifactFacet(
            "format",
            "delimited-text",
            namespace="example.plugin",
            metadata={"delimiter": "|"},
        ),
        *table_descriptor.facets,
    ]
    table_path = table_descriptor.locator.as_path() if table_descriptor.locator is not None else None
    assert table_path is not None
    table_path.write_text("name,value\nhello,world\n", encoding="utf-8")

    text_reader = _implementation(_select(registry, text_descriptor, operation="read", interface="text"))
    table_reader = _implementation(_select(registry, table_descriptor, operation="read", interface="table"))

    assert encoding_from_descriptor(text_descriptor) == "utf-8"
    assert delimiter_from_descriptor(table_descriptor) == ","
    assert text_reader.read(text_descriptor) == "hello 🙂"
    assert table_reader.read(table_descriptor) == [{"name": "hello", "value": "world"}]


def test_non_path_descriptors_do_not_select_stdlib_path_backed_capabilities() -> None:
    """Stdlib capabilities should fail selection when the path facet is absent."""
    registry = _registry()
    remote_text = ArtifactDescriptor(
        id="remote-text",
        role="data_artifact",
        locator=ArtifactLocator.from_urlpath("s3://example/input.txt"),
        claims=[InterfaceClaim("text")],
        facets=[encoding_facet("utf-8")],
    )
    remote_output = ArtifactDescriptor(
        id="remote-output",
        role="derived_artifact",
        locator=ArtifactLocator.from_urlpath("s3://example/output.txt"),
        claims=[InterfaceClaim("text")],
        facets=[encoding_facet("utf-8")],
    )

    with pytest.raises(MissingCapabilityError):
        registry.select(
            kind=CapabilityKind.READER,
            descriptor=remote_text,
            input_claims=[InterfaceClaim("text")],
        )

    with pytest.raises(MissingCapabilityError):
        registry.select(
            kind=CapabilityKind.WRITER,
            name="text-writer",
            descriptor=remote_output,
            output_claims=[InterfaceClaim("text")],
        )


def test_temporary_artifact_descriptor_can_be_used_for_selection(tmp_path: Path) -> None:
    """A non-catalog descriptor should still participate in registry selection."""
    descriptor = temporary_text_artifact_descriptor(tmp_path / "scratch.txt")
    registry = _registry()

    selected = _select(registry, descriptor, operation="write", interface="text")
    writer = _implementation(selected)
    result = writer.write(descriptor, "scratch").artifact

    assert descriptor.relationship == {"catalogued": False, "temporary": True}
    result_path = result.locator.as_path() if result.locator is not None else None
    assert result_path is not None
    assert result_path.read_text(encoding="utf-8") == "scratch"


def test_stdlib_capabilities_register_through_plugin_registry(tmp_path: Path) -> None:
    """Bundled examples should register like external plugin capabilities."""
    plugins = register_stdlib_capabilities(PluginRegistry())
    descriptor = text_artifact_descriptor(tmp_path / "plugin-text.txt")

    selected = plugins.capability_registry().select(
        kind="reader",
        descriptor=descriptor,
        input_claims=[InterfaceClaim("text")],
    )

    assert selected.name == "text-reader"
    assert plugins.hooks == ()


def test_stdlib_capabilities_use_core_capability_model() -> None:
    """Bundled capability examples should use the core capability model directly."""
    capabilities = stdlib_capabilities()

    assert all(isinstance(capability, ArtifactCapability) for capability in capabilities)
    assert {capability.kind for capability in capabilities} == {
        CapabilityKind.READER,
        CapabilityKind.WRITER,
        CapabilityKind.CONVERTER,
    }
    assert all(capability.namespace == "ogcat.stdlib" for capability in capabilities)


def test_stdlib_registration_prefers_capability_method() -> None:
    """Registration should call register_capability when a registry exposes it."""

    class CapabilityOnlyRegistry:
        """Minimal registry exposing only the capability registration method."""

        def __init__(self) -> None:
            self.capabilities: list[ArtifactCapability] = []

        def register_capability(self, capability: ArtifactCapability) -> ArtifactCapability:
            """Record the registered capability."""
            self.capabilities.append(capability)
            return capability

    registry = CapabilityOnlyRegistry()

    assert register_stdlib_capabilities(registry) is registry
    assert {capability.name for capability in registry.capabilities} == {
        capability.name for capability in stdlib_capabilities()
    }


def _registry() -> CapabilityRegistry:
    """Return a registry populated with bundled stdlib capabilities."""
    return register_stdlib_capabilities(CapabilityRegistry())


def _select(
    registry: CapabilityRegistry,
    descriptor: ArtifactDescriptor,
    *,
    operation: str,
    interface: str,
    name: str | None = None,
) -> ArtifactCapability:
    """Select a stdlib capability through the core registry API.

    Args:
        registry: Registry populated with stdlib capabilities.
        descriptor: Descriptor used as the source for reads/converts or the
            desired output shape for writes.
        operation: One of ``read``, ``write``, or ``convert``.
        interface: Interface claim name requested for the operation.
        name: Optional capability name used to disambiguate converters.

    Returns:
        Selected stdlib capability.
    """
    capability_kind = {
        "read": CapabilityKind.READER,
        "write": CapabilityKind.WRITER,
        "convert": CapabilityKind.CONVERTER,
    }[operation]
    capability_name = name or {("write", "text"): "text-writer"}.get((operation, interface))
    input_claims = [InterfaceClaim(interface)] if operation in {"read", "convert"} else []
    output_claims = [InterfaceClaim(interface)] if operation in {"write", "convert"} else []
    return registry.select(
        kind=capability_kind,
        name=capability_name,
        descriptor=descriptor,
        input_claims=input_claims,
        output_claims=output_claims,
    )


def _implementation(capability: ArtifactCapability) -> Any:
    """Return an opaque implementation object from a selected capability."""
    assert capability.implementation is not None
    return capability.implementation
