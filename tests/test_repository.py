"""Repository behavior tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ogcat.models import ArtifactDescriptor, ArtifactLocator, CatalogRecord
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
    assert record.artifacts == [
        ArtifactDescriptor(
            id="data",
            role="data_artifact",
            locator=ArtifactLocator.path(
                "/tmp/catalog/files/example.nc",
                relative_path="files/example.nc",
            ),
        )
    ]
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
    assert payload["artifacts"] == [
        {
            "id": "data",
            "role": "data_artifact",
            "locator": {
                "kind": "uri",
                "value": "s3://bucket/example.zarr",
                "relative_path": None,
            },
            "state": "available",
            "relationship": {},
            "claims": [],
            "facets": [],
        }
    ]
    assert json.loads(json.dumps(payload)) == payload


def test_record_round_trips_with_data_and_preview_artifacts() -> None:
    """Records can persist a data artifact plus an auxiliary preview descriptor."""
    data_locator = ArtifactLocator.path(
        "/tmp/catalog/files/example.nc",
        relative_path="files/example.nc",
    )
    preview_locator = ArtifactLocator.path(
        "/tmp/catalog/previews/example.png",
        relative_path="previews/example.png",
    )
    record = CatalogRecord(
        id="rec_000004",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        record_type="managed_file",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/stale-compat.zarr"),
        artifacts=[
            ArtifactDescriptor(
                id="data",
                role="data_artifact",
                locator=data_locator,
                claims=[{"kind": "representation", "name": "netcdf"}],
            ),
            ArtifactDescriptor(
                id="preview",
                role="preview",
                locator=preview_locator,
                relationship={"kind": "derived_from", "target_artifact_id": "data"},
                facets=[{"kind": "image", "format": "png"}],
            ),
        ],
        user_metadata={"species": "CO2"},
    )

    payload = record.to_dict()
    reloaded = CatalogRecord.from_dict(payload)

    assert record.locator == data_locator
    assert record.path() == Path("/tmp/catalog/files/example.nc")
    assert payload["locator"] == data_locator.to_dict()
    artifacts_payload = payload["artifacts"]
    assert isinstance(artifacts_payload, list)
    assert isinstance(artifacts_payload[1], dict)
    assert artifacts_payload[1]["role"] == "preview"
    assert reloaded == record


def test_data_artifact_locator_refreshes_compatibility_path_fields() -> None:
    """Inline data artifacts keep the legacy locator and path fields aligned."""
    data_locator = ArtifactLocator.path(
        "/tmp/catalog/files/current.nc",
        relative_path="files/current.nc",
    )
    record = CatalogRecord(
        id="rec_000005",
        catalog="fluxes",
        time_added="2026-04-23T12:00:00Z",
        locator=ArtifactLocator.path(
            "/tmp/catalog/files/stale.nc",
            relative_path="files/stale.nc",
        ),
        stored_abspath="/tmp/catalog/files/stale.nc",
        stored_relpath="files/stale.nc",
        artifacts=[
            ArtifactDescriptor(
                id="data",
                role="data_artifact",
                locator=data_locator,
            )
        ],
    )

    assert record.locator == data_locator
    assert record.stored_abspath == "/tmp/catalog/files/current.nc"
    assert record.stored_relpath == "files/current.nc"
    assert record.path() == Path("/tmp/catalog/files/current.nc")


def test_record_rejects_multiple_data_artifacts() -> None:
    """Only one current data artifact is allowed before replica leadership exists."""
    with pytest.raises(ValueError, match="multiple data_artifact"):
        CatalogRecord(
            catalog="fluxes",
            time_added="2026-04-23T12:00:00Z",
            artifacts=[
                ArtifactDescriptor(
                    id="data",
                    role="data_artifact",
                    locator=ArtifactLocator.path("/tmp/catalog/files/current.nc"),
                ),
                ArtifactDescriptor(
                    id="data-copy",
                    role="data_artifact",
                    locator=ArtifactLocator.path("/tmp/catalog/files/copy.nc"),
                ),
            ],
        )


def test_artifact_descriptor_allows_extension_roles_without_dispatch_semantics() -> None:
    """Extension roles can persist before ogcat assigns them dispatch behavior."""
    descriptor = ArtifactDescriptor(
        id="plugin-report",
        role="plugin_defined_artifact",
        relationship={"target_artifact_id": "data"},
    )

    assert descriptor.to_dict()["role"] == "plugin_defined_artifact"


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
    assert record.artifacts == []


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
