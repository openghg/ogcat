# custom_extractor example

This example shows how to write custom ogcat hooks:

- a ``before_validate_metadata`` hook that fills in ``title`` from the source
  filename when the caller did not supply one
- an ``extract_metadata`` hook that computes a SHA-256 checksum and stores it
  in ``derived_metadata``

## Requirements

``ogcat`` must be installed:

```bash
uv sync
```

## Run

```bash
uv run python examples/custom_extractor/scripts/run.py
```

The script creates three small temporary text files, ingests them with both
hooks active, and prints a table showing the title and checksum for each
record.

## Purpose

- Demonstrate implementing ``BeforeValidateMetadataHook`` and
  ``ExtractMetadataHook``.
- Show how to wire hooks through ``PluginRegistry``.
- Show that hooks work without modifying ogcat core.

## Data

The example generates small text files at runtime.  No pre-existing data
files are required.
