# Hooks and plugins

Hooks let you add domain-specific behaviour to the catalog operation lifecycle
without modifying ogcat core.

## Hook protocols

Each hook is a plain Python class that implements one or more of the
following methods.  You only need to implement the methods you care about.

| Method | When it runs |
|--------|-------------|
| ``before_validate_metadata(context)`` | Before schema validation. Mutate ``context.user_metadata`` here. |
| ``after_validate_metadata(context, report)`` | After schema validation. Inspect the validation report. |
| ``resolve_artifact_locator(context)`` | After the locator is proposed. Replace or extend ``context.planned_locators``. |
| ``extract_metadata(context)`` | During derived metadata collection. Return a dict to merge into ``context.derived_metadata``. |
| ``before_record_write(context)`` | Just before writing the record. Last chance to mutate metadata. |
| ``after_record_write(context)`` | Just after the record is written. Register side-effects here. |
| ``before_commit(context)`` | Before committing the transaction. |
| ``after_commit(context)`` | After committing the transaction (errors here become warnings). |
| ``on_error(context, error)`` | When the operation fails. |
| ``on_rollback(context, error)`` | After rollback has run. |

## Registering hooks

Pass a plugin registry when creating or opening a catalog:

```python
from ogcat import Catalog, CatalogSpec, PluginRegistry
from ogcat.hooks import OperationContext


class TitleFromFilenameHook:
    """Set title from the source filename when the caller did not supply one."""

    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.source_path is not None:
            context.user_metadata.setdefault("title", context.source_path.stem)


spec = CatalogSpec(catalog_name="files")
plugins = PluginRegistry([TitleFromFilenameHook()])
catalog = Catalog.create("./my-catalog", spec, plugins=plugins)
```

You can also register hooks after catalog creation:

```python
catalog.hook_manager.register(TitleFromFilenameHook())
```

## Metadata extractor hooks

Implement ``extract_metadata`` to add derived metadata:

```python
class ChecksumHook:
    """Compute a SHA-256 checksum and store it in derived metadata."""

    def extract_metadata(self, context: OperationContext) -> dict | None:
        if context.source_path is None:
            return None
        import hashlib
        digest = hashlib.sha256(context.source_path.read_bytes()).hexdigest()
        return {"sha256": digest}
```

## OperationContext

``OperationContext`` is the mutable object passed to every hook.  The most
commonly used fields are:

- ``context.user_metadata`` — mutable before validation
- ``context.derived_metadata`` — mutable during extraction
- ``context.source_path`` — source path when available
- ``context.planned_locators`` — list of locators to be resolved
- ``context.operation_type`` — name of the current operation

## Warnings

Hooks can record non-fatal warnings with ``context.add_warning()``:

```python
context.add_warning("title was derived from filename", hook_name="TitleFromFilenameHook")
```

Warnings appear in ``after_commit`` errors and are attached to the operation
context.  They do not prevent the record from being written.

## A complete example

See the [intermediate tutorial](../tutorials/intermediate.md) for a full worked
example including a custom extractor hook and smoke tests.
