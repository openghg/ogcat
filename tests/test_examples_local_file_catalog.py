"""Smoke tests for the local_file_catalog example."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ogcat import Catalog

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "local_file_catalog" / "scripts" / "run.py"


@pytest.fixture(scope="module")
def local_file_catalog_module() -> ModuleType:
    """Load the local_file_catalog run module once for this test module."""
    spec = importlib.util.spec_from_file_location("local_file_catalog_example", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_exists() -> None:
    """The run.py script must be present in the expected location."""
    assert SCRIPT.is_file(), f"example script not found at {SCRIPT}"


def test_build_spec_has_required_fields(local_file_catalog_module: ModuleType) -> None:
    """The catalog spec must declare required metadata fields."""
    catalog_spec = local_file_catalog_module._build_spec()
    required = catalog_spec.default_schema.required_field_names()
    assert "site" in required
    assert "species" in required


def test_run_creates_records(local_file_catalog_module: ModuleType, tmp_path: Path) -> None:
    """The run function creates the expected number of records."""
    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    local_file_catalog_module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    records = catalog.search()
    assert len(records) == 3

    species_values = {str(r.user_metadata["species"]) for r in records}
    assert species_values == {"CH4", "CO2"}


def test_run_search_returns_correct_subsets(local_file_catalog_module: ModuleType, tmp_path: Path) -> None:
    """The catalog supports the searches demonstrated in the example."""
    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    local_file_catalog_module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    ch4 = catalog.search(where={"species": "CH4"})
    assert len(ch4) == 2

    mhd_2023 = catalog.search(where={"site": "MHD", "year": 2023})
    assert len(mhd_2023) == 2


def test_main_runs_without_error(local_file_catalog_module: ModuleType) -> None:
    """The main() function completes successfully."""
    result = local_file_catalog_module.main()
    assert result == 0
