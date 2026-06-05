# Releasing

``ogcat`` uses calendar-free ``0.x.y`` releases while the API is developing
rapidly. Keep the major version at ``0`` for now. Use a minor bump for useful
new behavior and a patch bump for fixes, packaging updates, and small
compatibility changes.

The package uses the Apache License 2.0, matching OpenGHG.

## One-Time Setup

The release workflow publishes through PyPI Trusted Publishing, so there is no
PyPI token or GitHub secret to configure.

### GitHub Environment

Create the environment referenced by the release workflow:

1. Open ``https://github.com/openghg/ogcat``.
2. Go to ``Settings`` -> ``Environments`` -> ``New environment``.
3. Name the environment ``pypi``.
4. Optionally add required reviewers. This is useful because publishing is
   triggered by tags.
5. Optionally restrict deployments to tags matching ``v0.*``.

The workflow does not need environment secrets.

### PyPI Trusted Publisher

If ``ogcat`` already exists on PyPI, open the project and add a publisher:

1. Go to ``https://pypi.org/manage/projects/``.
2. Open the ``ogcat`` project.
3. Go to ``Publishing``.
4. Add a GitHub Actions trusted publisher with these values:

   - Owner: ``openghg``
   - Repository: ``ogcat``
   - Workflow name: ``release.yml``
   - Environment name: ``pypi``

If ``ogcat`` does not exist on PyPI yet, create a pending publisher instead:

1. Go to ``https://pypi.org/manage/account/publishing/``.
2. Add a new pending GitHub Actions publisher.
3. Use the same fields above, plus project name ``ogcat``.

Pending publishers do not reserve the project name until the first successful
publish.

## Release Commands

Start from a clean main branch, then choose one bump:

```bash
uv version --bump patch --no-sync
uv version --bump minor --no-sync
```

Check what changed:

```bash
uv version
git diff pyproject.toml uv.lock
```

Run the release checks:

```bash
uv sync --locked --group dev
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pyright
uv build --no-sources
uv run --isolated --no-project --with dist/*.whl tests/smoke_installed_package.py
uv run --isolated --no-project --with dist/*.tar.gz tests/smoke_installed_package.py
uv publish --dry-run --trusted-publishing never
```

Commit, tag, and push:

```bash
VERSION="$(uv version --short)"
git add pyproject.toml uv.lock
git commit -m "Release v${VERSION}"
git tag -a "v${VERSION}" -m "v${VERSION}"
git push origin main --follow-tags
```

Tags matching ``v0.*`` trigger the release workflow. The workflow rebuilds the
source distribution and wheel, smoke-tests both artifacts, then publishes with
``uv publish`` through PyPI Trusted Publishing.

## TestPyPI

For a dry run against TestPyPI, configure a matching TestPyPI trusted publisher
and publish the locally built files with:

```bash
uv build --no-sources
uv publish --index testpypi --trusted-publishing always
```

## Editable Installs

Frequent PyPI releases make normal installs easier, but editable installs are
still useful for active local changes:

```bash
uv add --editable /path/to/ogcat
```

Use editable installs when you need uncommitted behavior immediately. Use PyPI
releases for reproducible project environments and updates that should move
between machines.

## References

- [uv GitHub Actions publishing guide](https://docs.astral.sh/uv/guides/integration/github/#publishing-to-pypi)
- [PyPI Trusted Publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
- [PyPI Trusted Publishing security notes](https://docs.pypi.org/trusted-publishers/security-model/)
- [GitHub environment setup](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
