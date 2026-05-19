"""Dependency-free stdlib reader, writer, and converter examples.

This bundled module is intentionally shaped like a plugin: it declares
``ArtifactCapability`` objects, stores opaque implementation instances on those
declarations, and registers through the same capability registry route that an
external package would use. The implementations are small stdlib examples for
tests and documentation, not privileged core behavior.

All implementations in this module are local-path backed. Their declarations
therefore require the stdlib ``locator/path`` facet in addition to interface and
format claims. Text, table, and JSON examples also require stdlib-owned facets
for encoding and delimiter information. Runtime helpers consume only facets from
this module's namespace/version so plugin-owned facts with the same kind/name do
not silently alter stdlib behavior.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast

from ogcat.artifact_claims import iter_facets
from ogcat.capabilities import ArtifactCapability, CapabilityKind
from ogcat.models import (
    ArtifactClaim,
    ArtifactClaimInput,
    ArtifactDescriptor,
    ArtifactFacet,
    ArtifactFacetInput,
    ArtifactLocator,
    ArtifactWriteResult,
    DataTypeClaim,
    InterfaceClaim,
    JsonValue,
    MetadataDict,
    RepresentationClaim,
)

PLUGIN_NAMESPACE = "ogcat.stdlib"
SCHEMA_VERSION = "1"
DEFAULT_TEXT_ENCODING = "utf-8"
_EXTRA_COLUMNS_KEY = "__ogcat_extra_columns__"

JsonDocument: TypeAlias = JsonValue
TableRows: TypeAlias = list[dict[str, str]]
_WORD_RE = re.compile(r"[A-Za-z]+")
_VOWELS = frozenset("aeiouAEIOU")


class BytesReader:
    """Read path-backed artifacts as raw bytes."""

    def read(self, descriptor: ArtifactDescriptor) -> bytes:
        """Read bytes from the descriptor's path locator.

        Args:
            descriptor: Path-backed artifact descriptor to read.

        Returns:
            Raw bytes stored at the descriptor path.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be read.
        """
        return _descriptor_path(descriptor).read_bytes()


class BytesWriter:
    """Write path-backed artifacts as raw bytes."""

    def write(self, descriptor: ArtifactDescriptor, data: bytes) -> ArtifactWriteResult:
        """Write bytes to the descriptor's path locator.

        Args:
            descriptor: Path-backed output descriptor.
            data: Bytes to write.

        Returns:
            Result containing descriptor metadata with bytes/file claims and
            path locator facet.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be written.
        """
        _descriptor_path(descriptor).write_bytes(data)
        return ArtifactWriteResult.from_artifact(
            descriptor_with_claims(
                descriptor,
                claims=(InterfaceClaim("bytes"), RepresentationClaim("file")),
                facets=(path_locator_facet(),),
            )
        )


class TextReader:
    """Read path-backed artifacts as text using descriptor encoding facets."""

    def read(self, descriptor: ArtifactDescriptor) -> str:
        """Read text from the descriptor's path locator.

        Args:
            descriptor: Path-backed descriptor with a stdlib encoding facet.

        Returns:
            Decoded text content.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be read.
            UnicodeError: If the file cannot be decoded with the declared
                encoding.
        """
        return _descriptor_path(descriptor).read_text(
            encoding=encoding_from_descriptor(descriptor),
        )


class TextWriter:
    """Write path-backed artifacts as text."""

    def write(
        self,
        descriptor: ArtifactDescriptor,
        text: str,
        *,
        encoding: str | None = None,
    ) -> ArtifactWriteResult:
        """Write text and return descriptor metadata with an encoding facet.

        Args:
            descriptor: Path-backed output descriptor.
            text: Text to write.
            encoding: Optional encoding override. When omitted, the descriptor's
                stdlib encoding facet is used, falling back to UTF-8.

        Returns:
            Result containing descriptor metadata with text claims, path
            locator facet, and the resolved encoding facet.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be written.
            UnicodeError: If the text cannot be encoded with the resolved
                encoding.
        """
        resolved_encoding = encoding or encoding_from_descriptor(descriptor)
        _descriptor_path(descriptor).write_text(text, encoding=resolved_encoding)
        return ArtifactWriteResult.from_artifact(
            descriptor_with_claims(
                descriptor,
                claims=(RepresentationClaim("text"), InterfaceClaim("text")),
                facets=(path_locator_facet(), encoding_facet(resolved_encoding)),
            )
        )


class DelimitedTableReader:
    """Read delimited text artifacts as rows of dictionaries."""

    def read(
        self,
        descriptor: ArtifactDescriptor,
        *,
        delimiter: str | None = None,
    ) -> TableRows:
        """Read a delimited table using ``csv.DictReader``.

        Args:
            descriptor: Path-backed descriptor with stdlib encoding and
                delimiter facets.
            delimiter: Optional delimiter override. When omitted, the descriptor
                delimiter facet is used, falling back to a comma.

        Returns:
            Rows as dictionaries with string keys and string values.

        Raises:
            ValueError: If the descriptor is not path-backed or if a row has
                extra columns without headers.
            OSError: If the path cannot be read.
            UnicodeError: If the file cannot be decoded with the declared
                encoding.
        """
        resolved_delimiter = delimiter or delimiter_from_descriptor(descriptor)
        with _descriptor_path(descriptor).open(
            newline="",
            encoding=encoding_from_descriptor(descriptor),
        ) as stream:
            reader = csv.DictReader(
                stream,
                delimiter=resolved_delimiter,
                restkey=_EXTRA_COLUMNS_KEY,
                restval="",
            )
            return [_normalize_table_row(row, row_number=index) for index, row in enumerate(reader, start=2)]


class DelimitedTableWriter:
    """Write rows of dictionaries as delimited text artifacts."""

    def write(
        self,
        descriptor: ArtifactDescriptor,
        rows: Iterable[Mapping[str, object]],
        *,
        fieldnames: Sequence[str] | None = None,
        delimiter: str | None = None,
        encoding: str | None = None,
    ) -> ArtifactWriteResult:
        """Write a delimited table using ``csv.DictWriter``.

        Args:
            descriptor: Path-backed output descriptor.
            rows: Row mappings to write.
            fieldnames: Optional explicit field order. When omitted, field names
                are collected from row keys in first-seen order.
            delimiter: Optional delimiter override. When omitted, the descriptor
                delimiter facet is used, falling back to a comma.
            encoding: Optional encoding override. When omitted, the descriptor's
                stdlib encoding facet is used, falling back to UTF-8.

        Returns:
            Result containing descriptor metadata with text/table claims plus
            path, encoding, and delimiter facets.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be written.
            UnicodeError: If table text cannot be encoded with the resolved
                encoding.
        """
        row_list = [dict(row) for row in rows]
        resolved_fieldnames = list(fieldnames or _fieldnames_from_rows(row_list))
        resolved_delimiter = delimiter or delimiter_from_descriptor(descriptor)
        resolved_encoding = encoding or encoding_from_descriptor(descriptor)
        with _descriptor_path(descriptor).open("w", newline="", encoding=resolved_encoding) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=resolved_fieldnames,
                delimiter=resolved_delimiter,
            )
            writer.writeheader()
            writer.writerows(row_list)
        return ArtifactWriteResult.from_artifact(
            descriptor_with_claims(
                descriptor,
                claims=(
                    RepresentationClaim("text"),
                    InterfaceClaim("text"),
                    InterfaceClaim("table"),
                ),
                facets=(
                    path_locator_facet(),
                    encoding_facet(resolved_encoding),
                    delimiter_facet(resolved_delimiter),
                ),
            ),
        )


class JsonReader:
    """Read JSON text artifacts with the stdlib ``json`` module."""

    def read(self, descriptor: ArtifactDescriptor) -> JsonDocument:
        """Read JSON from the descriptor's path locator.

        Args:
            descriptor: Path-backed JSON descriptor with a stdlib encoding
                facet.

        Returns:
            JSON-compatible document loaded from the file.

        Raises:
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be read.
            json.JSONDecodeError: If the file does not contain valid JSON.
            UnicodeError: If the file cannot be decoded with the declared
                encoding.
        """
        with _descriptor_path(descriptor).open(encoding=encoding_from_descriptor(descriptor)) as stream:
            return cast(JsonDocument, json.load(stream))


class JsonWriter:
    """Write JSON text artifacts with the stdlib ``json`` module."""

    def write(
        self,
        descriptor: ArtifactDescriptor,
        document: JsonDocument,
        *,
        indent: int | None = 2,
        encoding: str | None = None,
    ) -> ArtifactWriteResult:
        """Write JSON and return descriptor metadata with JSON claims.

        Args:
            descriptor: Path-backed output descriptor.
            document: JSON-compatible document to serialize.
            indent: Indentation passed to ``json.dump``.
            encoding: Optional encoding override. When omitted, the descriptor's
                stdlib encoding facet is used, falling back to UTF-8.

        Returns:
            Result containing descriptor metadata with JSON/text claims plus
            path and encoding facets.

        Raises:
            TypeError: If ``document`` contains values that ``json.dump`` cannot
                serialize.
            ValueError: If the descriptor has no local path locator.
            OSError: If the path cannot be written.
        """
        resolved_encoding = encoding or encoding_from_descriptor(descriptor)
        with _descriptor_path(descriptor).open("w", encoding=resolved_encoding) as stream:
            json.dump(document, stream, ensure_ascii=False, indent=indent)
            stream.write("\n")
        return ArtifactWriteResult.from_artifact(
            descriptor_with_claims(
                descriptor,
                claims=(
                    RepresentationClaim("text"),
                    DataTypeClaim("json", namespace="iana.media-types"),
                    InterfaceClaim("text"),
                    InterfaceClaim("json"),
                ),
                facets=(path_locator_facet(), encoding_facet(resolved_encoding)),
            )
        )


class EmoticonEmojiTextConverter:
    """Convert ASCII emoticons in text artifacts to Unicode emoji."""

    emoticons: Mapping[str, str] = {
        ":)": "🙂",
        ":-)": "🙂",
        ":(": "🙁",
        ":-(": "🙁",
        ";)": "😉",
        ";-)": "😉",
    }

    def convert(self, text: str) -> str:
        """Convert ASCII-compatible text to Unicode emoji text.

        Args:
            text: Runtime text value to convert.

        Returns:
            Converted runtime text value.
        """
        converted = text
        for emoticon, emoji in self.emoticons.items():
            converted = converted.replace(emoticon, emoji)
        return converted


class PigLatinTextConverter:
    """Convert text artifacts to Pig Latin text."""

    def convert(self, text: str) -> str:
        """Convert runtime text to Pig Latin text.

        Args:
            text: Runtime text value to convert.

        Returns:
            Converted runtime text value.
        """
        return _pig_latin_text(text)


class DelimitedTableToJsonConverter:
    """Convert table rows to a JSON-compatible document."""

    def convert(self, rows: TableRows) -> JsonDocument:
        """Convert runtime table rows to a JSON-compatible document.

        Args:
            rows: Runtime table rows from a table reader.

        Returns:
            JSON-compatible list of row dictionaries.
        """
        return cast(JsonDocument, [dict(row) for row in rows])


def stdlib_capabilities() -> tuple[ArtifactCapability, ...]:
    """Create bundled stdlib capability examples.

    Returns:
        Capability declarations for the local path-backed stdlib examples. Each
        declaration uses the same ``ArtifactCapability`` model as external
        plugins and stores an opaque implementation object for tests and
        documentation examples.
    """
    return (
        _capability(
            name="bytes-reader",
            kind=CapabilityKind.READER,
            implementation=BytesReader(),
            input_claims=(InterfaceClaim("bytes"),),
            output_claims=(InterfaceClaim("bytes"),),
            required_facets=(path_locator_facet_requirement(),),
            description="Read local path artifacts with Path.read_bytes.",
        ),
        _capability(
            name="bytes-writer",
            kind=CapabilityKind.WRITER,
            implementation=BytesWriter(),
            output_claims=(RepresentationClaim("file"), InterfaceClaim("bytes")),
            required_facets=(path_locator_facet_requirement(),),
            description="Write local path artifacts with Path.write_bytes.",
        ),
        _capability(
            name="text-reader",
            kind=CapabilityKind.READER,
            implementation=TextReader(),
            input_claims=(InterfaceClaim("text"),),
            output_claims=(InterfaceClaim("text"),),
            required_facets=(path_locator_facet_requirement(), encoding_facet_requirement()),
            description="Read local path artifacts as text using encoding facets.",
        ),
        _capability(
            name="text-writer",
            kind=CapabilityKind.WRITER,
            implementation=TextWriter(),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            required_facets=(path_locator_facet_requirement(), encoding_facet_requirement()),
            description="Write local path artifacts as text.",
        ),
        _capability(
            name="delimited-table-reader",
            kind=CapabilityKind.READER,
            implementation=DelimitedTableReader(),
            input_claims=(InterfaceClaim("table"),),
            output_claims=(InterfaceClaim("table"),),
            required_facets=(
                path_locator_facet_requirement(),
                encoding_facet_requirement(),
                delimiter_facet_requirement(),
            ),
            description="Read delimited text tables with csv.DictReader.",
        ),
        _capability(
            name="delimited-table-writer",
            kind=CapabilityKind.WRITER,
            implementation=DelimitedTableWriter(),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text"), InterfaceClaim("table")),
            required_facets=(
                path_locator_facet_requirement(),
                encoding_facet_requirement(),
                delimiter_facet_requirement(),
            ),
            description="Write delimited text tables with csv.DictWriter.",
        ),
        _capability(
            name="json-reader",
            kind=CapabilityKind.READER,
            implementation=JsonReader(),
            input_claims=(InterfaceClaim("json"),),
            output_claims=(InterfaceClaim("json"),),
            required_facets=(path_locator_facet_requirement(), encoding_facet_requirement()),
            description="Read JSON artifacts with json.load.",
        ),
        _capability(
            name="json-writer",
            kind=CapabilityKind.WRITER,
            implementation=JsonWriter(),
            output_claims=(
                RepresentationClaim("text"),
                DataTypeClaim("json", namespace="iana.media-types"),
                InterfaceClaim("text"),
                InterfaceClaim("json"),
            ),
            required_facets=(path_locator_facet_requirement(), encoding_facet_requirement()),
            description="Write JSON artifacts with json.dump.",
        ),
        _capability(
            name="emoticon-to-emoji-text-converter",
            kind=CapabilityKind.CONVERTER,
            implementation=EmoticonEmojiTextConverter(),
            input_claims=(InterfaceClaim("text"),),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            description="Convert ASCII emoticons in text artifacts to Unicode emoji text.",
        ),
        _capability(
            name="pig-latin-text-converter",
            kind=CapabilityKind.CONVERTER,
            implementation=PigLatinTextConverter(),
            input_claims=(InterfaceClaim("text"),),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            description="Convert text artifacts to Pig Latin text.",
        ),
        _capability(
            name="delimited-table-to-json-converter",
            kind=CapabilityKind.CONVERTER,
            implementation=DelimitedTableToJsonConverter(),
            input_claims=(InterfaceClaim("table"),),
            output_claims=(
                RepresentationClaim("text"),
                DataTypeClaim("json", namespace="iana.media-types"),
                InterfaceClaim("text"),
                InterfaceClaim("json"),
            ),
            description="Read delimited text rows and write a JSON document.",
        ),
    )


def register_stdlib_capabilities(registry: Any) -> Any:
    """Register bundled stdlib capabilities with a capability registry.

    Args:
        registry: Registry object with ``register_capability`` or ``register``.

    Returns:
        The registry, to support fluent setup in tests and examples.
    """
    register = getattr(registry, "register_capability", None)
    if register is None:
        register = registry.register
    for capability in stdlib_capabilities():
        register(capability)
    return registry


def bytes_artifact_descriptor(path: str | Path, *, id: str = "data") -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as bytes.

    Args:
        path: Local filesystem path.
        id: Descriptor identifier.

    Returns:
        Artifact descriptor with bytes/file claims and stdlib path facet.
    """
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[RepresentationClaim("file"), InterfaceClaim("bytes")],
        facets=[path_locator_facet()],
    )


