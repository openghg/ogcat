"""Dependency-free stdlib reader, writer, and converter capability examples."""

from __future__ import annotations

import csv
import inspect
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias, cast

from ogcat.artifact_claims import has_claim, iter_facets
from ogcat.models import (
    ArtifactClaim,
    ArtifactClaimInput,
    ArtifactDescriptor,
    ArtifactFacet,
    ArtifactFacetInput,
    ArtifactLocator,
    DataTypeClaim,
    InterfaceClaim,
    JsonValue,
    MetadataDict,
    RepresentationClaim,
)

PLUGIN_NAMESPACE = "ogcat.stdlib"
SCHEMA_VERSION = "1"
DEFAULT_TEXT_ENCODING = "utf-8"

JsonDocument: TypeAlias = JsonValue
TableRows: TypeAlias = list[dict[str, str]]
_WORD_RE = re.compile(r"[A-Za-z]+")
_VOWELS = frozenset("aeiouAEIOU")


@dataclass(frozen=True, slots=True)
class StdlibCapability:
    """Small capability object used when the core class is unavailable.

    The public attributes intentionally use plain names so the examples remain
    easy for the core registry to inspect without special cases.
    """

    name: str
    operation: str
    implementation: object
    required_claims: tuple[ArtifactClaim, ...] = ()
    required_facets: tuple[ArtifactFacet, ...] = ()
    namespace: str = PLUGIN_NAMESPACE
    version: str = SCHEMA_VERSION
    description: str = ""
    priority: int = 0
    metadata: MetadataDict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """Return the operation name for registries that use ``kind``."""
        return self.operation

    @property
    def capability_type(self) -> str:
        """Return the operation name for registries that use ``capability_type``."""
        return self.operation

    def matches(
        self,
        descriptor: ArtifactDescriptor,
        *,
        operation: str | None = None,
        interface: str | None = None,
        data_type: str | None = None,
    ) -> bool:
        """Return whether this capability can handle a descriptor."""
        if operation is not None and operation != self.operation:
            return False
        if interface is not None and not has_claim(descriptor, kind="interface", name=interface):
            return False
        if data_type is not None and not has_claim(descriptor, kind="data_type", name=data_type):
            return False
        return all(_descriptor_has_claim(descriptor, claim) for claim in self.required_claims)

    def supports(self, descriptor: ArtifactDescriptor, **criteria: object) -> bool:
        """Compatibility alias for registries that call ``supports``."""
        operation = criteria.get("operation")
        interface = criteria.get("interface")
        data_type = criteria.get("data_type")
        return self.matches(
            descriptor,
            operation=None if operation is None else str(operation),
            interface=None if interface is None else str(interface),
            data_type=None if data_type is None else str(data_type),
        )


class BytesReader:
    """Read path-backed artifacts as raw bytes."""

    def read(self, descriptor: ArtifactDescriptor) -> bytes:
        """Read bytes from the descriptor's path locator."""
        return _descriptor_path(descriptor).read_bytes()


class BytesWriter:
    """Write path-backed artifacts as raw bytes."""

    def write(self, descriptor: ArtifactDescriptor, data: bytes) -> ArtifactDescriptor:
        """Write bytes to the descriptor's path locator and return descriptor metadata."""
        _descriptor_path(descriptor).write_bytes(data)
        return descriptor_with_claims(
            descriptor,
            claims=(InterfaceClaim("bytes"), RepresentationClaim("file")),
        )


class TextReader:
    """Read path-backed artifacts as text using descriptor encoding facets."""

    def read(self, descriptor: ArtifactDescriptor) -> str:
        """Read text from the descriptor's path locator."""
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
    ) -> ArtifactDescriptor:
        """Write text and return descriptor metadata with an encoding facet."""
        resolved_encoding = encoding or encoding_from_descriptor(descriptor)
        _descriptor_path(descriptor).write_text(text, encoding=resolved_encoding)
        return descriptor_with_claims(
            descriptor,
            claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            facets=(encoding_facet(resolved_encoding),),
        )


