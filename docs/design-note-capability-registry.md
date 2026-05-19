# Design Note: Capability Registry

This note records the #119 documentation slice for reader, writer, and
converter capability registration and lookup. It follows ADR 0002 and the
artifact claim/facet schema note.

The scope is deliberately narrow:

- define how capabilities are declared, registered, and selected;
- dispatch by `ArtifactDescriptor` claims and facets, not by `record_type`;
- explain how bundled and external plugins use the same route;
- preserve room for future typed reader handles and operation-materializer
  result merging.

This slice does not define the public `open_artifact()` API, read-handle
lifecycle, shell-like pipeline syntax, or catalog merge of writer-returned
artifact descriptors. Those remain separate work items.

## Core Idea

A capability is a typed declaration, optionally paired with an opaque runtime
implementation object. The declaration says what the implementation can accept
and produce. The registry stores those declarations and answers lookup
questions. Core does not infer behavior from a Python object alone.

The three initial capability kinds are:

- `reader`: opens an artifact or source through one requested interface;
- `writer`: materializes one requested output artifact shape or interface;
- `converter`: converts one runtime interface to another.

Capability declarations are data. Implementations are opaque to the registry.
An implementation may be a function, object, class instance, protocol
implementation, or plugin-owned adapter. The registry may return it, but this
slice does not make core invoke it implicitly.

Terminology matters: a registry writer capability is not the same thing as the
operation-scoped `ArtifactWriter`/materializer helpers in `ogcat.writers`.
Writer capabilities declare typed I/O behavior. Operation materializers prepare
the target descriptor, invoke a capability or one-off function, register
rollback, collect produced descriptor facts, and hand those facts to the
operation runner for merge.

## Declaration Shape

Each declaration needs stable identity and typed input/output requirements:

- `kind`: `reader`, `writer`, or `converter`.
- `name`: namespace-local capability name.
- `namespace`: owner namespace such as `ogcat.core`, a package name, plugin id,
  or reverse-DNS name.
- `version`: declaration contract version.
- `input_claims`: exact claims or claim patterns the capability can accept.
- `output_claims`: exact claims or claim patterns the capability can produce.
- `required_facets`: optional structured facts required for lookup. Matching
  uses the facet namespace/kind/name/version envelope plus required metadata as
  a subset, so `encoding=utf-8` and `encoding=ascii` can be distinguished even
  when the discriminating value lives in `facet.metadata`.
- `metadata`: optional structured facts the capability promises or records
  about produced outputs.
- `options`: optional plugin-owned JSON-compatible option metadata.
- `implementation`: optional opaque runtime object registered with the
  declaration.

The registry validates the common envelope and JSON-compatible declaration
metadata. Claim matching is envelope-only: claim metadata is descriptive and is
not dispatch-significant in this slice. Facet metadata is dispatch-significant
through subset matching, so plugin authors should put values such as encodings,
delimiters, member names, or locator requirements in facets rather than claim
metadata when selection must inspect them. Plugin-owned option schemas and
implementation objects remain plugin-owned. Core should not import pandas,
xarray, Zarr, Intake, or OpenGHG-specific packages to validate declarations for
those domains.

## Registration

Core, bundled plugins, and external plugins register through the same route:

```python
from ogcat.capabilities import ArtifactCapability, CapabilityKind, CapabilityRegistry


registry = CapabilityRegistry()
registry.register(
    ArtifactCapability(
        kind=CapabilityKind.READER,
        name="utf8-text-reader",
        namespace="ogcat.stdlib",
        input_claims=[
            {"kind": "interface", "name": "text"},
            {"kind": "representation", "name": "text"},
        ],
        output_claims=[
            {"kind": "interface", "name": "text"},
        ],
        required_facets=[
            {"kind": "encoding", "name": "charset", "metadata": {"encoding": "utf-8"}},
        ],
        implementation=object(),
    ),
)
```

Registration is additive. If a plugin wants to replace or shadow a capability,
it should do so explicitly by name/version or through a caller-supplied registry
composition policy. `find()` returns matches in registration order for
diagnostics and UI display, but `select()` does not use registration order to
pick a winner from ambiguous matches.

## Lookup And Selection

Lookup starts from a request, not from `record_type`. The request names the
kind of capability and the exact interface or output the caller wants. For
artifact reads, the input facts come from an `ArtifactDescriptor`:

```python
match = registry.select(
    kind="reader",
    descriptor=descriptor,
    input_claims=[{"kind": "interface", "name": "table"}],
)
```

The selector compares the request against the artifact's claims/facets and the
registered capability declarations. A descriptor can claim many interfaces at
once. For example, a CSV artifact may advertise:

- `interface=bytes`;
- `interface=text`;
- `interface=table`;
- `data_type=csv`;
- `representation=file` or a plugin-owned `locator/path` facet for a local
  path-backed implementation;
- `representation=text`;
- `encoding/charset` with metadata `{"encoding": "utf-8"}`;
- `format/delimited-text` with metadata `{"delimiter": ","}`.

That descriptor is intentionally ambiguous if the caller asks only to "read"
it. The caller must request a specific input interface such as
`interface=bytes`, `interface=text`, or `interface=table`. `select()` should
raise an ambiguity error until the request is specific enough.

Facet requirements are metadata-aware. A capability that requires an
`encoding/charset` facet with metadata `{"encoding": "utf-8"}` should match a
descriptor facet with the same envelope and at least that metadata, but it
should not match a descriptor that declares `{"encoding": "ascii"}`.
If a capability can use any declared value for that facet, it should require
the facet envelope with empty metadata and then interpret the actual descriptor
metadata at runtime. Runtime helpers should consume the same namespaced
facet/version that the capability declares, so unrelated plugins cannot change
behavior by using the same facet kind/name in their own namespace.