def text_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as text.

    Args:
        path: Local filesystem path.
        id: Descriptor identifier.
        encoding: Text encoding recorded in the stdlib encoding facet.

    Returns:
        Artifact descriptor with text interface claims plus path and encoding
        facets.
    """
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[RepresentationClaim("text"), InterfaceClaim("text")],
        facets=[path_locator_facet(), encoding_facet(encoding)],
    )


def delimited_table_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    delimiter: str = ",",
    encoding: str = DEFAULT_TEXT_ENCODING,
    data_type: str = "csv",
) -> ArtifactDescriptor:
    """Build a descriptor selectable as bytes, text, or a delimited table.

    Args:
        path: Local filesystem path.
        id: Descriptor identifier.
        delimiter: Delimiter recorded in the stdlib delimited-text facet.
        encoding: Text encoding recorded in the stdlib encoding facet.
        data_type: IANA media type data-type claim name.

    Returns:
        Artifact descriptor with bytes/text/table interfaces plus path,
        encoding, and delimiter facets.
    """
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[
            RepresentationClaim("file"),
            RepresentationClaim("text"),
            DataTypeClaim(data_type, namespace="iana.media-types"),
            InterfaceClaim("bytes"),
            InterfaceClaim("text"),
            InterfaceClaim("table"),
        ],
        facets=[path_locator_facet(), encoding_facet(encoding), delimiter_facet(delimiter)],
    )


def json_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as JSON text.

    Args:
        path: Local filesystem path.
        id: Descriptor identifier.
        encoding: Text encoding recorded in the stdlib encoding facet.

    Returns:
        Artifact descriptor with JSON/text interface claims plus path and
        encoding facets.
    """
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[
            RepresentationClaim("file"),
            RepresentationClaim("text"),
            DataTypeClaim("json", namespace="iana.media-types"),
            InterfaceClaim("text"),
            InterfaceClaim("json"),
        ],
        facets=[path_locator_facet(), encoding_facet(encoding)],
    )


