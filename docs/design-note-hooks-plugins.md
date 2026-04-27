# Hooks, Plugins, And Transactions

`ogcat` hooks let projects add domain-specific ingest behavior without adding that domain logic to
`ogcat` core. A plugin is just a Python object with one or more hook methods registered on a
`PluginRegistry` or `HookManager`.

Hooks are called in registration order. Most hook failures fail the catalog operation and use the
normal transaction rollback path. `after_commit` is different: it runs after the catalog operation
has already committed, so failures are reported as Python warnings rather than changing a successful
catalog write into an exception.

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
    def after_write_artifact(self, context: OperationContext) -> None:
        external_id = write_external_index(context.operation_id)

        context.rollback(
            lambda: delete_external_index(external_id),
            description=f"delete external index entry {external_id}",
        )
```

Rollback actions run in reverse registration order. They are best-effort compensating actions, not
database transactions.

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
- `plan_locator(context)`
- `before_write_artifact(context)`
- `after_write_artifact(context)`
- `extract_metadata(context)`
- `before_commit(context)`
- `after_commit(context)`
- `on_error(context, error)`
- `on_rollback(context, error)`

The context includes the catalog root, operation id, operation name, record type, user metadata,
derived metadata, planned locators, source information, storage mode, and rollback registration.
