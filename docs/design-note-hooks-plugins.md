# Hooks, Plugins, And Transactions

`ogcat` hooks let projects add domain-specific ingest behavior without adding that domain logic to
`ogcat` core. A plugin is just a Python object with one or more hook methods registered on a
`PluginRegistry` or `HookManager`.

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

`Catalog.add_artifact(...)` is record-only in this version: it records a locator for an artifact, but
does not write artifact data. `Catalog.add_file(...)` is a bundled local-file operation: it resolves a
path locator, copies or moves the source file, extracts generic metadata, and writes the record.
Future data-from-memory writes should be modeled as explicit operations or artifact writers rather
than as record-write hooks.

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
`context.derived_metadata`.

`add_artifact()` remains record-only unless a writer is explicitly supplied:

```python
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, OperationSource
from ogcat.hooks import OperationContext


class TextWriter:
    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        target_path = target.as_path()
        if target_path is None:
            raise ValueError("text writer requires a path locator")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(str(source.metadata["text"]), encoding="utf-8")
        context.rollback(lambda: target_path.unlink(missing_ok=True), description="remove text artifact")
        context.derived_metadata["byte_count"] = target_path.stat().st_size


catalog = Catalog.open("example-catalog")
record = catalog.add_artifact(
    record_type="generated_text",
    locator=ArtifactLocator.path(Path("example-catalog/files/generated/example.txt")),
    source=OperationSource(kind="text", descriptor="in-memory text", metadata={"text": "hello"}),
    artifact_writer=TextWriter(),
)
```

Writers are the right place for artifact creation such as copying, extracting, parsing, or
materialising data. Record hooks still surround catalog metadata and record persistence:
`before_record_write` and `after_record_write` should not perform data writes unless the hook object
is intentionally being used as an artifact writer.

### Unzip Writer Example

An unzip-style writer can take a zip file source, write an extracted directory artifact, and record
what it extracted:

```python
import shutil
import zipfile
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, OperationSource
from ogcat.hooks import OperationContext


class UnzipWriter:
    def write(
        self,
        context: OperationContext,
        source: OperationSource,
        target: ArtifactLocator,
    ) -> None:
        if source.kind != "zip_file" or source.path is None:
            raise ValueError("unzip writer requires OperationSource(kind='zip_file', path=...)")
        target_dir = target.as_path()
        if target_dir is None:
            raise ValueError("unzip writer requires a path locator")

        target_dir.mkdir(parents=True, exist_ok=True)
        target_root = target_dir.resolve()
        with zipfile.ZipFile(source.path) as archive:
            names = sorted(archive.namelist())
            for member in archive.infolist():
                destination = (target_dir / member.filename).resolve()
                if destination != target_root and target_root not in destination.parents:
                    raise ValueError(f"zip member escapes target directory: {member.filename}")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, destination.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)

        context.rollback(
            lambda: shutil.rmtree(target_dir, ignore_errors=True),
            description=f"remove extracted directory {target_dir}",
        )
        context.derived_metadata["extracted_file_count"] = len(names)
        context.derived_metadata["extracted_names"] = names


catalog = Catalog.open("example-catalog")
record = catalog.add_artifact(
    record_type="zip_directory",
    locator=ArtifactLocator.path(Path("example-catalog/files/extracted/example")),
    source=OperationSource(
        kind="zip_file",
        path=Path("incoming/example.zip"),
        descriptor="incoming/example.zip",
    ),
    artifact_writer=UnzipWriter(),
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