def temporary_text_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "temporary-output",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a non-catalog output descriptor suitable for capability selection.

    Args:
        path: Local filesystem path for temporary output.
        id: Descriptor identifier.
        encoding: Text encoding recorded in the stdlib encoding facet.

    Returns:
        Derived artifact descriptor marked as temporary/non-catalogued with text
        claims plus path and encoding facets.
    """
    return ArtifactDescriptor(
        id=id,
        role="derived_artifact",
        locator=ArtifactLocator.path(path),
        relationship={"catalogued": False, "temporary": True},
        claims=[RepresentationClaim("text"), InterfaceClaim("text")],
        facets=[path_locator_facet(), encoding_facet(encoding)],
    )


def path_locator_facet() -> ArtifactFacet:
    """Build the stdlib facet declaring a local path-backed descriptor.

    Returns:
        Facet whose namespace/version belongs to the bundled stdlib examples.
    """
    return ArtifactFacet(
        kind="locator",
        name="path",
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
    )


def path_locator_facet_requirement() -> ArtifactFacet:
    """Build a requirement for the stdlib local path locator facet.

    Returns:
        Facet requirement used by path-backed stdlib capabilities.
    """
    return path_locator_facet()


def encoding_facet(encoding: str) -> ArtifactFacet:
    """Build the stdlib text encoding facet.

    Args:
        encoding: Text encoding name.

    Returns:
        Namespaced facet containing the encoding metadata consumed by stdlib
        text implementations.
    """
    return ArtifactFacet(
        kind="encoding",
        name="charset",
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
        metadata={"encoding": encoding},
    )


def encoding_facet_requirement() -> ArtifactFacet:
    """Build a requirement for a declared text encoding facet.

    Returns:
        Facet requirement that accepts any stdlib charset metadata value.
    """
    return ArtifactFacet(
        kind="encoding",
        name="charset",
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
    )


def delimiter_facet(delimiter: str) -> ArtifactFacet:
    """Build the stdlib delimited text facet.

    Args:
        delimiter: Delimiter string used by ``csv`` readers/writers.

    Returns:
        Namespaced facet containing the delimiter metadata consumed by stdlib
        delimited-table implementations.
    """
    return ArtifactFacet(
        kind="format",
        name="delimited-text",
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
        metadata={"delimiter": delimiter},
    )


def delimiter_facet_requirement() -> ArtifactFacet:
    """Build a requirement for a declared delimited-text facet.

    Returns:
        Facet requirement that accepts any stdlib delimiter metadata value.
    """
    return ArtifactFacet(
        kind="format",
        name="delimited-text",
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
    )


def encoding_from_descriptor(
    descriptor: ArtifactDescriptor,
    *,
    default: str = DEFAULT_TEXT_ENCODING,
) -> str:
    """Return a descriptor encoding from stdlib facets.

    Args:
        descriptor: Descriptor whose facets should be inspected.
        default: Encoding returned when no stdlib charset facet has encoding
            metadata.

    Returns:
        Encoding declared by the stdlib ``encoding/charset`` facet, or
        ``default`` when absent.
    """
    for facet in iter_facets(descriptor):
        if not _is_stdlib_facet(facet, kind="encoding", name="charset"):
            continue
        metadata = facet["metadata"]
        if not isinstance(metadata, Mapping):
            continue
        value = metadata.get("encoding") or metadata.get("charset")
        if value is not None:
            return str(value)
    return default


def delimiter_from_descriptor(descriptor: ArtifactDescriptor, *, default: str = ",") -> str:
    """Return a delimited-text delimiter from stdlib facets.

    Args:
        descriptor: Descriptor whose facets should be inspected.
        default: Delimiter returned when no stdlib delimited-text facet has
            delimiter metadata.

    Returns:
        Delimiter declared by the stdlib ``format/delimited-text`` facet, or
        ``default`` when absent.
    """
    for facet in iter_facets(descriptor):
        if not _is_stdlib_facet(facet, kind="format", name="delimited-text"):
            continue
        metadata = facet["metadata"]
        if not isinstance(metadata, Mapping):
            continue
        value = metadata.get("delimiter")
        if value is not None:
            return str(value)
    return default


def descriptor_with_claims(
    descriptor: ArtifactDescriptor,
    *,
    claims: Iterable[ArtifactClaim] = (),
    facets: Iterable[ArtifactFacet] = (),
    relationship: Mapping[str, object] | None = None,
) -> ArtifactDescriptor:
    """Return a descriptor copy with merged claims, facets, and relationships.

    Args:
        descriptor: Source descriptor to copy.
        claims: Claims to merge by namespace/kind/name/version. Added claims
            replace existing claims with the same schema envelope.
        facets: Facets to merge by namespace/kind/name/version. Added facets
            replace existing facets with the same schema envelope.
        relationship: Optional relationship metadata to merge over existing
            descriptor relationship metadata.

    Returns:
        New descriptor preserving locator and state with merged schema facts.
    """
    return ArtifactDescriptor(
        id=descriptor.id,
        role=descriptor.role,
        locator=descriptor.locator,
        state=descriptor.state,
        relationship={
            **descriptor.relationship,
            **({} if relationship is None else cast(MetadataDict, dict(relationship))),
        },
        claims=_merge_claim_items(descriptor.claims, claims),
        facets=_merge_facet_items(descriptor.facets, facets),
    )


def _capability(
    *,
    name: str,
    kind: CapabilityKind,
    implementation: object,
    description: str,
    input_claims: tuple[ArtifactClaim, ...] = (),
    output_claims: tuple[ArtifactClaim, ...] = (),
    required_facets: tuple[ArtifactFacet, ...] = (),
) -> ArtifactCapability:
    """Build a bundled stdlib artifact capability."""
    return ArtifactCapability(
        kind=kind,
        name=name,
        namespace=PLUGIN_NAMESPACE,
        version=SCHEMA_VERSION,
        input_claims=input_claims,
        output_claims=output_claims,
        required_facets=required_facets,
        metadata={"description": description, "plugin": "stdlib_io"},
        implementation=implementation,
    )


def _descriptor_path(descriptor: ArtifactDescriptor) -> Path:
    """Return a local path from a descriptor or raise a clear error."""
    if descriptor.locator is None:
        raise ValueError(f"artifact descriptor {descriptor.id!r} has no locator")
    path = descriptor.locator.as_path()
    if path is None:
        raise ValueError(
            f"artifact descriptor {descriptor.id!r} is not path-backed: {descriptor.locator.kind!r}"
        )
    return path


def _is_stdlib_facet(facet: Mapping[str, object], *, kind: str, name: str) -> bool:
    """Return whether a normalized facet belongs to this stdlib schema item."""
    return (
        facet.get("namespace") == PLUGIN_NAMESPACE
        and facet.get("version") == SCHEMA_VERSION
        and facet.get("kind") == kind
        and facet.get("name") == name
    )


def _fieldnames_from_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Return stable CSV field names from row keys."""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(str(key))
    return fieldnames


