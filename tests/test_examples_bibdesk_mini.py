"""Smoke tests for the bibdesk_mini example."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "bibdesk_mini" / "scripts" / "run.py"
BIB_DATA = Path(__file__).resolve().parents[1] / "examples" / "bibdesk_mini" / "data" / "refs.bib"


@pytest.fixture(scope="module")
def bibdesk_module() -> ModuleType:
    """Load the bibdesk_mini run module once for this test module."""
    spec = importlib.util.spec_from_file_location("bibdesk_mini_example", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_exists() -> None:
    """The run.py script must be present in the expected location."""
    assert SCRIPT.is_file(), f"example script not found at {SCRIPT}"


def test_fixture_exists() -> None:
    """The BibTeX fixture must be present in the expected location."""
    assert BIB_DATA.is_file(), f"fixture not found at {BIB_DATA}"


def test_parse_bibtex_returns_all_entries(bibdesk_module: ModuleType) -> None:
    """parse_bibtex returns one dictionary per entry in the fixture."""
    entries = bibdesk_module.parse_bibtex(BIB_DATA)
    assert len(entries) == 4


def test_parse_bibtex_entry_has_expected_fields(bibdesk_module: ModuleType) -> None:
    """Each parsed entry has title, author, and year."""
    entries = bibdesk_module.parse_bibtex(BIB_DATA)
    for entry in entries:
        assert "title" in entry, f"entry {entry.get('key')} missing title"
        assert "author" in entry, f"entry {entry.get('key')} missing author"
        assert "year" in entry, f"entry {entry.get('key')} missing year"


def test_run_creates_records(bibdesk_module: ModuleType, tmp_path: Path) -> None:
    """The run function creates one record per BibTeX entry."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    bibdesk_module.run(catalog_root, BIB_DATA)

    catalog = Catalog.open(catalog_root)
    records = catalog.search()
    assert len(records) == 4


def test_run_search_by_year(bibdesk_module: ModuleType, tmp_path: Path) -> None:
    """Records can be searched by publication year."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    bibdesk_module.run(catalog_root, BIB_DATA)

    catalog = Catalog.open(catalog_root)
    papers_2019 = catalog.search(where={"year": 2019})
    assert len(papers_2019) == 2


def test_run_search_contains_title(bibdesk_module: ModuleType, tmp_path: Path) -> None:
    """Case-insensitive title search returns matching records."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    bibdesk_module.run(catalog_root, BIB_DATA)

    catalog = Catalog.open(catalog_root)
    carbon = catalog.search(contains={"title": "carbon"}, ignore_case=True)
    assert len(carbon) >= 1


def test_run_search_by_tag(bibdesk_module: ModuleType, tmp_path: Path) -> None:
    """Records tagged 'review' can be found via list membership search."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    bibdesk_module.run(catalog_root, BIB_DATA)

    catalog = Catalog.open(catalog_root)
    reviews = catalog.search(contains={"tags": "review"})
    assert len(reviews) >= 1


def test_main_runs_without_error(bibdesk_module: ModuleType) -> None:
    """The main() function completes successfully."""
    result = bibdesk_module.main()
    assert result == 0
