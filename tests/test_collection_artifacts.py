from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from ogcat import Catalog, CatalogSpec
from ogcat.models import CatalogRecord, MetadataDict


def _classification(record: CatalogRecord) -> MetadataDict:
    """Return classification metadata for a collection artifact test record."""
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)
    return classification


def test_add_collection_records_directory_as_one_collection_artifact(tmp_path: Path) -> None:
    """Directory-backed datasets should be catalogued as one collection artifact."""
    source = tmp_path / "collection"
    source.mkdir()
    (source / "co2_202401.nc").write_text("not really netcdf", encoding="utf-8")
    (source / "co2_202402.nc").write_text("not really netcdf", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    record = catalog.add_collection(
        source,
        collection_pattern="co2_*.nc",
        member_format="netcdf",
        member_suffixes=[".nc"],
    )

    classification = _classification(record)
    assert classification["artifact_kind"] == "collection"
    assert classification["collection_pattern"] == "co2_*.nc"
    assert classification["member_format"] == "netcdf"
    assert classification["member_suffixes"] == [".nc"]
    assert record.locator.kind == "path"
    assert record.path() == source.resolve()
    assert [stored.id for stored in catalog.search(as_record_set=False)] == [record.id]


def test_collection_search_supports_flattened_classification_fields(tmp_path: Path) -> None:
    """Collection classification fields should be searchable without dotted paths."""
    source = tmp_path / "collection"
    source.mkdir()
    (source / "flux_202401.nc").write_text("not really netcdf", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    record = catalog.add_collection(
        source,
        collection_pattern="flux_*.nc",
        member_format="netcdf",
        member_suffixes=[".nc"],
    )

    assert [item.id for item in catalog.search(where={"artifact_kind": "collection"})] == [record.id]
    assert [item.id for item in catalog.search(where={"member_format": "netcdf"})] == [record.id]
    assert [item.id for item in catalog.search(where={"collection_pattern": "flux_*.nc"})] == [record.id]
    assert [item.id for item in catalog.search(contains={"member_suffixes": ".nc"})] == [record.id]
    assert [
        item.id
        for item in catalog.search(where={"derived_metadata.classification.collection_pattern": "flux_*.nc"})
    ] == [record.id]


def test_add_collection_infers_member_suffixes_from_pattern(tmp_path: Path) -> None:
    """Collection metadata can infer simple member details from the pattern."""
    source = tmp_path / "collection"
    source.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    record = catalog.add_collection(
        source,
        collection_pattern="nested/*.nc",
        reader_hint="xarray.open_mfdataset",
    )

    classification = _classification(record)
    assert classification["artifact_kind"] == "collection"
    assert classification["member_format"] == "netcdf"
    assert classification["member_suffixes"] == [".nc"]
    assert classification["reader_hint"] == "xarray.open_mfdataset"


def test_plain_directory_reference_is_not_implicitly_a_collection(tmp_path: Path) -> None:
    """Collection semantics require explicit opt-in through add_collection."""
    source = tmp_path / "directory"
    source.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    record = catalog.add_reference(source)

    classification = _classification(record)
    assert classification["artifact_kind"] == "directory"
    assert "collection_pattern" not in classification


def test_add_collection_rejects_non_directory_local_path(tmp_path: Path) -> None:
    """A collection must be backed by a local directory, not a regular file."""
    source = tmp_path / "single.nc"
    source.write_text("not really netcdf", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    with pytest.raises(ValueError, match="existing directory"):
        catalog.add_collection(
            source,
            collection_pattern="*.nc",
            member_format="netcdf",
            member_suffixes=[".nc"],
        )


@pytest.mark.parametrize(
    "collection_pattern",
    [
        "",
        "/absolute/*.nc",
        "../outside/*.nc",
        r"..\outside\*.nc",
        r"C:\data\*.nc",
        "C:/data/*.nc",
        r"\\server\share\*.nc",
    ],
)
def test_add_collection_rejects_invalid_collection_patterns(
    tmp_path: Path,
    collection_pattern: str,
) -> None:
    """Collection member patterns must stay relative to the collection root."""
    source = tmp_path / "collection"
    source.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    with pytest.raises(ValueError, match="collection_pattern"):
        catalog.add_collection(source, collection_pattern=collection_pattern)


def test_add_collection_rejects_non_string_member_suffixes(tmp_path: Path) -> None:
    """Collection member suffixes should not silently coerce non-string values."""
    source = tmp_path / "collection"
    source.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    with pytest.raises(TypeError, match="member_suffixes"):
        catalog.add_collection(
            source,
            collection_pattern="*.nc",
            member_suffixes=[1],  # type: ignore[list-item]
        )


def test_add_collection_rejects_bare_string_member_suffixes(tmp_path: Path) -> None:
    """A single member suffix must be wrapped in a sequence."""
    source = tmp_path / "collection"
    source.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))

    with pytest.raises(TypeError, match="bare string"):
        catalog.add_collection(
            source,
            collection_pattern="*.nc",
            member_suffixes=".nc",  # type: ignore[arg-type]
        )


def test_add_collection_does_not_open_member_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Collection registration should not read member payloads."""
    source = tmp_path / "collection"
    source.mkdir()
    members = [
        source / "co2_202401.nc",
        source / "co2_202402.nc",
    ]
    for member in members:
        member.write_text("not really netcdf", encoding="utf-8")
    member_paths = {member.resolve() for member in members}
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="collections"))
    original_open = builtins.open
    original_path_open = Path.open

    def guard_member_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        """Fail if collection registration opens a member file."""
        if Path(file).resolve() in member_paths:
            raise AssertionError(f"unexpected member file open: {file}")
        return original_open(file, *args, **kwargs)

    def guard_member_path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        """Fail if collection registration opens a member path."""
        if self.resolve() in member_paths:
            raise AssertionError(f"unexpected member file open: {self}")
        return original_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard_member_open)
    monkeypatch.setattr(Path, "open", guard_member_path_open)

    record = catalog.add_collection(
        source,
        collection_pattern="co2_*.nc",
        member_format="netcdf",
        member_suffixes=[".nc"],
    )

    classification = _classification(record)
    assert classification["artifact_kind"] == "collection"
    assert classification["member_format"] == "netcdf"