def _normalize_table_row(row: Mapping[Any, Any], *, row_number: int) -> dict[str, str]:
    """Return a csv row normalized to string keys and values."""
    extra_columns = row.get(_EXTRA_COLUMNS_KEY)
    if isinstance(extra_columns, list):
        raise ValueError(f"delimited table row {row_number} has more columns than the header")

    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            raise ValueError(f"delimited table row {row_number} contains a column without a header")
        if isinstance(value, list):
            raise ValueError(f"delimited table row {row_number} contains a non-scalar value")
        normalized[str(key)] = "" if value is None else str(value)
    return normalized


def _pig_latin_text(text: str) -> str:
    """Return text with ASCII words converted to Pig Latin."""
    return _WORD_RE.sub(lambda match: _pig_latin_word(match.group(0)), text)


def _pig_latin_word(word: str) -> str:
    """Return one ASCII word converted to Pig Latin."""
    first_vowel = next((index for index, char in enumerate(word) if char in _VOWELS), -1)
    if first_vowel == 0:
        return f"{word}way"
    if first_vowel > 0:
        return f"{word[first_vowel:]}{word[:first_vowel]}ay"
    return f"{word}ay"


def _merge_claim_items(
    existing: Iterable[ArtifactClaimInput],
    added: Iterable[ArtifactClaim],
) -> list[ArtifactClaimInput]:
    """Merge claim schema items by namespace envelope."""
    return cast(list[ArtifactClaimInput], _merge_schema_items(existing, added))


def _merge_facet_items(
    existing: Iterable[ArtifactFacetInput],
    added: Iterable[ArtifactFacet],
) -> list[ArtifactFacetInput]:
    """Merge facet schema items by namespace envelope."""
    return cast(list[ArtifactFacetInput], _merge_schema_items(existing, added))


def _merge_schema_items(existing: Iterable[object], added: Iterable[object]) -> list[object]:
    """Merge claim or facet-like schema items, letting added items replace existing ones."""
    items_by_key: dict[tuple[str, str, str, str], object] = {}
    for item in [*existing, *added]:
        items_by_key[_schema_item_key(item)] = item
    return list(items_by_key.values())


def _schema_item_key(item: object) -> tuple[str, str, str, str]:
    """Return a schema item key from a claim or facet object."""
    if isinstance(item, ArtifactClaim | ArtifactFacet):
        return (item.namespace, item.kind, item.name, item.version)
    if isinstance(item, Mapping):
        return (
            str(item.get("namespace", "ogcat.core")),
            str(item.get("kind", "")),
            str(item.get("name", item.get("kind", ""))),
            str(item.get("version", "1")),
        )
    return (type(item).__module__, type(item).__name__, repr(item), "")
