"""Smoke test for installed ogcat source and wheel distributions."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

import ogcat
from ogcat import Catalog, CatalogSpec
from ogcat.cli import app


def main() -> None:
    """Exercise the public package, CLI app, and included metadata."""
    assert ogcat.__version__ == version("ogcat")

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "artifact.txt"
        source_path.write_text("hello from a built distribution\n")

        catalog = Catalog.create(temp_path / "catalog", CatalogSpec(catalog_name="smoke"))
        record = catalog.add_file(source_path, metadata={"title": "smoke"})
        matches = catalog.search(where={"title": "smoke"})
        stored_path = catalog.path(record.id)

        assert matches.ids == [record.id]
        assert stored_path is not None
        assert stored_path.read_text() == "hello from a built distribution\n"

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output


if __name__ == "__main__":
    main()
