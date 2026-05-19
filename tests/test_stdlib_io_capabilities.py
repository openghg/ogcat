"""Tests for bundled stdlib plugin capability examples."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ogcat.artifact_claims import has_claim, has_facet
from ogcat.bundled_plugins.stdlib_io import (
    delimited_table_artifact_descriptor,
    json_artifact_descriptor,
    register_stdlib_capabilities,
    temporary_text_artifact_descriptor,
    text_artifact_descriptor,
)
from ogcat.capabilities import MissingCapabilityError
from ogcat.models import ArtifactDescriptor, ArtifactLocator, DataTypeClaim, InterfaceClaim
from ogcat.plugins import PluginRegistry


def test_bytes_reader_and_writer_examples_round_trip_bytes(tmp_path: Path) -> None:
    """Bytes capabilities should use Path.read_bytes and Path.write_bytes."""
    path = tmp_path / "payload.bin"
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[InterfaceClaim("bytes")],
    )
    registry = _registry()

    writer = _implementation(_select(registry, descriptor, operation="write", interface="bytes"))
    result = writer.write(descriptor, b"abc\x00def")
    reader = _implementation(_select(registry, result, operation="read", interface="bytes"))

    assert path.read_bytes() == b"abc\x00def"
    assert reader.read(result) == b"abc\x00def"


def test_text_reader_and_writer_examples_use_encoding_facets(tmp_path: Path) -> None:
    """Text capabilities should honor ASCII and UTF-8 encoding facets."""
    registry = _registry()
    ascii_descriptor = text_artifact_descriptor(tmp_path / "ascii.txt", encoding="ascii")
    utf8_descriptor = text_artifact_descriptor(tmp_path / "utf8.txt", encoding="utf-8")

    writer = _implementation(_select(registry, ascii_descriptor, operation="write", interface="text"))
    ascii_result = writer.write(ascii_descriptor, "plain ascii")
    utf8_result = writer.write(utf8_descriptor, "hello 🙂")
    reader = _implementation(_select(registry, utf8_result, operation="read", interface="text"))

    assert (tmp_path / "ascii.txt").read_bytes() == b"plain ascii"
    assert reader.read(ascii_result) == "plain ascii"
    assert reader.read(utf8_result) == "hello 🙂"
    assert has_facet(ascii_result, kind="encoding", name="charset", namespace="ogcat.stdlib")
    assert has_facet(utf8_result, kind="encoding", name="charset", namespace="ogcat.stdlib")


def test_delimited_table_reader_and_writer_examples_support_csv_and_options(
    tmp_path: Path,
) -> None:
    """Delimited table capabilities should support CSV and alternate delimiters."""
    registry = _registry()
    csv_descriptor = delimited_table_artifact_descriptor(tmp_path / "table.csv")
    pipe_descriptor = delimited_table_artifact_descriptor(tmp_path / "table.psv", delimiter="|")
    rows = [{"station": "mhd", "value": "410.2"}, {"station": "tac", "value": "419.5"}]

    writer = _implementation(_select(registry, csv_descriptor, operation="write", interface="table"))
    csv_result = writer.write(csv_descriptor, rows)
    pipe_result = writer.write(pipe_descriptor, rows, delimiter="|")
    reader = _implementation(_select(registry, csv_result, operation="read", interface="table"))

    assert reader.read(csv_result) == rows
    assert reader.read(pipe_result) == rows
    assert (tmp_path / "table.psv").read_text(encoding="utf-8").splitlines()[0] == "station|value"


def test_json_reader_and_writer_examples_round_trip_documents(tmp_path: Path) -> None:
    """JSON capabilities should use json.dump and json.load."""
    registry = _registry()
    descriptor = json_artifact_descriptor(tmp_path / "document.json")
    document = {"name": "example", "values": [1, 2, 3], "unicode": "🙂"}

    writer = _implementation(_select(registry, descriptor, operation="write", interface="json"))
    result = writer.write(descriptor, document)
    reader = _implementation(_select(registry, result, operation="read", interface="json"))

    assert reader.read(result) == document
    assert has_claim(result, kind="data_type", name="json", namespace="iana.media-types")


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
    result = converter.convert(source, output)
    reader = _implementation(_select(registry, result, operation="read", interface="text"))

    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "done 🙂"
    assert reader.read(result) == "done 🙂"
    assert result.relationship["catalogued"] is False
    assert result.relationship["generated_by"] == "ogcat.stdlib.emoticon_to_emoji"
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
    result = converter.convert(source, output)
    reader = _implementation(_select(registry, result, operation="read", interface="text"))

    assert reader.read(result) == "ellohay appleway skyay"
    assert result.relationship["generated_by"] == "ogcat.stdlib.pig_latin"
    assert has_claim(result, kind="interface", name="text")
    assert has_facet(result, kind="encoding", name="charset", namespace="ogcat.stdlib")


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
    result = converter.convert(source, output)
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


def test_temporary_artifact_descriptor_can_be_used_for_selection(tmp_path: Path) -> None:
    """A non-catalog descriptor should still participate in registry selection."""
    descriptor = temporary_text_artifact_descriptor(tmp_path / "scratch.txt")
    registry = _registry()

    selected = _select(registry, descriptor, operation="write", interface="text")
    writer = _implementation(selected)
    result = writer.write(descriptor, "scratch")

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


def _registry() -> Any:
    """Return a registry populated with bundled stdlib capabilities."""
    capability_module = pytest.importorskip("ogcat.capabilities")
    return register_stdlib_capabilities(capability_module.CapabilityRegistry())


def _select(
    registry: Any,
    descriptor: ArtifactDescriptor,
    *,
    operation: str,
    interface: str,
    name: str | None = None,
) -> object:
    """Select a capability across the small expected registry API variants."""
    select = registry.select
    capability_kind = {"read": "reader", "write": "writer", "convert": "converter"}[operation]
    capability_name = name or {("write", "text"): "text-writer"}.get((operation, interface))
    input_claims = [InterfaceClaim(interface)] if operation in {"read", "convert"} else []
    output_claims = [InterfaceClaim(interface)] if operation in {"write", "convert"} else []
    attempts = (
        lambda: select(
            kind=capability_kind,
            name=capability_name,
            descriptor=descriptor,
            input_claims=input_claims,
            output_claims=output_claims,
        ),
        lambda: select(kind=capability_kind, descriptor=descriptor),
        lambda: select(descriptor, operation=operation, interface=interface),
        lambda: select(descriptor, kind=capability_kind, interface=interface),
        lambda: select(operation, descriptor, interface=interface),
        lambda: select(operation=operation, descriptor=descriptor, interface=interface),
    )
    errors: list[Exception] = []
    for attempt in attempts:
        try:
            selected = attempt()
        except TypeError as exc:
            errors.append(exc)
            continue
        if selected is not None:
            return selected

    find = getattr(registry, "find", None)
    if find is not None:
        for kwargs in (
            {
                "kind": capability_kind,
                "name": capability_name,
                "descriptor": descriptor,
                "input_claims": input_claims,
                "output_claims": output_claims,
            },
            {"kind": capability_kind, "descriptor": descriptor},
            {"operation": operation, "interface": interface},
            {"kind": capability_kind, "interface": interface},
        ):
            try:
                matches = list(find(**kwargs))
            except TypeError:
                continue
            if matches:
                return matches[0]
    raise AssertionError(f"no capability selected for {operation}/{interface}: {errors}")


def _implementation(capability: object) -> Any:
    """Return an opaque implementation object from a selected capability."""
    return getattr(capability, "implementation", getattr(capability, "handler", capability))
