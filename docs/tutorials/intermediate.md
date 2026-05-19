# Tutorial: intermediate topics

These tutorials are placeholders for focused guides that build on the basic
catalog workflow. They should stay small and runnable.

## Custom hooks and extractors

Use this level for examples that register hooks through `PluginRegistry`, fill
metadata before validation, collect derived metadata, or handle rollback
warnings. The existing runnable example is:

```bash
uv run python examples/custom_extractor/scripts/run.py
```

Future content should show:

- `before_validate_metadata` for metadata defaults
- `extract_metadata` for derived metadata
- `context.add_warning(...)` for non-fatal findings
- focused tests for hook outcomes

## Operation materializers

Use this level for examples that materialise data during `add_artifact()`, for
example writing an in-memory object or unpacking a zip archive with helpers from
`ogcat.writers`.

Future content should show:

- `memory_source()` with `memory_writer()`
- `path_source()` with `path_writer()`
- `UnzipArtifactWriter`
- rollback behavior when a materializer fails

## Validation patterns

Use this level for stricter catalog policies. Required metadata fields are
errors during ingest, while unknown metadata is accepted unless strict
validation is requested explicitly.

Future content should show:

- `validate_spec()` before writing a catalog spec
- `validate_metadata()` and `ValidationIssue.path`
- `allow_unknown_metadata=False` with explicit strict validation
- user-friendly error messages in CLIs or notebooks

## CLI parity

Use this level for shell-first workflows that mirror the Python API.

Future content should show:

- compact search expressions such as `species=CH4`, `title:methane`, and `field?`
- `--fields` plus `--format csv|tsv|pipe`
- `--json`, `--ids`, and `--paths` for automation
