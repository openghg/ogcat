"""Repository behavior tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ogcat.models import ArtifactLocator, CatalogRecord
from ogcat.tinydb_repository import TinyDbCatalogRepository


def test_repository_insert_get_update_and_all(tmp_path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    record = CatalogRecord(
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

    persisted = repository.insert(record)
    expected = replace(record, id="1")

    assert persisted == expected
    stored = repository.get("1")
    assert stored == expected
    assert repository.all() == [expected]

    updated = CatalogRecord(
        id=persisted.id,
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

    assert repository.get("1") == updated


def test_record_round_trips_with_non_path_locator(tmp_path: Path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    record = CatalogRecord(
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        user_metadata={"species": "CO2"},
    )

    persisted = repository.insert(record)
    expected = replace(record, id="1")

    assert persisted == expected
    stored = repository.get("1")
    assert stored == expected
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


def test_from_dict_tolerates_missing_or_null_id_for_draft_records() -> None:
    missing_id_record = CatalogRecord.from_dict(
        {
            "catalog": "fluxes",
            "time_added": "2026-04-23T12:00:00Z",
        }
    )
    null_id_record = CatalogRecord.from_dict(
        {
            "id": None,
            "catalog": "fluxes",
            "time_added": "2026-04-23T12:00:00Z",
        }
    )

    assert missing_id_record.id is None
    assert null_id_record.id is None


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
            catalog="fluxes",
            time_added="2026-04-23T12:00:00Z",
            record_type="external_reference",
            locator=ArtifactLocator.path(f"/tmp/catalog/files/example-{index}.nc"),
            original_filename=f"example-{index}.nc",
            suffixes=[".nc"],
        )
        for index in range(3)
    ]

    persisted = repository.insert_many(records)
    expected = [replace(record, id=str(index + 1)) for index, record in enumerate(records)]

    assert persisted == expected
    assert repository.all() == expected


def test_repository_delete(tmp_path: Path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    persisted = repository.insert(
        CatalogRecord(
            catalog="fluxes",
            time_added="2026-04-23T12:00:00Z",
            locator=ArtifactLocator.path("/tmp/catalog/files/example.nc"),
        )
    )

    assert persisted.id == "1"
    repository.delete("1")

    assert repository.all() == []


def test_repository_recovers_missing_document_id_from_tinydb_doc_id(tmp_path: Path) -> None:
    repository = TinyDbCatalogRepository(tmp_path / "db.json")
    repository._db.insert(
        {
            "catalog": "fluxes",
            "time_added": "2026-04-23T12:00:00Z",
            "record_type": "external_reference",
        }
    )

    stored = repository.get("1")

    assert stored is not None
    assert stored.id == "1"
    assert [record.id for record in repository.all()] == ["1"]


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


def test_locator_from_dict_treats_null_value_as_empty() -> None:
    locator = ArtifactLocator.from_dict({"kind": "path", "value": None, "relative_path": None})

    assert locator.value == ""
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
