"""Replica view planning and symlink application tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ogcat import (
    Catalog,
    CatalogRecord,
    CatalogSpec,
    ReplicaState,
)
from ogcat.replicas import plan_replica_view


def _record_id(record: CatalogRecord) -> str:
    """Return a persisted record id for test setup."""
    assert record.id is not None
    return record.id


def _source_file(tmp_path: Path, name: str, text: str = "payload") -> Path:
    """Create a source file for replica tests."""
    source = tmp_path / "source" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return source


def test_plan_view_does_not_create_links(tmp_path: Path) -> None:
    """Planning a view is a dry run with no filesystem side effects."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    view_root = tmp_path / "view"

    plan = plan_replica_view(
        root=view_root,
        template="{product}/{id}_{original_filename}",
        records=[record],
    )

    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.state == ReplicaState.PLANNED
    assert item.source_path == record.path()
    assert item.target_path == view_root / "flux" / f"{_record_id(record)}_alpha.nc"
    assert not view_root.exists()


def test_apply_symlink_view_creates_links_and_is_idempotent(tmp_path: Path) -> None:
    """Applying a symlink view creates links and re-apply reports them up to date."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    primary_path = record.path()
    assert primary_path is not None

    plan = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}_{original_filename}",
        records=[record],
    )
    result = plan.apply()
    link_path = result.created[0].target_path

    assert link_path.is_symlink()
    assert not Path(os.readlink(link_path)).is_absolute()
    assert link_path.resolve() == primary_path

    second_result = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}_{original_filename}",
        records=[record],
    ).apply()

    assert second_result.created == []
    assert second_result.up_to_date[0].target_path == link_path


def test_apply_skip_errors_reports_symlink_oserror_as_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skip_errors=True reports symlink failures in both skipped and errors."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    plan = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}_{original_filename}",
        records=[record],
    )

    def fail_symlink_to(
        self: Path,
        target: str | Path,
        target_is_directory: bool = False,
    ) -> None:
        raise OSError("simulated symlink failure")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink_to)

    with pytest.raises(ValueError, match="simulated symlink failure"):
        plan.apply()
    result = plan.apply(skip_errors=True)

    assert result.skipped[0].state == ReplicaState.ERROR
    assert result.errors[0].state == ReplicaState.ERROR


def test_view_reports_template_collisions(tmp_path: Path) -> None:
    """Plans report duplicate rendered paths before creating any links."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    first = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    second = catalog.add_file(_source_file(tmp_path, "beta.nc"), metadata={"product": "flux"})
    view_root = tmp_path / "view"

    plan = plan_replica_view(root=view_root, template="same-name.nc", records=[first, second])

    assert len(plan.collisions) == 2
    with pytest.raises(ValueError, match="same-name.nc"):
        plan.apply()
    assert not view_root.exists()


def test_view_reports_unsupported_non_path_locators(tmp_path: Path) -> None:
    """URI records are reported as unsupported for local symlink views."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_reference(uri="https://example.org/data.nc", metadata={"product": "remote"})

    plan = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}.nc",
        records=[record],
    )

    assert len(plan.unsupported) == 1
    assert plan.unsupported[0].state == ReplicaState.UNSUPPORTED
    with pytest.raises(ValueError, match="not path-backed"):
        plan.apply()
    result = plan.apply(skip_errors=True)
    assert result.skipped[0].state == ReplicaState.UNSUPPORTED


def test_view_reports_missing_primary_targets(tmp_path: Path) -> None:
    """Missing primary paths are reported and can be skipped."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    primary_path = record.path()
    assert primary_path is not None
    primary_path.unlink()

    plan = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}.nc",
        records=[record],
    )

    assert len(plan.missing_targets) == 1
    with pytest.raises(ValueError, match="Primary target does not exist"):
        plan.apply()
    result = plan.apply(skip_errors=True)
    assert result.skipped[0].state == ReplicaState.MISSING_TARGET


def test_apply_rechecks_missing_primary_targets_after_planning(tmp_path: Path) -> None:
    """Apply should not create a broken link when a planned primary disappears."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "flux"})
    primary_path = record.path()
    assert primary_path is not None
    plan = plan_replica_view(
        root=tmp_path / "view",
        template="{product}/{id}.nc",
        records=[record],
    )
    target_path = plan.items[0].target_path
    primary_path.unlink()

    with pytest.raises(ValueError, match="Primary target does not exist"):
        plan.apply()

    assert not target_path.exists()
    assert not target_path.is_symlink()

    result = plan.apply(skip_errors=True)
    assert result.skipped[0].state == ReplicaState.MISSING_TARGET
    assert not target_path.exists()
    assert not target_path.is_symlink()


def test_plan_view_rejects_normalized_parent_segments(tmp_path: Path) -> None:
    """Whitespace-padded parent-directory segments must not escape the view root."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": " .. "})

    with pytest.raises(ValueError, match="relative path below the view root"):
        plan_replica_view(
            root=tmp_path / "view",
            template="{product}/{id}.nc",
            records=[record],
        )


def test_view_regeneration_uses_updated_metadata_without_moving_primary(tmp_path: Path) -> None:
    """Regenerated views use current metadata while the primary UUID path stays put."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    record = catalog.add_file(_source_file(tmp_path, "alpha.nc"), metadata={"product": "old"})
    record_id = _record_id(record)
    primary_path = record.path()
    assert primary_path is not None

    updated = catalog.update_metadata(record_id, {"product": "new"}, mode="shallow_merge")
    plan = catalog.plan_view(tmp_path / "view", "{product}/{id}_{original_filename}")
    result = plan.apply()

    assert updated.path() == primary_path
    assert result.created[0].target_path == tmp_path / "view" / "new" / f"{record_id}_alpha.nc"
    assert result.created[0].target_path.resolve() == primary_path
