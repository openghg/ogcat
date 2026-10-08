"""Tests for ephemeral collection traversal and reusable metadata extraction."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from ogcat import (
    ArtifactLocator,
    Catalog,
    CatalogSpec,
    CollectionEntry,
    MetadataExtractorHook,
    OperationContext,
    OperationSource,
    SearchQuery,
    source_writer,
)


def test_members_are_live_sorted_leaves_with_no_inherited_metadata(tmp_path: Path) -> None:
    """Live entries see filesystem changes and retain only explicitly extracted metadata."""
    root = tmp_path / "series"
    root.mkdir()
    first = root / "b.zarr"
    first.mkdir()
    (first / "chunk").touch()
    entry = CollectionEntry(ArtifactLocator.from_path(root), {"scenario": "parent"}, "*.zarr")
    assert [item.path() for item in entry.members()] == [first]
    second = root / "a.zarr"
    second.mkdir()
    members = entry.members(extractor=lambda path: {"name": path.name})
    assert [item.path() for item in members] == [second, first]
    assert members[0].metadata == {"name": "a.zarr"}
    assert all(item.member_pattern is None for item in members)
    with pytest.raises(ValueError, match="explicit member pattern"):
        members[1].members()
    first.rename(root / "c.zarr")
    assert [item.path() for item in entry.members()] == [second, root / "c.zarr"]


def test_explicit_nested_traversal_requires_no_catalog(tmp_path: Path) -> None:
    """A directory leaf can become a collection through an explicit caller pattern."""
    nested = tmp_path / "model"
    nested.mkdir()
    (nested / "co2.nc").touch()
    (nested / "chunk").touch()
    root = CollectionEntry(ArtifactLocator.from_path(tmp_path), {}, "*")
    model = root.members()[0]
    children = model.members(pattern="*.nc", extractor=lambda path: {"species": path.stem})
    assert len(children) == 1
    assert children[0].metadata == {"species": "co2"}
    assert children[0].path() == nested / "co2.nc"


@pytest.mark.parametrize(
    "filters",
    [
        {"where": {"species": "CO2"}},
        {"contains": {"tags": "observed"}},
        {"regex": {"species": "^CO[0-9]$"}},
        {"match": {"species": "CO*"}},
        {"exists": ["nullable"], "missing": ["absent"]},
        {"where": {"species": "co2"}, "ignore_case": True},
        {"query": SearchQuery.date_between("date", "2024-01-01", "2024-01-31")},
    ],
)
def test_members_filter_extracted_plain_metadata(tmp_path: Path, filters: dict[str, object]) -> None:
    """The read side supports shared equality, pattern, presence, and date predicates."""
    (tmp_path / "a.nc").touch()
    (tmp_path / "b.nc").touch()

    def extract(path: Path) -> Mapping[str, object]:
        """Return distinct metadata for the two leaves."""
        if path.stem == "a":
            return {"species": "CO2", "date": "2024-01-01", "tags": ["observed"], "nullable": None}
        return {"species": "O2", "date": "2024-02-01", "tags": [], "absent": True}

    root = CollectionEntry(ArtifactLocator.from_path(tmp_path), {}, "*.nc")
    # Parametrization supplies the public keyword types individually.
    result = root.members(extractor=extract, **filters)  # type: ignore[arg-type]
    assert [item.path() for item in result] == [tmp_path / "a.nc"]


def test_extractor_none_normalization_and_errors(tmp_path: Path) -> None:
    """Normalization rejects unsupported metadata and never swallows extractor failures."""
    (tmp_path / "a.nc").touch()
    root = CollectionEntry(ArtifactLocator.from_path(tmp_path), {}, "*.nc")
    assert root.members(extractor=lambda path: None)[0].metadata == {}
    with pytest.raises(TypeError):
        root.members(extractor=lambda path: {"bad": object()})

    def fail(path: Path) -> Mapping[str, object]:
        """Simulate an unreadable or malformed member."""
        raise RuntimeError("malformed member")

    with pytest.raises(RuntimeError, match="malformed member"):
        root.members(extractor=fail)


def test_empty_collection_still_validates_filters(tmp_path: Path) -> None:
    """Invalid search arguments raise even when no members can match."""
    root = CollectionEntry(ArtifactLocator.from_path(tmp_path), {}, "*.nc")
    with pytest.raises(TypeError, match="exists"):
        root.members(exists="name")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="regex"):
        root.members(regex={"name": 3})  # type: ignore[arg-type]


def test_members_reject_patterns_and_symlinks_escaping_root(tmp_path: Path) -> None:
    """Safety validation applies to independent entries as well as catalog roots."""
    root = tmp_path / "series"
    root.mkdir()
    outside = tmp_path / "outside.nc"
    outside.touch()
    entry = CollectionEntry(ArtifactLocator.from_path(root), {}, "*.nc")
    with pytest.raises(ValueError, match="collection_pattern"):
        entry.members(pattern="../*.nc")
    with pytest.raises(ValueError, match="collection_pattern"):
        entry.members(pattern=str(outside))
    (root / "link.nc").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes its root"):
        entry.members()


def test_catalog_members_are_read_only_and_do_not_dispatch_hooks(tmp_path: Path) -> None:
    """Browsing leaves database and artifact bytes unchanged and runs no ingest hooks."""
    root = tmp_path / "series"
    root.mkdir()
    (root / "a.nc").write_bytes(b"dataset")
    calls: list[Path] = []

    def extract(path: Path) -> Mapping[str, object] | None:
        """Record hook dispatch without reading directory contents."""
        calls.append(path)
        return None

    catalog_root = tmp_path / "catalog"
    with Catalog.create(
        catalog_root, CatalogSpec(catalog_name="entries"), hooks=[MetadataExtractorHook(extract)]
    ) as catalog:
        record = catalog.add_collection(root, collection_pattern="*.nc", metadata={"scenario": "parent"})
    calls.clear()
    before = {path: path.read_bytes() for path in catalog_root.rglob("*") if path.is_file()}
    with Catalog.open(catalog_root, read_only=True, hooks=[MetadataExtractorHook(extract)]) as catalog:
        members = catalog.members(record.id, extractor=lambda path: {"name": path.name})
        assert members[0].metadata == {"name": "a.nc"}
        assert len(catalog.search()) == 1
    assert calls == []
    assert before == {path: path.read_bytes() for path in catalog_root.rglob("*") if path.is_file()}
    assert (root / "a.nc").read_bytes() == b"dataset"


def test_same_extractor_preserves_source_name_on_move_and_live_members(tmp_path: Path) -> None:
    """A single callable reads the original month, filename, and content before a move."""
    series = tmp_path / "series"
    series.mkdir()
    source = series / "co2_202401.nc"
    source.write_bytes(b"dataset")
    paths: list[Path] = []

    def extract(path: Path) -> Mapping[str, object] | None:
        """Parse monthly filenames and read content, skipping collection directories."""
        if path.is_dir():
            return None
        paths.append(path)
        return {"bytes": len(path.read_bytes()), "month": path.stem.split("_")[1], "name": path.name}

    with Catalog.create(
        tmp_path / "catalog", CatalogSpec(catalog_name="adapter"), hooks=[MetadataExtractorHook(extract)]
    ) as catalog:
        collection = catalog.add_collection(series, collection_pattern="*.nc")
        members = catalog.members(collection.id, extractor=extract)
        expected = {"bytes": 7, "month": "202401", "name": "co2_202401.nc"}
        assert members[0].metadata == expected
        record = catalog.add_file(source, operation="move", create_template_replica=False)
        assert not source.exists()
        assert paths == [source, source]
        assert all(record.derived_metadata[key] == value for key, value in expected.items())
        target = record.path()
        assert target is not None
        assert target.name != source.name
        assert target.read_bytes() == b"dataset"
        assert catalog.members(collection.id, extractor=extract) == []


def test_hook_skips_operations_without_local_sources(tmp_path: Path) -> None:
    """The source adapter skips URI artifacts and generated targets without local sources."""
    paths: list[Path] = []
    hook = MetadataExtractorHook(lambda path: paths.append(path))
    context = OperationContext(
        catalog_root=tmp_path,
        operation_id="test",
        operation_type="add_reference",
        record_type="reference",
        user_metadata={},
        planned_locators=[ArtifactLocator(kind="uri", value="s3://bucket/file")],
    )
    hook.before_validate_metadata(context)
    context.planned_locators = [ArtifactLocator.from_path(tmp_path / "generated.nc")]
    hook.before_validate_metadata(context)
    assert paths == []
    assert context.derived_metadata == {}


def test_generated_artifact_without_source_skips_source_extractor(tmp_path: Path) -> None:
    """A writer target is not mistaken for a source before materialization."""
    paths: list[Path] = []

    def extract(path: Path) -> Mapping[str, object]:
        """Read genuine sources without masking nonexistent path errors."""
        paths.append(path)
        return {"bytes": len(path.read_bytes()), "name": path.name}

    def generate(source: OperationSource, target: Path) -> None:
        """Generate an artifact without a local input path."""
        assert source.path is None
        target.write_bytes(b"generated")

    with Catalog.create(
        tmp_path / "catalog", CatalogSpec(catalog_name="generated"), hooks=[MetadataExtractorHook(extract)]
    ) as catalog:
        target = tmp_path / "generated.nc"
        record = catalog.add_artifact(
            record_type="generated",
            locator=ArtifactLocator.from_path(target),
            artifact_writer=source_writer(generate, target_kind="file"),
        )
        assert paths == []
        assert "bytes" not in record.derived_metadata
        assert target.read_bytes() == b"generated"
        reference = catalog.add_reference(target)
        assert paths == [target]
        assert reference.derived_metadata["bytes"] == 9
        assert reference.derived_metadata["name"] == "generated.nc"


def test_catalog_members_requires_active_local_collection(tmp_path: Path) -> None:
    """Explicit overrides cannot traverse ordinary, remote, or deleted records."""
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="validation")) as catalog:
        ordinary = catalog.add_reference(tmp_path)
        remote = catalog.add_collection(uri="s3://bucket/series")
        collection = catalog.add_collection(tmp_path, collection_pattern="*.nc")
        with pytest.raises(KeyError):
            catalog.members("absent")
        with pytest.raises(ValueError, match="not an active collection"):
            catalog.members(ordinary.id)
        with pytest.raises(NotImplementedError, match="local path"):
            catalog.members(remote.id)
        catalog.delete(collection.id)
        with pytest.raises(ValueError, match="not an active collection"):
            catalog.members(collection.id)
