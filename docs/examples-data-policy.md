# Examples data policy

## What lives in ``examples/``

Each example is a self-contained directory:

```text
examples/
  data/
    README.md             this document
  local_file_catalog/
    README.md
    scripts/
      run.py
  bibdesk_mini/
    README.md
    data/
      refs.bib            small vendored BibTeX fixture
    scripts/
      run.py
  custom_extractor/
    README.md
    scripts/
      run.py
```

## Rules

- **ogcat is installed, not imported via path hack.**  No example modifies
  ``sys.path``.  Run ``pip install -e .`` first.

- **Vendored data is small.**  Files committed to the repository under
  ``examples/*/data/`` must be small enough to be comfortable in a Git
  repository.  A few kilobytes per fixture is acceptable; megabytes are not.
  When in doubt, generate the data in the example script.

- **Generated data is written to temporary directories.**  Example scripts
  write catalog databases and managed files under ``tempfile.mkdtemp()`` or
  ``tmp_path`` (in tests) and remove them when done.  No output is written
  under ``examples/`` at run time.

- **External downloads are optional.**  If an example can fetch data from the
  network, it must also work completely offline using bundled fixtures.

- **Large data lives outside the repository.**  Examples that require
  large files (hundreds of megabytes or more) should document where to
  obtain the data and fail gracefully with a clear message when it is absent.

- **Each example has one clear purpose.**  Keep examples focused.  If an
  example grows to cover multiple unrelated features, split it.

## Adding a new example

1. Create ``examples/<name>/README.md`` describing what the example shows and
   how to run it.
2. Put the runnable script at ``examples/<name>/scripts/run.py``.
3. Vendor only the data files that are necessary.  Prefer generating data in
   the script.
4. Add a smoke test in ``tests/test_examples_<name>.py`` that imports and runs
   the example without asserting exact human-readable output.
5. Reference the example from a docs tutorial page.
