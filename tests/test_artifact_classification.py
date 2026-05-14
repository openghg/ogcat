from __future__ import annotations

import zipfile
from pathlib import Path

from ogcat import ArtifactLocator, Catalog, CatalogSpec
from ogcat.models import CatalogRecord, MetadataDict


def _classification(record: CatalogRecord) -> MetadataDict:
    """Return the classification metadata for a test record."""
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)
    return classification


def test_add_file_classifies_netcdf_suffix(tmp_path: Path) -> None:
    """A managed .nc file should get cheap NetCDF classification metadata."""
    source = tmp_path / "source.nc"
    source.write_text("not really netcdf", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    classification = _classification(record)
    assert classification["artifact_kind"] == "file"
    assert classification["format"] == "netcdf"
    assert classification["suffixes"] == [".nc"]
    assert record.suffixes == [".nc"]


def test_add_reference_classifies_zarr_directory(tmp_path: Path) -> None:
    """A .zarr directory should be classified as a Zarr store."""
    zarr_store = tmp_path / "output.zarr"
    zarr_store.mkdir()
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(zarr_store, record_type="derived_artifact")

    classification = _classification(record)
    assert classification["artifact_kind"] == "zarr_store"
    assert classification["format"] == "zarr"
    assert classification["suffixes"] == [".zarr"]


def test_add_reference_classifies_zip_archive_with_single_netcdf_member(tmp_path: Path) -> None:
    """A local zip with one .nc member should expose archive and inner formats."""
    archive_path = tmp_path / "data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data.nc", "not really netcdf")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(archive_path)

    classification = _classification(record)
    assert classification["artifact_kind"] == "archive"
    assert classification["format"] == "zip"
    assert classification["archive_format"] == "zip"
    assert classification["inner_format"] == "netcdf"


def test_add_reference_classifies_gzipped_netcdf_suffix_chain(tmp_path: Path) -> None:
    """A .nc.gz path should expose gzip as the archive and NetCDF as the inner format."""
    source = tmp_path / "data.nc.gz"
    source.write_bytes(b"compressed-ish")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(source)

    classification = _classification(record)
    assert classification["artifact_kind"] == "archive"
    assert classification["format"] == "gzip"
    assert classification["archive_format"] == "gzip"
    assert classification["inner_format"] == "netcdf"
    assert record.suffixes == [".nc", ".gz"]


def test_add_artifact_classifies_path_locator(tmp_path: Path) -> None:
    """The lower-level add_artifact API should attach classification metadata."""
    source = tmp_path / "artifact.nc"
    source.write_text("not really netcdf", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator.path(source),
        suffixes=source.suffixes,
    )

    classification = _classification(record)
    assert classification["artifact_kind"] == "file"
    assert classification["format"] == "netcdf"


def test_remote_uri_classification_uses_locator_text_without_filesystem_access(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Remote URI classification should not stat a local filesystem path."""

    def fail_is_dir(self: Path) -> bool:
        raise AssertionError(f"unexpected filesystem access for {self}")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    monkeypatch.setattr(Path, "is_dir", fail_is_dir)

    record = catalog.add_reference(uri="https://example.org/data/example.nc?token=abc")

    classification = _classification(record)
    assert record.suffixes == []
    assert classification["artifact_kind"] == "remote_resource"
    assert classification["format"] == "netcdf"
    assert classification["suffixes"] == [".nc"]


def test_urlpath_classification_uses_locator_text_without_filesystem_access(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """URL-path classification should infer Zarr stores without local stat calls."""

    def fail_is_dir(self: Path) -> bool:
        raise AssertionError(f"unexpected filesystem access for {self}")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    monkeypatch.setattr(Path, "is_dir", fail_is_dir)

    record = catalog.add_reference(urlpath="s3://bucket/data/example.zarr")

    classification = _classification(record)
    assert record.suffixes == []
    assert classification["artifact_kind"] == "zarr_store"
    assert classification["format"] == "zarr"
    assert classification["suffixes"] == [".zarr"]


def test_unknown_suffix_classifies_unknown_format(tmp_path: Path) -> None:
    """Unrecognized suffixes should get an unknown format instead of a guess."""
    source = tmp_path / "artifact.custom"
    source.write_text("opaque", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(source)

    classification = _classification(record)
    assert classification["artifact_kind"] == "file"
    assert classification["format"] == "unknown"
    assert classification["suffixes"] == [".custom"]


def test_search_supports_flattened_classification_fields(tmp_path: Path) -> None:
    """Search should resolve common classification fields without dotted paths."""
    netcdf_path = tmp_path / "data.nc"
    netcdf_path.write_text("not really netcdf", encoding="utf-8")
    zarr_store = tmp_path / "output.zarr"
    zarr_store.mkdir()
    archive_path = tmp_path / "data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data.nc", "not really netcdf")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    netcdf = catalog.add_reference(netcdf_path)
    zarr = catalog.add_reference(zarr_store)
    archive = catalog.add_reference(archive_path)

    assert [record.id for record in catalog.search(where={"format": "netcdf"})] == [netcdf.id]
    assert [record.id for record in catalog.search(where={"format": "zip"})] == [archive.id]
    assert [record.id for record in catalog.search(where={"artifact_kind": "zarr_store"})] == [zarr.id]
    assert [record.id for record in catalog.search(where={"archive_format": "zip"})] == [archive.id]
    assert [record.id for record in catalog.search(where={"inner_format": "netcdf"})] == [archive.id]
    assert [
        record.id
        for record in catalog.search(where={"derived_metadata.classification.archive_format": "zip"})
    ] == [archive.id]
