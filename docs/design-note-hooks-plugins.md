# Hooks, Plugins, And Transactions

`ogcat` hooks let projects add domain-specific ingest behavior without adding that domain logic to
`ogcat` core. A plugin is just a Python object with one or more hook methods registered on a
`PluginRegistry` or `HookManager`.

Capability registration is a separate extension point. Lifecycle hooks observe
or mutate catalog operations; capability declarations describe typed readers,
writers, and converters that can be selected from artifact claims and facets.
A single plugin object may provide both, but the capability registry remains a
registration and lookup layer rather than another hook lifecycle.

Hooks are called in registration order. Most hook failures fail the catalog operation and use the
normal transaction rollback path. `after_commit` is different: it runs after the catalog operation
has already committed, so failures are reported as Python warnings rather than changing a successful
catalog write into an exception.

Terminology matters:

- a **record** is the catalog database entry;
- an **artifact** is the data object being catalogued;
- a **locator** describes where the artifact is or will be;
- an **operation** coordinates validation, locator resolution, optional artifact work, record writes,
  hooks, and rollback.

`Catalog.add_artifact(...)` is record-only by default: it records a locator for an artifact, but does
not write artifact data unless an artifact writer is explicitly supplied. `Catalog.add_file(...)` is a
bundled local-file operation: it resolves a path locator, copies or moves the source file, extracts
generic metadata, and writes the record. Future data-from-memory writes should be modeled as explicit
operations or artifact writers rather than as record-write hooks.

## Direct Registration

```python
from ogcat import Catalog, CatalogSpec, PluginRegistry
from ogcat.hooks import OperationContext


class FilenameMetadataPlugin:
    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.source_path is None:
            return
        context.user_metadata.setdefault("title", context.source_path.stem)


plugins = PluginRegistry([FilenameMetadataPlugin()])
catalog = Catalog.create("example-catalog", CatalogSpec(catalog_name="files"), plugins=plugins)
record = catalog.add_file("co2_example.nc")
```

Use `before_validate_metadata` for metadata defaults, normalisation, or light parsing that must run
before schema validation and naming templates.

## Validation Hooks

Hooks can add project-specific validation without changing `RecordSchema` or importing a project
package into `ogcat`:

```python
class SpeciesRequiredPlugin:
    def before_validate_metadata(self, context: OperationContext) -> None:
        if "species" not in context.user_metadata:
            raise ValueError("species is required for this catalog")
```

Raising from a pre-commit hook fails the add operation. If work has already been staged, the active
`UnitOfWork` runs rollback actions before the exception returns to the caller.

## Derived Metadata Warnings

Metadata discovery hooks can add warning-only findings and still allow ingest to succeed:

```python
from ogcat import HookWarning


class SoftFilenameParser:
    def extract_metadata(self, context: OperationContext) -> dict[str, object]:
        context.add_warning(
            HookWarning(
                hook_name="filename-parser",
                message="could not infer averaging period from filename",
                code="filename.missing_averaging_period",
            )
        )
        return {"filename_stem": context.source_path.stem if context.source_path else None}
```

Warnings recorded before commit are stored under `record.derived_metadata["hook_warnings"]`.

## Rollback Participation

Hooks that create external side effects should register cleanup work with `context.rollback()`:

```python
class ExternalIndexPlugin:
    def after_record_write(self, context: OperationContext) -> None:
        external_id = write_external_index(context.operation_id)

        context.rollback(
            lambda: delete_external_index(external_id),
            description=f"delete external index entry {external_id}",
        )
```

Rollback actions run in reverse registration order. They are best-effort compensating actions, not
database transactions.

## Writing Artifacts From Plugins

Artifact writers materialise data before the catalog record is written. They receive the active
`OperationContext`, an `OperationSource`, and the resolved target `ArtifactLocator`. Writers should
create the artifact, register rollback for anything they created, and add writer-derived metadata to
`context.derived_metadata`. `ogcat.writers` includes small helper writers for examples and lightweight
workflows; they are intentionally minimal wrappers, not a full pipeline system.

`add_artifact()` remains record-only unless a writer is explicitly supplied:

```python
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, memory_source, memory_writer


def write_text(data: object, target: Path) -> dict[str, object]:
    text = str(data)
    target.write_text(text, encoding="utf-8")
    return {"byte_count": target.stat().st_size}


catalog = Catalog.open("example-catalog")
record = catalog.add_artifact(
    record_type="generated_text",
    locator=ArtifactLocator.path(Path("example-catalog/files/generated/example.txt")),
    source=memory_source("hello", kind="text", descriptor="in-memory text"),
    artifact_writer=memory_writer(write_text, target_kind="file", source_kind="text"),
)
```

Writers are the right place for artifact creation such as copying, extracting, parsing, or
materialising data. Record hooks still surround catalog metadata and record persistence:
`before_record_write` and `after_record_write` should not perform data writes unless the hook object
is intentionally being used as an artifact writer.

