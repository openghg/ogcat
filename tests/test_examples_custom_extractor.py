"""Smoke tests for the custom_extractor example."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "custom_extractor" / "scripts" / "run.py"


@pytest.fixture(scope="module")
def extractor_module() -> ModuleType:
    """Load the custom_extractor run module once per test session."""
    spec = importlib.util.spec_from_file_location("custom_extractor_example", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_exists() -> None:
    """The run.py script must be present in the expected location."""
    assert SCRIPT.is_file(), f"example script not found at {SCRIPT}"


def test_title_from_filename_sets_title(extractor_module: ModuleType, tmp_path: Path) -> None:
    """TitleFromFilenameHook sets title when the caller does not supply one."""
    from ogcat import Catalog, CatalogSpec, PluginRegistry

    source = tmp_path / "source" / "my_data_file.txt"
    source.parent.mkdir()
    source.write_text("content", encoding="utf-8")

    hook = extractor_module.TitleFromFilenameHook()
    plugins = PluginRegistry([hook])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test"), plugins=plugins)
    record = catalog.add_file(source, metadata={})

    assert record.user_metadata.get("title") == "my_data_file"


def test_title_from_filename_does_not_override(extractor_module: ModuleType, tmp_path: Path) -> None:
    """TitleFromFilenameHook does not override an explicitly supplied title."""
    from ogcat import Catalog, CatalogSpec, PluginRegistry

    source = tmp_path / "source" / "filename.txt"
    source.parent.mkdir()
    source.write_text("content", encoding="utf-8")

    hook = extractor_module.TitleFromFilenameHook()
    plugins = PluginRegistry([hook])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test"), plugins=plugins)
    record = catalog.add_file(source, metadata={"title": "Explicit Title"})

    assert record.user_metadata.get("title") == "Explicit Title"


def test_checksum_extractor_sets_sha256(extractor_module: ModuleType, tmp_path: Path) -> None:
    """ChecksumExtractor records a SHA-256 hash in derived_metadata."""
    import hashlib

    from ogcat import Catalog, CatalogSpec, PluginRegistry

    content = b"hello checksum"
    source = tmp_path / "source" / "probe.txt"
    source.parent.mkdir()
    source.write_bytes(content)

    hook = extractor_module.ChecksumExtractor()
    plugins = PluginRegistry([hook])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="test"), plugins=plugins)
    record = catalog.add_file(source, metadata={})

    expected = hashlib.sha256(content).hexdigest()
    assert record.derived_metadata.get("sha256") == expected


def test_run_creates_records_with_sha256(extractor_module: ModuleType, tmp_path: Path) -> None:
    """The run function creates records that all have a sha256 in derived_metadata."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    extractor_module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    records = catalog.search()
    assert len(records) == 3
    for rec in records:
        assert "sha256" in rec.derived_metadata, f"record {rec.id} missing sha256"
        assert len(str(rec.derived_metadata["sha256"])) == 64


def test_run_records_have_titles(extractor_module: ModuleType, tmp_path: Path) -> None:
    """All records produced by run() have a title in user_metadata."""
    from ogcat import Catalog

    catalog_root = tmp_path / "catalog"
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    extractor_module.run(catalog_root, source_dir)

    catalog = Catalog.open(catalog_root)
    for rec in catalog.search():
        assert "title" in rec.user_metadata, f"record {rec.id} missing title"
        assert rec.user_metadata["title"], f"record {rec.id} has empty title"


def test_main_runs_without_error(extractor_module: ModuleType) -> None:
    """The main() function completes successfully."""
    result = extractor_module.main()
    assert result == 0
