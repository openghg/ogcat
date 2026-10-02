"""Registered local path inspection and CLI regressions."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ogcat import Catalog, CatalogSpec
from ogcat.cli import app
from ogcat.models import ArtifactDescriptor, ArtifactLocator, CatalogRecord


def test_check_local_paths_and_view_links(tmp_path: Path) -> None:
    """Report missing, broken, and wrong links while preserving valid paths."""
    source = tmp_path / "source"
    source.write_text("data")
    wrong = tmp_path / "wrong"
    wrong.write_text("other data")
    good_link = tmp_path / "good-link"
    good_link.symlink_to(source.name)
    broken = tmp_path / "broken"
    broken.symlink_to("missing-target")
    wrong_link = tmp_path / "wrong-link"
    wrong_link.symlink_to(wrong)
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example")) as catalog:
        artifacts = [
            ArtifactDescriptor(id="data", role="data_artifact", locator=ArtifactLocator.from_path(source)),
            ArtifactDescriptor(
                id="directory", role="attachment", locator=ArtifactLocator.from_path(tmp_path)
            ),
            ArtifactDescriptor(
                id="remote", role="attachment", locator=ArtifactLocator.from_urlpath("s3://bucket/missing")
            ),
            ArtifactDescriptor(
                id="purged",
                role="attachment",
                locator=ArtifactLocator.from_path(tmp_path / "purged"),
                state="purged",
            ),
        ]
        for name, path in [("good", good_link), ("broken", broken), ("wrong", wrong_link), ("file", wrong)]:
            artifacts.append(
                ArtifactDescriptor(
                    id=name,
                    role="view_link",
                    locator=ArtifactLocator.from_path(path),
                    relationship={"kind": "view_of", "target_artifact_id": "data"},
                )
            )
        artifacts.append(
            ArtifactDescriptor(
                id="missing", role="attachment", locator=ArtifactLocator.from_path(tmp_path / "absent")
            )
        )
        record = catalog.repository.insert(
            CatalogRecord(catalog="example", time_added="now", artifacts=artifacts)
        )
        issues = catalog.check()
        assert [(issue["artifact_id"], issue["code"]) for issue in issues] == [
            ("broken", "broken_symlink"),
            ("wrong", "incorrect_symlink"),
            ("file", "incorrect_symlink"),
            ("missing", "missing_path"),
        ]
        assert all(issue["record_id"] == record.id for issue in issues)
        assert all(set(issue) == {"record_id", "artifact_id", "code", "path", "message"} for issue in issues)


def test_check_visibility_snapshot_and_closed_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use one snapshot, hide deleted records, and reject inspection after close."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example")) as catalog:
        record = catalog.add_reference(tmp_path / "missing")
        catalog.delete(record.id)
        original_all = catalog.repository.all
        calls = []

        def snapshot() -> list[CatalogRecord]:
            """Count repository snapshots."""
            calls.append(True)
            return original_all()

        monkeypatch.setattr(catalog.repository, "all", snapshot)
        assert catalog.check() == []
        assert len(calls) == 1
        assert catalog.check(include_deleted=True)[0]["code"] == "missing_path"
        assert len(calls) == 2
    with pytest.raises(RuntimeError, match="closed"):
        catalog.check()


def test_check_permission_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Represent filesystem inspection failures as unreadable path issues."""
    path = tmp_path / "source"
    path.write_text("data")
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="example")) as catalog:
        catalog.add_reference(path)
        original_lstat = Path.lstat

        def denied(self: Path) -> object:
            """Deny inspection only for the registered artifact."""
            if self == path:
                raise PermissionError("permission denied")
            return original_lstat(self)

        monkeypatch.setattr(Path, "lstat", denied)
        assert catalog.check()[0]["code"] == "unreadable_path"
        monkeypatch.setattr(Path, "lstat", original_lstat)
        monkeypatch.setattr("ogcat.integrity.os.access", lambda path, mode: False)
        assert catalog.check()[0]["code"] == "unreadable_path"


def test_check_cli_read_only_json_and_exit_status(tmp_path: Path) -> None:
    """Expose findings with exit one and leave catalog files untouched."""
    root = tmp_path / "catalog"
    with Catalog.create(root, CatalogSpec(catalog_name="example")) as catalog:
        catalog.add_reference(tmp_path / "missing")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    runner = CliRunner()
    result = runner.invoke(app, ["check", "--catalog", str(root), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)[0]["code"] == "missing_path"
    text = runner.invoke(app, ["check", "--catalog", str(root)])
    assert text.exit_code == 1
    assert "missing_path" in text.stdout
    assert str(tmp_path / "missing") in text.stdout
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before
    (tmp_path / "missing").write_text("data")
    clean = runner.invoke(app, ["check", "--catalog", str(root), "--json"])
    assert clean.exit_code == 0
    assert json.loads(clean.stdout) == []
    clean_text = runner.invoke(app, ["check", "--catalog", str(root)])
    assert clean_text.exit_code == 0
    assert "No registered local path issues observed" in clean_text.stdout