Writer capabilities and converters use the same rule. A caller requests exact
input and output claims or interfaces:

```python
emoji_converter = registry.select(
    kind=CapabilityKind.CONVERTER,
    name="emoticon-to-emoji",
    input_claims=[{"kind": "interface", "name": "text"}],
    output_claims=[{"kind": "interface", "name": "text"}],
)
```

The registry may return candidate matches through a `find()` method and a
single match through `select()`. Missing, unsupported, and ambiguous matches
should raise distinct errors so callers can explain whether a plugin is absent,
the artifact lacks required claims/facets, or the request needs a more exact
interface. Malformed lookup filters should raise a lookup-domain validation
error rather than leaking raw schema normalization exceptions.

## Dispatch Inputs

Dispatch uses `ArtifactDescriptor` facts:

- claims: data type, representation, and interface contracts;
- facets: encoding, suffixes, table dialect, archive members, collection
  members, validation state, and plugin-owned structured facts;
- optional caller request details: output interface, namespace/version, options,
  trust policy, and desired materialization behavior.

Dispatch must not use `CatalogRecord.record_type` as an I/O key. `record_type`
remains logical/schema metadata for validation, naming, and search. Two records
with different `record_type` values may point at artifacts with the same
capabilities. One record may also own several artifacts with different
capabilities.

## Relationship To PluginRegistry

`PluginRegistry` remains the lifecycle hook registry. It owns objects that
participate in operation hooks such as `before_validate_metadata`,
`extract_metadata`, and `after_commit`.

The capability registry owns typed declarations for readers, writer capabilities, and
converters. A plugin object may contribute both lifecycle hooks and capability
declarations, but those are different extension points:

- hooks observe or mutate catalog operations at lifecycle points;
- capabilities declare typed artifact access, materialization, or conversion
  contracts;
- implementations may need `OperationContext` later, but registration and
  lookup do not require a hook method;
- core, bundled plugins, and external plugins all register capability
  declarations through the same capability route.

One practical shape is for `PluginRegistry` to expose or feed a
`CapabilityRegistry` during catalog setup. That keeps user ergonomics close to
today's plugin registration while preserving the design boundary between hooks
and typed capabilities.

## Bundled Stdlib Examples

The first examples should stay dependency-light and use only the Python
standard library. They should live in bundled plugin-style submodules and be
registered like external plugin capabilities, not as special cases hard-coded
into core dispatch.

Useful examples:

- bytes reader/writer declarations for local path-backed artifacts, using a
  path locator facet rather than relying on `ArtifactLocator.kind` during
  registry matching;
- text reader/writer declarations with path locator and encoding facets such as
  UTF-8 and ASCII;
- CSV and other delimited table readers using `csv`, with bytes, text, and
  table interfaces advertised separately;
- JSON reader/writer declarations using `json`, with `interface=json`;
- an emoticon-to-emoji text converter that maps ASCII emoticons such as `:)` to
  Unicode emoji;
- a Pig Latin text converter that shows CSV-like descriptors can be routed as
  text when text input and text output are requested, but the same converter
  must not be selected when the requested output is CSV or a table.

These examples are meant to exercise the registry design. They must not bend
core matching rules to make toy examples pass, and they must not introduce
optional scientific dependencies. NetCDF, HDF5, Zarr, xarray, pandas, Intake,
and OpenGHG-specific behavior remain optional plugin territory.
The bundled converter implementations call the bundled text reader and writer
directly only to keep this slice executable without the future handle API. That
is useful as a plugin-style pressure test, but it is not the intended long-term
pipeline executor.

The long-term executor should make composition explicit. A reader selected with
zero or more converters can be exposed as a reader/read plan for a requested
output interface, following Intake's idea that a pipeline of reader/converters
is itself reader-like. Operation materializers then consume the final runtime
value or handle and use writer capabilities to produce artifact descriptors
inside the operation rollback/audit boundary.

## Preserved Testing Request

When #119 moves from documentation into implementation, preserve this testing
scope:

- implement complete examples for text, CSV and other delimited tables, and
  JSON using built-in libraries;
- keep example reader, writer, and converter code in bundled plugin-style
  submodules and register it the same way an external plugin would register;
- use facets for text encodings;
- include a regression test that converts ASCII emoticons such as `:)` to
  Unicode emoji, saves the result with correct text claims and encoding facets,
  and then selects an appropriate text reader;
- show that CSV-like data can be read in multiple ways, including bytes, text,
  and table;
- avoid bending the core registry design to make examples work;
- where appropriate, demonstrate claim/facet-based reader selection for data
  that is not catalogued;
- allow the registry to store and return runtime implementation objects, but do
  not make core invoke them implicitly;
- keep reader handle APIs for #118 and catalog materializer-result merge for
  #117.

## Deferred

This note intentionally leaves several choices to implementation:

- exact ranking rules beyond "specific request beats ambiguous request";
- trust and authentication policy for third-party plugin declarations;
- how `PluginRegistry` exposes capability contribution methods;
- the read-handle API that consumes selected reader implementations;
- the operation-materializer result model that persists produced claims/facets;
- optional integration with Intake pipelines or other external registries.
- a converter orchestration layer that passes opened handles or runtime values
  between readers, filters, and writer capabilities so chained conversions do
  not repeat reader/writer boilerplate.