class DelimitedTableReader:
    """Read delimited text artifacts as rows of dictionaries."""

    def read(
        self,
        descriptor: ArtifactDescriptor,
        *,
        delimiter: str | None = None,
    ) -> TableRows:
        """Read a delimited table using ``csv.DictReader``."""
        resolved_delimiter = delimiter or delimiter_from_descriptor(descriptor)
        with _descriptor_path(descriptor).open(
            newline="",
            encoding=encoding_from_descriptor(descriptor),
        ) as stream:
            return [dict(row) for row in csv.DictReader(stream, delimiter=resolved_delimiter)]


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
    ) -> ArtifactDescriptor:
        """Write a delimited table using ``csv.DictWriter``."""
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
        return descriptor_with_claims(
            descriptor,
            claims=(
                RepresentationClaim("text"),
                InterfaceClaim("text"),
                InterfaceClaim("table"),
            ),
            facets=(
                encoding_facet(resolved_encoding),
                delimiter_facet(resolved_delimiter),
            ),
        )


class JsonReader:
    """Read JSON text artifacts with the stdlib ``json`` module."""

    def read(self, descriptor: ArtifactDescriptor) -> JsonDocument:
        """Read JSON from the descriptor's path locator."""
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
    ) -> ArtifactDescriptor:
        """Write JSON and return descriptor metadata with JSON claims."""
        resolved_encoding = encoding or encoding_from_descriptor(descriptor)
        with _descriptor_path(descriptor).open("w", encoding=resolved_encoding) as stream:
            json.dump(document, stream, ensure_ascii=False, indent=indent)
            stream.write("\n")
        return descriptor_with_claims(
            descriptor,
            claims=(
                RepresentationClaim("text"),
                DataTypeClaim("json", namespace="iana.media-types"),
                InterfaceClaim("text"),
                InterfaceClaim("json"),
            ),
            facets=(encoding_facet(resolved_encoding),),
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

    def convert(
        self,
        source_descriptor: ArtifactDescriptor,
        output_descriptor: ArtifactDescriptor,
    ) -> ArtifactDescriptor:
        """Read ASCII-compatible text and write UTF-8 emoji text output."""
        source_text = TextReader().read(source_descriptor)
        converted = source_text
        for emoticon, emoji in self.emoticons.items():
            converted = converted.replace(emoticon, emoji)

        output = descriptor_with_claims(
            output_descriptor,
            claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            facets=(encoding_facet(DEFAULT_TEXT_ENCODING),),
            relationship={
                "generated_by": "ogcat.stdlib.emoticon_to_emoji",
                "source_artifact_id": source_descriptor.id,
            },
        )
        return TextWriter().write(output, converted, encoding=DEFAULT_TEXT_ENCODING)


class PigLatinTextConverter:
    """Convert text artifacts to Pig Latin text."""

    def convert(
        self,
        source_descriptor: ArtifactDescriptor,
        output_descriptor: ArtifactDescriptor,
    ) -> ArtifactDescriptor:
        """Read text and write UTF-8 Pig Latin text output."""
        converted = _pig_latin_text(TextReader().read(source_descriptor))
        output = descriptor_with_claims(
            output_descriptor,
            claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            facets=(encoding_facet(DEFAULT_TEXT_ENCODING),),
            relationship={
                "generated_by": "ogcat.stdlib.pig_latin",
                "source_artifact_id": source_descriptor.id,
            },
        )
        return TextWriter().write(output, converted, encoding=DEFAULT_TEXT_ENCODING)


def stdlib_capabilities() -> tuple[object, ...]:
    """Create bundled stdlib capability examples."""
    return (
        _capability(
            name="bytes-reader",
            operation="reader",
            implementation=BytesReader(),
            required_claims=(InterfaceClaim("bytes"),),
            output_claims=(InterfaceClaim("bytes"),),
            description="Read local path artifacts with Path.read_bytes.",
        ),
        _capability(
            name="bytes-writer",
            operation="writer",
            implementation=BytesWriter(),
            output_claims=(RepresentationClaim("file"), InterfaceClaim("bytes")),
            description="Write local path artifacts with Path.write_bytes.",
        ),
        _capability(
            name="text-reader",
            operation="reader",
            implementation=TextReader(),
            required_claims=(InterfaceClaim("text"),),
            output_claims=(InterfaceClaim("text"),),
            required_facets=(encoding_facet_requirement(),),
            description="Read local path artifacts as text using encoding facets.",
        ),
        _capability(
            name="text-writer",
            operation="writer",
            implementation=TextWriter(),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            required_facets=(encoding_facet_requirement(),),
            description="Write local path artifacts as text.",
        ),
        _capability(
            name="delimited-table-reader",
            operation="reader",
            implementation=DelimitedTableReader(),
            required_claims=(InterfaceClaim("table"),),
            output_claims=(InterfaceClaim("table"),),
            required_facets=(encoding_facet_requirement(), delimiter_facet_requirement()),
            description="Read delimited text tables with csv.DictReader.",
        ),
        _capability(
            name="delimited-table-writer",
            operation="writer",
            implementation=DelimitedTableWriter(),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text"), InterfaceClaim("table")),
            required_facets=(encoding_facet_requirement(), delimiter_facet_requirement()),
            description="Write delimited text tables with csv.DictWriter.",
        ),
        _capability(
            name="json-reader",
            operation="reader",
            implementation=JsonReader(),
            required_claims=(InterfaceClaim("json"),),
            output_claims=(InterfaceClaim("json"),),
            required_facets=(encoding_facet_requirement(),),
            description="Read JSON artifacts with json.load.",
        ),
        _capability(
            name="json-writer",
            operation="writer",
            implementation=JsonWriter(),
            output_claims=(
                RepresentationClaim("text"),
                DataTypeClaim("json", namespace="iana.media-types"),
                InterfaceClaim("text"),
                InterfaceClaim("json"),
            ),
            required_facets=(encoding_facet_requirement(),),
            description="Write JSON artifacts with json.dump.",
        ),
        _capability(
            name="emoticon-to-emoji-text-converter",
            operation="converter",
            implementation=EmoticonEmojiTextConverter(),
            required_claims=(InterfaceClaim("text"),),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            required_facets=(encoding_facet_requirement(),),
            description="Convert ASCII emoticons in text artifacts to Unicode emoji text.",
        ),
        _capability(
            name="pig-latin-text-converter",
            operation="converter",
            implementation=PigLatinTextConverter(),
            required_claims=(InterfaceClaim("text"),),
            output_claims=(RepresentationClaim("text"), InterfaceClaim("text")),
            required_facets=(encoding_facet_requirement(),),
            description="Convert text artifacts to Pig Latin text.",
        ),
    )


def register_stdlib_capabilities(registry: Any) -> Any:
    """Register bundled stdlib capabilities with a capability registry.

    Args:
        registry: Registry object with a ``register`` method.

    Returns:
        The registry, to support fluent setup in tests and examples.
    """
    for capability in stdlib_capabilities():
        register = getattr(registry, "register_capability", registry.register)
        register(capability)
    return registry


def bytes_artifact_descriptor(path: str | Path, *, id: str = "data") -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as bytes."""
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[RepresentationClaim("file"), InterfaceClaim("bytes")],
    )


def text_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as text."""
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[RepresentationClaim("text"), InterfaceClaim("text")],
        facets=[encoding_facet(encoding)],
    )


