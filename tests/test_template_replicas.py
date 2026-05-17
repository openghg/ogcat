"""Template-link replica materialization tests."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ogcat import ArtifactLocator, CatalogRecord
from ogcat.template_replicas import materialize_template_link_replica


def _path_record(primary_path: Path) -> CatalogRecord:
    """Build a path-backed record for template-link materializer tests."""
    return CatalogRecord(
        catalog="files",
        time_added="2026-05-15T00:00:00Z",
        id="record-1",
        record_type="managed_file",
        locator=ArtifactLocator.path(
            primary_path,
            relative_path="data/objects/ab/abcdef.nc",
        ),
        original_filename="alpha.nc",
        suffixes=[".nc"],
        naming_metadata={"artifact_uuid": "abcdef"},
    )


def test_materialize_template_link_replica_records_metadata_and_relative_link(
    tmp_path: Path,
) -> None:
    """Template-link materialization returns metadata and a relative symlink."""
    root = tmp_path / "catalog"
    files_root = root / "data" / "files"
    primary_path = root / "data" / "objects" / "ab" / "abcdef.nc"
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    primary_path.write_text("payload", encoding="utf-8")
    record = _path_record(primary_path)
    expected_link_path = files_root / "2026" / "alpha" / "alpha.nc"

    materialized = materialize_template_link_replica(
        catalog_root=root,
        files_root=files_root,
        record=record,
        directory_template="{year_added}/{original_stem}",
        filename_template="{original_filename}",
    )

    assert materialized is not None
    assert materialized.primary_path == primary_path
    assert materialized.target_path == expected_link_path
    assert materialized.catalog_relative_path == "data/files/2026/alpha/alpha.nc"
    assert materialized.storage_relative_path == "2026/alpha/alpha.nc"
    assert materialized.resolved_directory == "2026/alpha"
    assert materialized.resolved_filename == "alpha.nc"
    assert materialized.naming_metadata["template_replica_path"] == str(expected_link_path)
    assert materialized.naming_metadata["template_replica_relative_path"] == "data/files/2026/alpha/alpha.nc"
    assert materialized.naming_metadata["template_replica_storage_relative_path"] == "2026/alpha/alpha.nc"
    assert materialized.naming_metadata["template_resolved_directory"] == "2026/alpha"
    assert materialized.naming_metadata["template_resolved_filename"] == "alpha.nc"
    assert expected_link_path.is_symlink()
    assert not Path(os.readlink(expected_link_path)).is_absolute()
    assert expected_link_path.resolve() == primary_path


def test_materialize_template_link_replica_registers_rollback(tmp_path: Path) -> None:
    """Template-link materialization registers rollback for the created symlink."""
    root = tmp_path / "catalog"
    files_root = root / "data" / "files"
    primary_path = root / "data" / "objects" / "ab" / "abcdef.nc"
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    primary_path.write_text("payload", encoding="utf-8")
    record = _path_record(primary_path)
    rollback_actions: list[Callable[[], None]] = []
    rollback_descriptions: list[str] = []

    def register_rollback(action: Callable[[], None], description: str) -> object:
        rollback_actions.append(action)
        rollback_descriptions.append(description)
        return action

    materialized = materialize_template_link_replica(
        catalog_root=root,
        files_root=files_root,
        record=record,
        directory_template="{year_added}/{original_stem}",
        filename_template="{original_filename}",
        register_rollback=register_rollback,
    )

    assert materialized is not None
    assert rollback_descriptions == [f"remove template symlink replica {materialized.target_path}"]
    assert materialized.target_path.is_symlink()

    rollback_actions[0]()

    assert not materialized.target_path.exists()
    assert not materialized.target_path.is_symlink()


def test_materialize_template_link_replica_skips_non_path_records(tmp_path: Path) -> None:
    """Template-link materialization skips records without local primary paths."""
    root = tmp_path / "catalog"
    files_root = root / "data" / "files"
    record = CatalogRecord(
        catalog="files",
        time_added="2026-05-15T00:00:00Z",
        id="record-1",
        record_type="managed_file",
        locator=ArtifactLocator(kind="uri", value="https://example.org/data.nc"),
        original_filename="alpha.nc",
        suffixes=[".nc"],
        naming_metadata={"artifact_uuid": "abcdef"},
    )

    def register_rollback(action: Callable[[], None], description: str) -> object:
        raise AssertionError("non-path records should not register rollback")

    materialized = materialize_template_link_replica(
        catalog_root=root,
        files_root=files_root,
        record=record,
        directory_template="{year_added}/{original_stem}",
        filename_template="{original_filename}",
        register_rollback=register_rollback,
    )

    assert materialized is None
    assert not (files_root / "2026" / "alpha" / "alpha.nc").exists()
