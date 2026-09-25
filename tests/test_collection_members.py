"""Tests for local collection member enumeration."""

from pathlib import Path

import pytest

from ogcat import Catalog, CatalogSpec


def _catalog(tmp_path: Path) -> Catalog:
    """Create a catalog for collection member tests."""
    return Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="members"))


def test_member_paths_lists_current_matching_files_and_directories(tmp_path: Path) -> None:
    """Members are sorted and enumerated when requested, including directory datasets."""
    root = tmp_path / "series"
    root.mkdir()
    catalog = _catalog(tmp_path)
    record = catalog.add_collection(root, collection_pattern="*.zarr")
    (root / "b.zarr").mkdir()
    (root / "a.zarr").mkdir()
    (root / "ignored.nc").touch()

    assert catalog.member_paths(record.id) == [root / "a.zarr", root / "b.zarr"]


def test_member_paths_supports_nested_patterns(tmp_path: Path) -> None:
    """A stored relative pattern can select members below the collection root."""
    root = tmp_path / "series"
    nested = root / "nested"
    nested.mkdir(parents=True)
    match = nested / "co2_202401.nc"
    match.touch()
    catalog = _catalog(tmp_path)
    record = catalog.add_collection(root, collection_pattern="nested/co2_*.nc")

    assert catalog.member_paths(record.id) == [match]


def test_member_paths_requires_active_local_collection(tmp_path: Path) -> None:
    """Member enumeration rejects missing, ordinary, deleted, and remote records."""
    root = tmp_path / "series"
    root.mkdir()
    catalog = _catalog(tmp_path)
    ordinary = catalog.add_reference(root)
    collection = catalog.add_collection(root)
    remote = catalog.add_collection(uri="s3://bucket/series", collection_pattern="*.nc")

    with pytest.raises(KeyError, match="does not exist"):
        catalog.member_paths("missing")
    with pytest.raises(ValueError, match="not an active collection"):
        catalog.member_paths(ordinary.id)
    with pytest.raises(NotImplementedError, match="local path"):
        catalog.member_paths(remote.id)
    catalog.delete(collection.id)
    with pytest.raises(ValueError, match="not an active collection"):
        catalog.member_paths(collection.id)


def test_member_paths_requires_existing_root(tmp_path: Path) -> None:
    """A removed collection directory produces an explicit error."""
    root = tmp_path / "series"
    root.mkdir()
    catalog = _catalog(tmp_path)
    record = catalog.add_collection(root)
    root.rmdir()

    with pytest.raises(FileNotFoundError, match="Collection root"):
        catalog.member_paths(record.id)


def test_member_paths_rejects_escaping_symlinks(tmp_path: Path) -> None:
    """A matching symlink cannot make enumeration escape its collection root."""
    root = tmp_path / "series"
    root.mkdir()
    outside = tmp_path / "outside.nc"
    outside.touch()
    (root / "linked.nc").symlink_to(outside)
    catalog = _catalog(tmp_path)
    record = catalog.add_collection(root, collection_pattern="*.nc")

    with pytest.raises(ValueError, match="escapes its root"):
        catalog.member_paths(record.id)


def test_member_paths_revalidates_stored_pattern(tmp_path: Path) -> None:
    """Corrupt or manually edited record metadata cannot escape the root."""
    root = tmp_path / "series"
    root.mkdir()
    catalog = _catalog(tmp_path)
    record = catalog.add_collection(root, collection_pattern="*.nc")
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)
    classification["collection_pattern"] = "../*.nc"
    catalog.repository.update(record)

    with pytest.raises(ValueError, match="collection_pattern"):
        catalog.member_paths(record.id)
