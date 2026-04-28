"""Smoke tests for the local_file_catalog example."""

from __future__ import annotations

from pathlib import Path

from ogcat import Catalog

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "local_file_catalog" / "scripts" / "run.py"


def test_script_exists() -> None:
    """The run.py script must be present in the expected location."""
    assert SCRIPT.is_file(), f"example script not found at {SCRIPT}"


def test_build_spec_has_required_fields() -> None:
    """The catalog spec must declare required metadata fields."""
    import importlib.util
    import sys

    spec_obj = importlib.util.spec_from_file_location("local_file_catalog_example", SCRIPT)
    assert spec_obj is not None
    module = importlib.util.module_from_spec(spec_obj)
    assert spec_obj.loader is not None
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)

    catalog_spec = module._build_spec()
    required = catalog_spec.default_schema.required_field_names()
    assert "site" in required
    assert "species" in required


def test_run_creates_records(tmp_path: Path) -> None:
    """The run function creates the expected number of records."""
    import importlib.util
    import sys

    spec_obj = importlib.util.spec_from_file_location("local_file_catalog_example_run", SCRIPT)
    assert spec_obj is not None
    module = importlib.util.module_from_spec(spec_obj)
    assert spec_obj.loader is not None
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)

    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    records = catalog.search()
    assert len(records) == 3

    species_values = {str(r.user_metadata["species"]) for r in records}
    assert species_values == {"CH4", "CO2"}


def test_run_search_returns_correct_subsets(tmp_path: Path) -> None:
    """The catalog supports the searches demonstrated in the example."""
    import importlib.util
    import sys

    spec_obj = importlib.util.spec_from_file_location("local_file_catalog_example_search", SCRIPT)
    assert spec_obj is not None
    module = importlib.util.module_from_spec(spec_obj)
    assert spec_obj.loader is not None
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)

    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    ch4 = catalog.search(where={"species": "CH4"})
    assert len(ch4) == 2

    mhd_2023 = catalog.search(where={"site": "MHD", "year": 2023})
    assert len(mhd_2023) == 2


def test_main_runs_without_error() -> None:
    """The main() function completes successfully."""
    import importlib.util
    import sys

    spec_obj = importlib.util.spec_from_file_location("local_file_catalog_example_main", SCRIPT)
    assert spec_obj is not None
    module = importlib.util.module_from_spec(spec_obj)
    assert spec_obj.loader is not None
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)

    result = module.main()
    assert result == 0
