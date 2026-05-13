"""Catalog metadata update API tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogRecord, CatalogSpec, MetadataFieldDescription, RecordSchema


def _record_id(record: CatalogRecord) -> str:
    """Return a persisted record id for test setup."""
    assert record.id is not None
    return record.id


def _catalog(tmp_path: Path) -> Catalog:
    """Create a small catalog for metadata update tests."""
    return Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="updates"))


def _reference_record(catalog: Catalog, *, metadata: dict[str, object] | None = None) -> CatalogRecord:
    """Add a reference record with optional user metadata."""
    return catalog.add_reference(
        uri="https://example.org/data.nc",
        metadata={} if metadata is None else metadata,
    )


def test_update_metadata_replace_replaces_user_metadata(tmp_path: Path) -> None:
    """replace mode swaps the whole user metadata dictionary."""
    catalog = _catalog(tmp_path)
    record = _reference_record(catalog, metadata={"title": "Old title", "species": "CO2"})

    updated = catalog.update_metadata(
        _record_id(record),
        {"title": "New title", "quality_flag": "reviewed"},
        mode="replace",
    )

    assert updated is not record
    assert updated.user_metadata == {"title": "New title", "quality_flag": "reviewed"}
    assert catalog.get(_record_id(record)) == updated


def test_update_metadata_shallow_merge_updates_only_top_level_keys(tmp_path: Path) -> None:
    """shallow_merge keeps existing top-level keys and replaces nested values."""
    catalog = _catalog(tmp_path)
    record = _reference_record(
        catalog,
        metadata={
            "title": "Original title",
            "review": {"status": "pending", "notes": ["needs QA"]},
            "site": "MHD",
        },
    )

    updated = catalog.update_metadata(
        _record_id(record),
        {"quality_flag": "reviewed", "review": {"status": "accepted"}},
        mode="shallow_merge",
    )

    assert updated.user_metadata == {
        "title": "Original title",
        "review": {"status": "accepted"},
        "site": "MHD",
        "quality_flag": "reviewed",
    }


def test_update_derived_metadata_replace_and_shallow_merge(tmp_path: Path) -> None:
    """Derived metadata supports the same replace and shallow merge modes."""
    catalog = _catalog(tmp_path)
    record = catalog.add_reference(
        uri="https://example.org/data.nc",
        derived_metadata={
            "checksum": "old",
            "netcdf": {"dims": {"time": 12}},
        },
    )

    replaced = catalog.update_derived_metadata(
        _record_id(record),
        {"shape": (12, 4)},
        mode="replace",
    )
    merged = catalog.update_derived_metadata(
        _record_id(record),
        {"netcdf": {"attrs": {"title": "demo"}}, "path": Path("derived.nc")},
        mode="shallow_merge",
    )

    assert replaced.derived_metadata == {"shape": [12, 4]}
    assert merged.derived_metadata == {
        "shape": [12, 4],
        "netcdf": {"attrs": {"title": "demo"}},
        "path": "derived.nc",
    }


def test_update_metadata_normalizes_path_tuple_and_nested_values(tmp_path: Path) -> None:
    """Metadata update normalizes Python values with the add-operation rules."""
    catalog = _catalog(tmp_path)
    record = _reference_record(catalog, metadata={"title": "Original title"})

    updated = catalog.update_metadata(
        _record_id(record),
        {
            "source_path": Path("raw") / "data.nc",
            "tags": ("co2", Path("sites") / "mhd"),
            1: {"parts": ("a", "b")},
        },
        mode="replace",
    )

    assert updated.user_metadata == {
        "source_path": "raw/data.nc",
        "tags": ["co2", "sites/mhd"],
        "1": {"parts": ["a", "b"]},
    }


def test_update_metadata_replace_preserves_required_field_validation(tmp_path: Path) -> None:
    """Invalid replacement metadata is rejected and the stored record is unchanged."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="validated",
            record_schemas={
                "measurement": RecordSchema(
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="title",
                            description="Human-readable title.",
                            required=True,
                        ),
                        MetadataFieldDescription(
                            name="site",
                            description="Site code.",
                            required=True,
                        ),
                    ],
                )
            },
        ),
    )
    record = catalog.add_artifact(
        record_type="measurement",
        locator=ArtifactLocator(kind="uri", value="https://example.org/data.nc"),
        metadata={"title": "MHD data", "site": "MHD"},
    )

    with pytest.raises(ValueError, match="Missing required metadata for schema measurement: site"):
        catalog.update_metadata(
            _record_id(record),
            {"title": "Retitled"},
            mode="replace",
        )

    assert catalog.get(_record_id(record)) == record


def test_update_metadata_missing_record_id_has_clear_error(tmp_path: Path) -> None:
    """Missing update targets raise a clear record-not-found error."""
    catalog = _catalog(tmp_path)

    with pytest.raises(KeyError, match="Record not found: missing"):
        catalog.update_metadata("missing", {"title": "No record"})


def test_update_metadata_rolls_back_with_caller_owned_transaction(tmp_path: Path) -> None:
    """Caller-owned transactions restore the previous record on rollback."""
    catalog = _catalog(tmp_path)
    record = _reference_record(catalog, metadata={"title": "Original title"})

    with pytest.raises(RuntimeError, match="later operation failed"), catalog.transaction() as transaction:
        updated = catalog.update_metadata(
            _record_id(record),
            {"title": "Updated title"},
            transaction=transaction,
        )
        assert catalog.get(_record_id(record)) == updated
        raise RuntimeError("later operation failed")

    assert catalog.get(_record_id(record)) == record


def test_update_metadata_rejects_unknown_mode(tmp_path: Path) -> None:
    """Only replace and shallow_merge update modes are accepted."""
    catalog = _catalog(tmp_path)
    record = _reference_record(catalog, metadata={"title": "Original title"})

    with pytest.raises(ValueError, match="metadata update mode must be 'replace' or 'shallow_merge'"):
        catalog.update_metadata(
            _record_id(record),
            {"title": "Updated title"},
            mode="deep_merge",  # type: ignore[arg-type]
        )
