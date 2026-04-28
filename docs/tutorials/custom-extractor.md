# Tutorial: custom extractor hook

This tutorial shows how to write a custom ``extract_metadata`` hook that
adds derived metadata during file ingest, and a ``before_validate_metadata``
hook that fills in missing metadata fields automatically.

The full runnable script is at ``examples/custom_extractor/scripts/run.py``.

## What this example demonstrates

- Implementing :class:`ogcat.hooks.ExtractMetadataHook` to add derived metadata.
- Implementing :class:`ogcat.hooks.BeforeValidateMetadataHook` to set defaults.
- Registering hooks with a :class:`ogcat.PluginRegistry`.
- Writing and testing a plugin without modifying ogcat core.

## The hooks

### Checksum extractor

Computes a SHA-256 hash of the ingested file and stores it in
``derived_metadata["sha256"]``.

```python
import hashlib
from ogcat.hooks import OperationContext


class ChecksumExtractor:
    """Compute SHA-256 and record it in derived metadata."""

    def extract_metadata(self, context: OperationContext) -> dict | None:
        if context.source_path is None or not context.source_path.is_file():
            return None
        digest = hashlib.sha256(context.source_path.read_bytes()).hexdigest()
        return {"sha256": digest}
```

### Title-from-filename hook

Sets ``title`` in ``user_metadata`` when the caller did not supply one.

```python
class TitleFromFilenameHook:
    """Use the source filename stem as the record title when no title is given."""

    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.source_path is not None:
            context.user_metadata.setdefault("title", context.source_path.stem)
```

## Wiring it together

```python
from ogcat import Catalog, CatalogSpec, PluginRegistry

plugins = PluginRegistry([
    TitleFromFilenameHook(),
    ChecksumExtractor(),
])
catalog = Catalog.create("/tmp/extractor-demo", CatalogSpec(catalog_name="demo"), plugins=plugins)
```

## Checking the result

```python
record = catalog.add_file(Path("sample.txt"), metadata={})
print(record.user_metadata["title"])          # "sample" (from filename)
print(record.derived_metadata["sha256"])      # hex digest
```

## Running the example script

```bash
python examples/custom_extractor/scripts/run.py
```

The script creates three small temporary files, ingests them with both hooks
active, and prints a table showing the derived metadata.

## Testing your hooks

See ``tests/test_examples_custom_extractor.py`` for an example of how to
smoke-test a custom hook.  The key principle is to test observable outcomes
(metadata fields are set, records are searchable) rather than asserting exact
output strings.
