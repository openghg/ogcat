"""Repository behavior tests."""

from __future__ import annotations

import json
from pathlib import Path

from ogcat.models import ArtifactLocator, CatalogRecord
from ogcat.tinydb_repository import TinyDbCatalogRepository


def test_repository_insert_get_update_and_all(tmp_path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    record = CatalogRecord(
        id="rec_000001",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        locator=ArtifactLocator.path(
            "/tmp/catalog/files/example.nc",
            relative_path="files/example.nc",
        ),
        stored_abspath="/tmp/catalog/files/example.nc",
        stored_relpath="files/example.nc",
        storage_mode="copy",
        original_path="/tmp/source/example.nc",
        original_filename="example.nc",
        suffixes=[".nc"],
        user_metadata={"species": "CO2"},
        derived_metadata={"checksum": "abc123"},
        naming_metadata={"resolved_filename": "example.nc"},
    )

    repository.insert(record)

    stored = repository.get(record.id)
    assert stored == record
    assert repository.all() == [record]

    updated = CatalogRecord(
        id=record.id,
        catalog=record.catalog,
        time_added=record.time_added,
        record_type=record.record_type,
        locator=record.locator,
        stored_abspath=record.stored_abspath,
        stored_relpath=record.stored_relpath,
        storage_mode=record.storage_mode,
        original_path=record.original_path,
        original_filename=record.original_filename,
        suffixes=record.suffixes,
        user_metadata={"species": "CH4"},
        derived_metadata=record.derived_metadata,
        naming_metadata=record.naming_metadata,
    )
    repository.update(updated)

    assert repository.get(record.id) == updated


def test_record_round_trips_with_non_path_locator(tmp_path: Path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    record = CatalogRecord(
        id="rec_000002",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        user_metadata={"species": "CO2"},
    )

    repository.insert(record)

    stored = repository.get(record.id)
    assert stored == record
    assert stored is not None
    assert stored.path() is None


def test_from_dict_upgrades_legacy_path_only_records() -> None:
    record = CatalogRecord.from_dict(
        {
            "id": "rec_000003",
            "catalog": "fluxes",
            "stored_abspath": "/tmp/catalog/files/example.nc",
            "stored_relpath": "files/example.nc",
            "storage_mode": "copy",
            "time_added": "2026-04-23T12:00:00Z",
            "original_path": "/tmp/source/example.nc",
            "original_filename": "example.nc",
            "suffixes": [".nc"],
            "user_metadata": {},
            "derived_metadata": {},
            "naming_metadata": {},
        }
    )

    assert record.record_type == "managed_file"
    assert record.locator == ArtifactLocator.path(
        "/tmp/catalog/files/example.nc",
        relative_path="files/example.nc",
    )
    assert record.path() == Path("/tmp/catalog/files/example.nc")


def test_record_to_dict_stays_json_serialisable() -> None:
    record = CatalogRecord(
        id="rec_000004",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        user_metadata={"species": "CO2"},
    )

    payload = record.to_dict()

    assert payload["locator"] == {
        "kind": "uri",
        "value": "s3://bucket/example.zarr",
        "relative_path": None,
    }
    assert json.loads(json.dumps(payload)) == payload


def test_repository_insert_many(tmp_path: Path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    records = [
        CatalogRecord(
            id="rec_000010",
            catalog="fluxes",
            time_added="2026-04-23T12:00:00Z",
            record_type="external_reference",
            locator=ArtifactLocator.path(f"/tmp/catalog/files/example-{index}.nc"),
            original_filename=f"example-{index}.nc",
            suffixes=[".nc"],
        )
        for index in range(3)
    ]

    repository.insert_many(records)

    assert repository.all() == records


def test_record_without_locator_does_not_resolve_to_current_directory() -> None:
    record = CatalogRecord(
        id="rec_000099",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
    )

    assert record.stored_abspath is None
    assert record.path() is None


def test_empty_path_locator_does_not_resolve_to_current_directory() -> None:
    locator = ArtifactLocator(kind="path", value="  ")

    assert locator.as_path() is None


def test_from_dict_defaults_null_record_type_to_managed_file() -> None:
    record = CatalogRecord.from_dict(
        {
            "id": "rec_000100",
            "catalog": "fluxes",
            "record_type": None,
            "stored_abspath": "/tmp/catalog/files/example.nc",
            "stored_relpath": "files/example.nc",
            "storage_mode": "copy",
            "time_added": "2026-04-23T12:00:00Z",
            "original_path": "/tmp/source/example.nc",
            "original_filename": "example.nc",
            "suffixes": [".nc"],
            "user_metadata": {},
            "derived_metadata": {},
            "naming_metadata": {},
        }
    )

    assert record.record_type == "managed_file"
