# AGENTS.md

## Project setup

- Prefer `uv` for all Python environment and dependency management.
- Use the project-local virtual environment at `.venv/`.
- Do not silently use a global Python, system Python, conda environment, or ad-hoc `pip install`.
- If `.venv/` is missing, stop and ask whether to run `uv sync`.
- If imports fail because optional dependencies are missing, inspect `pyproject.toml` for extras and ask which extras to install rather than inventing a workaround.
- Run Python commands through uv, for example:
  - `uv run python ...`
  - `uv run pytest ...`
  - `uv run ruff check ...`
  - `uv run mypy ...`

## Development workflow

- Make small, focused changes.
- Preserve the existing public API unless explicitly asked to change it.
- Prefer readability over clever abstraction.
- Use `pathlib` for filesystem paths.
- Follow PEP naming conventions.
- Follow Google Python style when in doubt.
- Use type hints for new or changed public Python code.

## Docstrings

- Public modules, classes, functions, and methods should have Google-style docstrings.
- Private helpers should have at least a one-line docstring.
- If a private helper has several arguments, non-obvious behavior, or important return values, use a full Google-style docstring.

## Checks

For changed Python code, run:

;;;
uv run ruff check <changed files>
uv run ruff format --check <changed files>
;;;

If formatting is needed, run:

;;;
uv run ruff format <changed files>
;;;

Then run the most appropriate available type checker. Prefer the tool already configured in the repo:

;;;
uv run mypy ...
uv run pyright ...
uv run ty check ...
;;;

Use whichever of these is available and configured. Do not add a new type checker without asking.

## Tests

- New tests should include a short docstring describing the behavior or
  regression they cover.
- Run focused tests while developing.
- Before pushing or opening a PR, run the full test suite if it is reasonably quick.
- If the full suite takes more than about a minute, use judgement: run the relevant subset and explain what was not run.
- Prefer:

;;;
uv run pytest
;;;

or a focused command such as:

;;;
uv run pytest tests/path/to/test_file.py -q
;;;

## Git hygiene

- Do not commit unless explicitly asked.
- Do not push unless explicitly asked.
- Before finishing, summarize:
  - files changed,
  - checks run,
  - checks not run,
  - any assumptions or follow-up needed.

## When blocked

If environment setup, missing extras, failing credentials, missing data, or unavailable services block progress, stop and report the blocker. Do not create mock replacements or bypass project tooling unless explicitly asked.

## Scientific/data code

- Preserve coordinates, dimensions, metadata, and attrs unless the task requires changing them.
- Prefer xarray-native operations for labelled arrays.
- Avoid eager `.values`/NumPy conversion unless there is a clear reason.
- Keep units explicit.