`add_artifacts()` is equivalent to calling `add_artifact()` once per item. Each item runs the normal
hook, writer, and commit lifecycle independently; if a later item fails, earlier successful items
remain committed.

Internally, `Catalog` prepares an `AddOperationRequest` and delegates the ordered lifecycle to an
`AddOperationRunner`. The runner is not public API, but the boundary matters for plugin behavior:
hooks and artifact writers see the same `OperationContext`, hook ordering, rollback behavior, and
audit events whether the operation started from `add_file()` or `add_artifact()`. The generic
`OperationRunner` interface is intentionally broader than add operations so future operation
families can use the same command boundary without changing the public hook API.

The same wrapper pattern works for path-backed transforms:

```python
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, path_source, path_writer


def transform_netcdf(source: Path, target: Path) -> dict[str, object]:
    # For example: open with xarray, transform, and write a new NetCDF file.
    target.write_bytes(source.read_bytes())
    return {"transform": "copy-placeholder"}


catalog = Catalog.open("example-catalog")
record = catalog.add_artifact(
    record_type="processed_flux",
    locator=ArtifactLocator.path(Path("example-catalog/files/processed/output.nc")),
    source=path_source("incoming/input.nc", kind="netcdf_file"),
    artifact_writer=path_writer(transform_netcdf, target_kind="file", source_kind="netcdf_file"),
)
```

### Unzip Writer Example

An unzip-style writer can take a zip file source, write an extracted directory artifact, and record
what it extracted. The bundled example validates archive member paths before writing so entries such
as `../escape.txt` cannot leave the target directory:

```python
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, UnzipArtifactWriter, path_source

catalog = Catalog.open("example-catalog")
record = catalog.add_artifact(
    record_type="zip_directory",
    locator=ArtifactLocator.path(Path("example-catalog/files/extracted/example")),
    source=path_source("incoming/example.zip", kind="zip_file"),
    artifact_writer=UnzipArtifactWriter(),
)
```

## Composed Transactions

Callers can pass a `UnitOfWork` to compose multiple catalog operations. In this mode the caller owns
commit and rollback decisions:

```python
from ogcat import ArtifactLocator

with catalog.transaction() as transaction:
    first = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/first.zarr"),
        transaction=transaction,
    )
    second = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/second.zarr"),
        transaction=transaction,
    )

    transaction.commit()
```

If a hook fails inside a caller-owned transaction, `add_artifact(..., transaction=transaction)` raises
but does not immediately roll back the whole transaction. The caller can inspect state, add
diagnostics, roll back, or let the transaction context manager roll back on exit.

## Lifecycle Points

The initial hook surface is intentionally small:

- `before_validate_metadata(context)`
- `after_validate_metadata(context, report)`
- `resolve_artifact_locator(context)`
- `before_record_write(context)`
- `after_record_write(context)`
- `extract_metadata(context)`
- `before_commit(context)`
- `after_commit(context)`
- `on_error(context, error)`
- `on_rollback(context, error)`

Hooks exert their effect by mutating `OperationContext`, raising an exception, or registering
rollback work:

- mutate `user_metadata` before validation to add defaults or normalise caller input;
- mutate `planned_locators` during `resolve_artifact_locator`; the first locator is canonical;
- inspect `source` during artifact writing and metadata extraction;
- return or add `derived_metadata` during `extract_metadata`;
- call `context.rollback(...)` after creating external side effects.

The context includes the catalog root, operation id, operation type, record type, user metadata,
derived metadata, planned locators, source information, storage mode, and rollback registration.
`context.source_path` and `context.source_descriptor` remain compatibility shims over
`context.source.path` and `context.source.descriptor`.

## Relationship To Capability Declarations

The #119 capability registry builds on the plugin ergonomics described here
without merging lifecycle hooks and typed artifact access. `PluginRegistry`
continues to collect hook objects for operation lifecycle points. A
`CapabilityRegistry` collects declarations for readers, writers, and
converters: what input claims or interfaces they accept, what output claims or
interfaces they produce, and any plugin-owned option metadata.

Bundled capabilities and external capabilities should register the same way.
For example, dependency-light bundled examples for bytes, text with encoding
facets, CSV/table access, JSON access, and an emoticon-to-emoji converter can
ship as plugin-style modules. A playful text-to-text converter such as Pig
Latin can also exercise the boundary: a CSV-like artifact may be selected as
text input, but that converter still cannot advertise CSV/table output.
Scientific readers such as xarray, pandas, Zarr, NetCDF, HDF5, Intake, or
OpenGHG-specific integrations stay optional and should not become core imports
merely because the registry can describe them.

Capability lookup dispatches by `ArtifactDescriptor` claims and facets, not by
`record_type`. It also requires explicit selection: if an artifact can be read
as bytes, text, and table, callers must ask for the desired interface before
the registry returns one selected capability.
