"""Secondary artifact operation tests."""

from __future__ import annotations

import os
from pathlib import Path

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogRecord,
    CatalogSpec,
    OperationContext,
    OperationSource,
)
from ogcat.hooks import RollbackRegistrar
from ogcat.secondary_artifacts import TemplateLinkSecondaryArtifact


def _operation_context(root: Path, transaction_register: RollbackRegistrar) -> OperationContext:
    """Build a minimal operation context for secondary artifact tests."""
    return OperationContext(
        catalog_root=root,
        operation_id="operation-1",
        operation_type="add_file",
        record_type="managed_file",
        user_metadata={},
        derived_metadata={},
        register_rollback=transaction_register,
        source=OperationSource(kind="test"),
    )


def test_template_link_secondary_artifact_materializes_metadata_and_rollback(
    tmp_path: Path,
) -> None:
    """Template-link secondary operations create relative symlinks with rollback."""
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))
    primary_path = root / "data" / "objects" / "ab" / "abcdef.nc"
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    primary_path.write_text("payload", encoding="utf-8")
    record = CatalogRecord(
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
    operation = TemplateLinkSecondaryArtifact(
        catalog_root=root,
        files_root=root / "data" / "files",
        directory_template="{year_added}/{original_stem}",
        filename_template="{original_filename}",
    )
    expected_link_path = root / "data" / "files" / "2026" / "alpha" / "alpha.nc"

    with catalog.transaction() as transaction:
        result = operation.run(
            transaction,
            _operation_context(root, transaction.register_rollback),
            record,
        )
        assert result is not None
        link_path = Path(str(result.naming_metadata_updates["template_replica_path"]))
        assert link_path == expected_link_path
        assert result.role == "template_link"
        assert result.mode == "symlink"
        assert link_path.is_symlink()
        assert not Path(os.readlink(link_path)).is_absolute()
        assert link_path.resolve() == primary_path

    assert not expected_link_path.exists()
    assert not expected_link_path.is_symlink()


def test_template_link_secondary_artifact_skips_non_path_records(tmp_path: Path) -> None:
    """Template-link secondary operations skip records without local paths."""
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))
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
    operation = TemplateLinkSecondaryArtifact(
        catalog_root=root,
        files_root=root / "data" / "files",
        directory_template="{year_added}/{original_stem}",
        filename_template="{original_filename}",
    )
    expected_link_path = root / "data" / "files" / "2026" / "alpha" / "alpha.nc"

    with catalog.transaction() as transaction:
        result = operation.run(
            transaction,
            _operation_context(root, transaction.register_rollback),
            record,
        )

    assert result is None
    assert not expected_link_path.exists()
    assert not expected_link_path.is_symlink()