def delimited_table_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    delimiter: str = ",",
    encoding: str = DEFAULT_TEXT_ENCODING,
    data_type: str = "csv",
) -> ArtifactDescriptor:
    """Build a descriptor selectable as bytes, text, or a delimited table."""
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[
            RepresentationClaim("text"),
            DataTypeClaim(data_type, namespace="iana.media-types"),
            InterfaceClaim("bytes"),
            InterfaceClaim("text"),
            InterfaceClaim("table"),
        ],
        facets=[encoding_facet(encoding), delimiter_facet(delimiter)],
    )


def json_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "data",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a path-backed descriptor selectable as JSON text."""
    return ArtifactDescriptor(
        id=id,
        role="data_artifact",
        locator=ArtifactLocator.path(path),
        claims=[
            RepresentationClaim("text"),
            DataTypeClaim("json", namespace="iana.media-types"),
            InterfaceClaim("text"),
            InterfaceClaim("json"),
        ],
        facets=[encoding_facet(encoding)],
    )


def temporary_text_artifact_descriptor(
    path: str | Path,
    *,
    id: str = "temporary-output",
    encoding: str = DEFAULT_TEXT_ENCODING,
) -> ArtifactDescriptor:
    """Build a non-catalog output descriptor suitable for capability selection."""
    return ArtifactDescriptor(
        id=id,
        role="derived_artifact",
        locator=ArtifactLocator.path(path),
        relationship={"catalogued": False, "temporary": True},
        claims=[RepresentationClaim("text"), InterfaceClaim("text")],
        facets=[encoding_facet(encoding)],
    )


def encoding_facet(encoding: str) -> ArtifactFacet:
    """Build the stdlib text encoding facet."""
    return ArtifactFacet(
        kind="encoding",
        name="charset",
        namespace=PLUGIN_NAMESPACE,
        metadata={"encoding": encoding},
    )


def encoding_facet_requirement() -> ArtifactFacet:
    """Build a requirement for a declared text encoding facet."""
    return ArtifactFacet(
        kind="encoding",
        name="charset",
        namespace=PLUGIN_NAMESPACE,
    )


def delimiter_facet(delimiter: str) -> ArtifactFacet:
    """Build the stdlib delimited text facet."""
    return ArtifactFacet(
        kind="format",
        name="delimited-text",
        namespace=PLUGIN_NAMESPACE,
        metadata={"delimiter": delimiter},
    )


def delimiter_facet_requirement() -> ArtifactFacet:
    """Build a requirement for a declared delimited-text facet."""
    return ArtifactFacet(
        kind="format",
        name="delimited-text",
        namespace=PLUGIN_NAMESPACE,
    )


def encoding_from_descriptor(
    descriptor: ArtifactDescriptor,
    *,
    default: str = DEFAULT_TEXT_ENCODING,
) -> str:
    """Return a descriptor encoding from facets, falling back to an explicit default."""
    for facet in iter_facets(descriptor):
        if facet["kind"] != "encoding" or facet["name"] != "charset":
            continue
        metadata = facet["metadata"]
        if not isinstance(metadata, Mapping):
            continue
        value = metadata.get("encoding") or metadata.get("charset")
        if value is not None:
            return str(value)
    return default


def delimiter_from_descriptor(descriptor: ArtifactDescriptor, *, default: str = ",") -> str:
    """Return a delimited-text delimiter from facets."""
    for facet in iter_facets(descriptor):
        if facet["kind"] != "format" or facet["name"] != "delimited-text":
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
    """Return a descriptor copy with merged claims, facets, and relationship metadata."""
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
    operation: str,
    implementation: object,
    description: str,
    required_claims: tuple[ArtifactClaim, ...] = (),
    output_claims: tuple[ArtifactClaim, ...] = (),
    required_facets: tuple[ArtifactFacet, ...] = (),
) -> object:
    """Build a core ArtifactCapability when available, otherwise a local object."""
    fallback = StdlibCapability(
        name=name,
        operation=operation,
        implementation=implementation,
        required_claims=required_claims,
        required_facets=required_facets,
        description=description,
    )
    try:
        from ogcat.capabilities import ArtifactCapability
    except ImportError:
        return fallback
    capability_factory = cast(Any, ArtifactCapability)

    metadata: MetadataDict = {"plugin": "stdlib_io"}
    values: dict[str, object] = {
        "name": name,
        "namespace": PLUGIN_NAMESPACE,
        "version": SCHEMA_VERSION,
        "operation": operation,
        "kind": operation,
        "capability_type": operation,
        "implementation": implementation,
        "handler": implementation,
        "input_claims": required_claims,
        "output_claims": output_claims,
        "required_claims": required_claims,
        "claims": required_claims,
        "required_facets": required_facets,
        "facets": required_facets,
        "description": description,
        "priority": 0,
        "metadata": metadata,
    }
    try:
        parameters = inspect.signature(capability_factory).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs = {name: values[name] for name in parameters if name in values}
    if kwargs:
        try:
            return capability_factory(**kwargs)
        except TypeError:
            pass

    for kwargs in (
        {
            "name": name,
            "operation": operation,
            "implementation": implementation,
            "required_claims": required_claims,
            "description": description,
            "metadata": metadata,
        },
        {"name": name, "kind": operation, "implementation": implementation},
        {"name": name, "implementation": implementation},
    ):
        try:
            return capability_factory(**kwargs)
        except TypeError:
            continue
    return fallback


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


def _descriptor_has_claim(descriptor: ArtifactDescriptor, claim: ArtifactClaim) -> bool:
    """Return whether a descriptor has a matching claim envelope."""
    return has_claim(
        descriptor,
        kind=claim.kind,
        name=claim.name,
        namespace=claim.namespace,
        version=claim.version,
    )


def _fieldnames_from_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Return stable CSV field names from row keys."""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(str(key))
    return fieldnames


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
    """Merge claim or facet-like schema items by namespace envelope."""
    items: list[object] = []
    keys: set[tuple[str, str, str, str]] = set()
    for item in [*existing, *added]:
        key = _schema_item_key(item)
        if key in keys:
            continue
        keys.add(key)
        items.append(item)
    return items


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
