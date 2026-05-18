import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.models import CatalogRecord


def _record_id(record: CatalogRecord) -> str:
    assert record.id is not None
    return record.id


def _stored_path(record: CatalogRecord) -> Path:
    assert record.stored_abspath is not None
    return Path(record.stored_abspath)


def _template_replica_path(record: CatalogRecord) -> Path:
    assert "template_replica_path" in record.naming_metadata
    return Path(str(record.naming_metadata["template_replica_path"]))


def _write_zarr_store(path: Path) -> None:
    """Write a tiny directory tree that behaves like a Zarr store for ingest tests."""
    path.mkdir()
    (path / "zarr.json").write_text("{}", encoding="utf-8")
    chunks = path / "data"
    chunks.mkdir()
    (chunks / "0").write_text("chunk", encoding="utf-8")


def test_add_file_uses_generic_default_storage_layout(tmp_path: Path) -> None:
    """Default managed ingest writes a UUID primary and a template symlink replica."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    artifact_uuid = str(record.naming_metadata["artifact_uuid"])
    expected_primary = root / "data" / "objects" / artifact_uuid[:2] / f"{artifact_uuid}.nc"
    expected_replica = root / "data" / "files" / record.time_added[:4] / "example" / "example.nc"
    assert _stored_path(record) == expected_primary
    assert record.record_type == "managed_file"
    assert record.locator == ArtifactLocator.path(expected_primary, relative_path=record.stored_relpath)
    assert expected_primary.exists()
    assert expected_replica.is_symlink()
    assert expected_replica.resolve() == expected_primary
    assert _template_replica_path(record) == expected_replica
    assert [artifact.role for artifact in record.artifacts] == ["data_artifact", "view_link"]
    assert record.artifacts[0].id == "data"
    assert record.artifacts[0].locator == record.locator
    assert record.artifacts[1].id == "template_link"
    assert record.artifacts[1].locator == ArtifactLocator.path(
        expected_replica,
        relative_path=expected_replica.relative_to(root).as_posix(),
    )
    assert record.artifacts[1].relationship == {
        "kind": "view_of",
        "target_artifact_id": "data",
        "view_role": "template_link",
    }
    assert record.original_filename == "example.nc"
    assert record.suffixes == [".nc"]
    assert record.storage_mode == "copy"
    assert record.naming_metadata["primary_location"] == "uuid"


def test_add_file_copies_zarr_directory_as_managed_artifact(tmp_path: Path) -> None:
    """Managed ingest should copy a file-like directory store as one artifact."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.zarr"
    _write_zarr_store(source)

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    artifact_uuid = str(record.naming_metadata["artifact_uuid"])
    expected_primary = root / "data" / "objects" / artifact_uuid[:2] / f"{artifact_uuid}.zarr"
    replica_path = _template_replica_path(record)
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)

    assert source.is_dir()
    assert _stored_path(record) == expected_primary
    assert expected_primary.is_dir()
    assert (expected_primary / "zarr.json").read_text(encoding="utf-8") == "{}"
    assert (expected_primary / "data" / "0").read_text(encoding="utf-8") == "chunk"
    assert replica_path.is_symlink()
    assert replica_path.resolve() == expected_primary
    assert record.suffixes == [".zarr"]
    assert record.storage_mode == "copy"
    assert classification["artifact_kind"] == "zarr_store"
    assert classification["format"] == "zarr"


def test_add_file_moves_zarr_directory_as_managed_artifact(tmp_path: Path) -> None:
    """Managed move ingest should transfer a file-like directory store."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "moved.zarr"
    _write_zarr_store(source)

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source, operation="move", create_template_replica=False)

    stored_path = _stored_path(record)
    classification = record.derived_metadata["classification"]
    assert isinstance(classification, dict)

    assert not source.exists()
    assert stored_path.is_dir()
    assert (stored_path / "zarr.json").read_text(encoding="utf-8") == "{}"
    assert record.storage_mode == "move"
    assert record.suffixes == [".zarr"]
    assert classification["artifact_kind"] == "zarr_store"
    assert classification["format"] == "zarr"


def test_add_file_template_replica_uses_relative_symlink_target(tmp_path: Path) -> None:
    """Default template replicas use relative links so movable catalogs are less brittle."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "relative.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)
    replica_path = _template_replica_path(record)

    assert replica_path.is_symlink()
    assert not Path(os.readlink(replica_path)).is_absolute()
    assert replica_path.resolve() == _stored_path(record)


def test_add_file_uuid_primary_can_skip_template_replica(tmp_path: Path) -> None:
    """UUID-primary managed ingest can omit the human-readable template replica."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source, create_template_replica=False)

    artifact_uuid = str(record.naming_metadata["artifact_uuid"])
    expected_primary = root / "data" / "objects" / artifact_uuid[:2] / f"{artifact_uuid}.nc"
    expected_replica = root / "data" / "files" / record.time_added[:4] / "example" / "example.nc"
    assert _stored_path(record) == expected_primary
    assert expected_primary.exists()
    assert not expected_replica.exists()
    assert not expected_replica.is_symlink()
    assert "template_replica_path" not in record.naming_metadata
    assert "template_replica_storage_relative_path" not in record.naming_metadata
    assert [artifact.role for artifact in record.artifacts] == ["data_artifact"]
    assert record.artifacts[0].locator == record.locator
    assert record.naming_metadata["primary_location"] == "uuid"


def test_add_file_can_store_primary_at_template_path(tmp_path: Path) -> None:
    """Callers can request the template path as the primary artifact location."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source, primary_location="template")

    expected = root / "data" / "files" / record.time_added[:4] / "example" / "example.nc"
    assert _stored_path(record) == expected
    assert expected.exists()
    assert not expected.is_symlink()
    assert "template_replica_path" not in record.naming_metadata
    artifact_uuid = str(record.naming_metadata["artifact_uuid"])
    assert len(artifact_uuid) == 32
    int(artifact_uuid, 16)
    assert artifact_uuid != _record_id(record)
    assert record.naming_metadata["primary_location"] == "template"


def test_add_file_supports_flux_style_templates_when_requested(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            default_schema=RecordSchema(
                directory_template=(
                    "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                    "{version|unversioned}/{flux_type|misc}"
                ),
                filename_template=(
                    "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                    "{original_suffix}"
                ),
            ),
        ),
    )

    record = catalog.add_file(
        source,
        metadata={
            "product": "CTE-HR",
            "version": "v4.2",
            "species": "CO2",
            "domain": "EUROPE",
            "flux_type": "anthropogenic",
            "year": 2024,
            "month": 1,
        },
    )

    expected = (
        root
        / "data"
        / "files"
        / "CO2"
        / "EUROPE"
        / "CTE-HR"
        / "v4.2"
        / "anthropogenic"
        / "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc"
    )
    assert _stored_path(record).parent.parent.name == "objects"
    assert expected.is_symlink()
    assert expected.resolve() == _stored_path(record)
    assert _template_replica_path(record) == expected
    assert record.original_filename == "anthropogenic.202401.nc"
    assert record.storage_mode == "copy"


def test_add_file_uses_record_type_schema_for_naming(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            record_schemas={
                "flux": RecordSchema(
                    directory_template="{species}/{domain|GLOBAL}/{product}",
                    filename_template="{product}_{species}_{year_month_or_original_stem}{original_suffix}",
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="species",
                            description="Gas species.",
                            required=True,
                        ),
                        MetadataFieldDescription(
                            name="product",
                            description="Product name.",
                            required=True,
                        ),
                    ],
                )
            },
        ),
    )

    record = catalog.add_file(
        source,
        record_type="flux",
        metadata={"product": "CTE-HR", "species": "CO2", "year": 2024, "month": 1},
    )

    expected = root / "data" / "files" / "CO2" / "GLOBAL" / "CTE-HR" / "CTE-HR_CO2_202401.nc"
    assert _stored_path(record).parent.parent.name == "objects"
    assert expected.is_symlink()
    assert expected.resolve() == _stored_path(record)
    assert record.record_type == "flux"
    assert record.naming_metadata["record_schema"] == "flux"
    assert _template_replica_path(record) == expected


def test_add_file_preserves_empty_schema_directory_template(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            record_schemas={
                "flux": RecordSchema(
                    directory_template="",
                    filename_template="{product}{original_suffix}",
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="product",
                            description="Product name.",
                            required=True,
                        )
                    ],
                )
            },
        ),
    )

    record = catalog.add_file(source, record_type="flux", metadata={"product": "CTE-HR"})

    expected = root / "data" / "files" / "CTE-HR.nc"
    assert _stored_path(record).parent.parent.name == "objects"
    assert expected.is_symlink()
    assert expected.resolve() == _stored_path(record)
    assert record.naming_metadata["directory_template"] == ""
    assert _template_replica_path(record) == expected


def test_add_file_rejects_unknown_explicit_record_schema(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    with pytest.raises(ValueError, match="Unknown record schema: flux"):
        catalog.add_file(source, record_type="flux")


def test_add_file_enforces_required_metadata_for_selected_schema(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            record_schemas={
                "flux": RecordSchema(
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="species",
                            description="Gas species.",
                            required=True,
                        )
                    ]
                )
            },
        ),
    )

    with pytest.raises(ValueError, match="Missing required metadata for schema flux: species"):
        catalog.add_file(source, record_type="flux", metadata={})

    assert catalog.describe()["record_count"] == 0


def test_add_file_rejects_falsy_non_dict_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    with pytest.raises(TypeError, match="Metadata for schema default must be a dictionary, got list"):
        catalog.add_file(source, metadata=[])  # type: ignore[arg-type]

    assert catalog.describe()["record_count"] == 0


def test_add_artifact_uses_default_schema_required_fields(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="artifacts",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="title",
                        description="Short title.",
                        required=True,
                    )
                ]
            ),
        ),
    )

    with pytest.raises(ValueError, match="Missing required metadata for schema default: title"):
        catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        )


def test_add_artifact_rejects_falsy_non_dict_metadata(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(TypeError, match="Metadata for schema default must be a dictionary, got str"):
        catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
            metadata="",  # type: ignore[arg-type]
        )

    assert catalog.describe()["record_count"] == 0


def test_add_file_does_not_truncate_fractional_year_metadata_for_naming(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "floatyear.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            default_schema=RecordSchema(
                filename_template="{year_month_or_original_stem}{original_suffix}",
            ),
        ),
    )

    record = catalog.add_file(source, metadata={"year": 2024.9, "month": 1})

    assert _template_replica_path(record).name == "floatyear.nc"


def test_add_file_uses_readable_list_metadata_in_naming_templates(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "tagged.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{tags}",
                filename_template="{tags}{original_suffix}",
            ),
        ),
    )

    record = catalog.add_file(source, metadata={"tags": ["a", "b", "c"]})

    replica_path = root / "data" / "files" / "a-b-c" / "a-b-c.nc"
    assert replica_path.is_symlink()
    assert replica_path.resolve() == _stored_path(record)
    assert _template_replica_path(record) == replica_path


def test_add_file_normalizes_path_metadata_before_tinydb_insert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "pathmeta.nc"
    source.write_text("dummy", encoding="utf-8")
    metadata_path = tmp_path / "metadata" / "config.json"

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))
    db = cast(Any, catalog.repository)._db
    original_insert = db.insert
    captured_payloads: list[dict[str, Any]] = []

    def capture_insert(payload: dict[str, Any]) -> int:
        captured_payloads.append(payload)
        user_metadata = payload["user_metadata"]
        assert isinstance(user_metadata, dict)
        assert isinstance(user_metadata["source_path"], str)
        return original_insert(payload)

    monkeypatch.setattr(db, "insert", capture_insert)

    record = catalog.add_file(source, metadata={"source_path": metadata_path})

    assert record.user_metadata["source_path"] == str(metadata_path)
    assert captured_payloads[0]["user_metadata"]["source_path"] == str(metadata_path)


def test_add_artifact_normalizes_nested_metadata_values(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={
            "source_path": Path("raw") / "example.nc",
            "nested": {
                "tuple": ("co2", Path("sites") / "mhd"),
                "set": {"z", "a"},
                "frozenset": frozenset({2, 1}),
                1: "integer key",
            },
        },
        derived_metadata={"shape": (12, 4), "path": Path("derived.nc")},
        naming_metadata={"parts": frozenset({"b", "a"})},
    )

    assert record.user_metadata == {
        "source_path": "raw/example.nc",
        "nested": {
            "tuple": ["co2", "sites/mhd"],
            "set": ["a", "z"],
            "frozenset": [1, 2],
            "1": "integer key",
        },
    }
    assert record.derived_metadata["shape"] == [12, 4]
    assert record.derived_metadata["path"] == "derived.nc"
    assert record.naming_metadata["parts"] == ["a", "b"]


def test_add_artifact_normalizes_date_datetime_metadata(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={
            "published": date(2024, 1, 2),
            "observed_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        },
    )

    assert record.user_metadata["published"] == "2024-01-02"
    assert record.user_metadata["observed_at"] == "2024-01-02T03:04:05+00:00"


def test_add_artifact_rejects_unsupported_metadata_objects(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(TypeError, match="metadata.unsupported must be JSON-compatible; got object"):
        catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
            metadata={"unsupported": object()},
        )


def test_plan_artifact_storage_normalizes_metadata_before_naming(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "planned.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{tags}",
                filename_template="{source_path}{original_suffix}",
            ),
        ),
    )

    plan = catalog.plan_artifact_storage(
        path=source,
        metadata={"tags": ("a", "b"), "source_path": Path("named")},
        primary_location="template",
    )

    assert plan.resolved_directory == "a-b"
    assert plan.resolved_filename == "named.nc"


def test_metadata_normalization_accepts_numpy_scalars_when_available(tmp_path: Path) -> None:
    numpy = cast(Any, pytest.importorskip("numpy"))
    scalar = numpy.int64(5)
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"count": scalar},
    )

    assert record.user_metadata["count"] == 5
    assert type(record.user_metadata["count"]) is int


@pytest.mark.parametrize("field_name", ["artifact_uuid", "id", "uuid", "operation_id"])
def test_add_file_rejects_metadata_that_clobbers_reserved_template_fields(
    tmp_path: Path,
    field_name: str,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "reserved.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    with pytest.raises(ValueError, match=f"Metadata cannot use reserved template field\\(s\\): {field_name}"):
        catalog.add_file(source, metadata={field_name: "user-value"})

    assert catalog.repository.all() == []
    assert list((root / "data" / "files").rglob("reserved.nc")) == []
    assert list((root / "data" / "objects").rglob("reserved.nc")) == []


def test_add_file_template_primary_rejects_artifact_uuid_metadata(tmp_path: Path) -> None:
    """Template-primary ingest rejects metadata that would shadow planner fields."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "reserved.nc"
    source.write_text("dummy", encoding="utf-8")
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    with pytest.raises(ValueError, match="Metadata cannot use reserved template field\\(s\\): artifact_uuid"):
        catalog.add_file(source, metadata={"artifact_uuid": "user-value"}, primary_location="template")

    assert catalog.repository.all() == []
    assert list((root / "data" / "files").rglob("reserved.nc")) == []


@pytest.mark.parametrize("field_name", ["artifact_uuid", "id", "operation_id", "uuid"])
def test_add_file_rejects_internal_identifiers_in_template_replicas(
    tmp_path: Path,
    field_name: str,
) -> None:
    """Default template replicas reject internal identifier template fields."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "reserved.nc"
    source.write_text("dummy", encoding="utf-8")
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{year_added}",
                filename_template=f"{{{field_name}}}{{original_suffix}}",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match=f"Naming templates cannot use internal template field\\(s\\): {field_name}",
    ):
        catalog.add_file(source)

    assert catalog.repository.all() == []
    assert list((root / "data" / "files").rglob("*")) == []
    assert list((root / "data" / "objects").rglob("*")) == []


def test_add_file_allows_domain_identifier_metadata_in_template_replicas(
    tmp_path: Path,
) -> None:
    """Domain identifiers remain available through explicit metadata names."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "named.nc"
    source.write_text("dummy", encoding="utf-8")
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="files",
            default_schema=RecordSchema(
                directory_template="{dataset_id}",
                filename_template="{dataset_id}{original_suffix}",
            ),
        ),
    )

    record = catalog.add_file(source, metadata={"dataset_id": "site-a-2026"})

    expected_replica = root / "data" / "files" / "site-a-2026" / "site-a-2026.nc"
    assert _template_replica_path(record) == expected_replica
    assert expected_replica.is_symlink()


def test_add_reference_allows_identifier_metadata_without_template_context(tmp_path: Path) -> None:
    """Explicit-locator records can persist identifier-like user metadata."""
    source = tmp_path / "external.nc"
    source.write_text("dummy", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    record = catalog.add_reference(
        source,
        metadata={
            "id": "domain-id",
            "uuid": "domain-uuid",
        },
    )

    assert record.user_metadata["id"] == "domain-id"
    assert record.user_metadata["uuid"] == "domain-uuid"


def test_add_file_preserves_dotted_stems_and_simple_suffixes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = (
        root / "data" / "files" / record.time_added[:4] / "anthropogenic.202401" / "anthropogenic.202401.nc"
    )
    assert expected.is_symlink()
    assert expected.resolve() == _stored_path(record)
    assert _template_replica_path(record) == expected
    assert record.original_filename == "anthropogenic.202401.nc"
    assert record.suffixes == [".202401", ".nc"]


def test_add_file_preserves_compressed_suffixes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "archive.tar.gz"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "data" / "files" / record.time_added[:4] / "archive" / "archive.tar.gz"
    assert expected.is_symlink()
    assert expected.resolve() == _stored_path(record)
    assert _template_replica_path(record) == expected
    assert record.original_filename == "archive.tar.gz"
    assert record.suffixes == [".tar", ".gz"]


def test_add_file_appends_numeric_suffix_on_collision(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.nc"
    second = source_dir / "second.nc"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="fluxes",
            default_schema=RecordSchema(
                directory_template=(
                    "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                    "{version|unversioned}/{flux_type|misc}"
                ),
                filename_template=(
                    "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                    "{original_suffix}"
                ),
            ),
        ),
    )
    metadata = {
        "product": "CTE-HR",
        "version": "v4.2",
        "species": "CO2",
        "domain": "EUROPE",
        "flux_type": "anthropogenic",
        "year": 2024,
        "month": 1,
    }

    first_record = catalog.add_file(first, metadata=metadata)
    second_record = catalog.add_file(second, metadata=metadata)

    assert _template_replica_path(first_record).name == ("CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc")
    assert _template_replica_path(second_record).name == ("CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401_2.nc")


def test_add_file_collision_suffixing_preserves_full_extension(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first = source_dir / "first.tar.gz"
    second = source_dir / "second.tar.gz"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="archives",
            default_schema=RecordSchema(
                directory_template="{year_added}",
                filename_template="bundle{original_suffix}",
            ),
        ),
    )

    first_record = catalog.add_file(first)
    second_record = catalog.add_file(second)

    assert _template_replica_path(first_record).name == "bundle.tar.gz"
    assert _template_replica_path(second_record).name == "bundle_2.tar.gz"


def test_add_file_rolls_back_record_when_copy_fails(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    with pytest.raises(FileNotFoundError):
        catalog.add_file(tmp_path / "source" / "missing.nc")

    assert catalog.describe()["record_count"] == 0
    assert catalog.repository.all() == []


def test_add_file_rolls_back_record_when_naming_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "broken.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_render_storage_location(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated naming failure")

    monkeypatch.setattr("ogcat.template_replicas.render_storage_location", fail_render_storage_location)

    with pytest.raises(ValueError, match="simulated naming failure"):
        catalog.add_file(source)

    assert catalog.describe()["record_count"] == 0
    assert catalog.repository.all() == []
    assert list((root / "data" / "files").rglob("*.nc")) == []
    assert list((root / "data" / "objects").rglob("*.nc")) == []


def test_add_file_rolls_back_record_after_staged_insert_before_file_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "broken.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_before_file_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated post-insert failure")

    monkeypatch.setattr("ogcat.template_replicas.render_storage_location", fail_before_file_write)

    with pytest.raises(RuntimeError, match="simulated post-insert failure"):
        catalog.add_file(source)

    assert catalog.repository.all() == []
    assert list((root / "data" / "files").rglob("*.nc")) == []
    assert list((root / "data" / "objects").rglob("*.nc")) == []


def test_add_file_removes_partial_target_when_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "partial.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_copy(source_path: Path, target_path: Path, *args: Any, **kwargs: Any) -> None:
        target_path.write_text("partial", encoding="utf-8")
        raise OSError("simulated copy failure")

    monkeypatch.setattr("ogcat.storage.shutil.copy2", fail_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        catalog.add_file(source)

    assert catalog.describe()["record_count"] == 0
    assert list((root / "data" / "files").rglob("*.nc")) == []


def test_add_file_removes_copied_target_when_record_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "copied.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_insert(record: CatalogRecord) -> CatalogRecord:
        raise OSError("simulated record write failure")

    monkeypatch.setattr(catalog.repository, "insert", fail_insert)

    with pytest.raises(OSError, match="simulated record write failure"):
        catalog.add_file(source)

    assert source.exists()
    assert catalog.describe()["record_count"] == 0
    assert list((root / "data" / "files").rglob("*.nc")) == []


def test_add_file_removes_copied_target_when_metadata_extraction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "metadata.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_extract(path: Path) -> dict[str, object]:
        raise ValueError("simulated metadata failure")

    monkeypatch.setattr("ogcat.catalog_application.extract_derived_metadata", fail_extract)

    with pytest.raises(ValueError, match="simulated metadata failure"):
        catalog.add_file(source)

    assert source.exists()
    assert catalog.describe()["record_count"] == 0
    assert list((root / "data" / "files").rglob("*.nc")) == []


def test_add_file_removes_copied_directory_when_metadata_extraction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback should remove a copied directory target if later metadata extraction fails."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "metadata.zarr"
    _write_zarr_store(source)

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_extract(path: Path) -> dict[str, object]:
        raise ValueError("simulated metadata failure")

    monkeypatch.setattr("ogcat.catalog_application.extract_derived_metadata", fail_extract)

    with pytest.raises(ValueError, match="simulated metadata failure"):
        catalog.add_file(source, create_template_replica=False)

    assert source.is_dir()
    assert (source / "zarr.json").exists()
    assert catalog.describe()["record_count"] == 0
    assert list((root / "data" / "objects").rglob("*.zarr")) == []


def test_add_file_restores_moved_file_when_record_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "moved.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_insert(record: CatalogRecord) -> CatalogRecord:
        raise OSError("simulated record write failure")

    monkeypatch.setattr(catalog.repository, "insert", fail_insert)

    with pytest.raises(OSError, match="simulated record write failure"):
        catalog.add_file(source, operation="move")

    assert list((root / "data" / "files").rglob("*.nc")) == []
    assert source.exists()
    assert source.read_text(encoding="utf-8") == "dummy"
    assert catalog.describe()["record_count"] == 0


def test_add_file_restores_moved_directory_when_record_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback should restore a moved directory if record persistence fails."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "moved.zarr"
    _write_zarr_store(source)

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    def fail_insert(record: CatalogRecord) -> CatalogRecord:
        raise OSError("simulated record write failure")

    monkeypatch.setattr(catalog.repository, "insert", fail_insert)

    with pytest.raises(OSError, match="simulated record write failure"):
        catalog.add_file(source, operation="move", create_template_replica=False)

    assert list((root / "data" / "objects").rglob("*.zarr")) == []
    assert source.is_dir()
    assert (source / "zarr.json").read_text(encoding="utf-8") == "{}"
    assert (source / "data" / "0").read_text(encoding="utf-8") == "chunk"
    assert catalog.describe()["record_count"] == 0


def test_add_file_committed_record_shape_does_not_include_transaction_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    assert "operation_id" not in record.to_dict()
    assert "transaction_id" not in record.to_dict()
    assert catalog.repository.all() == [record]


def test_add_artifact_supports_non_path_locator_records(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2"},
    )

    assert record.record_type == "external_reference"
    assert record.locator.kind == "uri"
    assert record.locator.value == "s3://bucket/example.zarr"
    assert [artifact.role for artifact in record.artifacts] == ["data_artifact"]
    assert record.artifacts[0].id == "data"
    assert record.artifacts[0].locator == record.locator
    assert record.stored_abspath is None
    assert catalog.path(_record_id(record)) is None


def test_catalog_get_and_path_accept_stringable_record_ids(tmp_path: Path) -> None:
    """Record lookup should coerce public record-id inputs with str()."""

    class RecordId:
        def __str__(self) -> str:
            return "50"

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator.path(tmp_path / "references" / f"record-{index}.nc"),
            }
            for index in range(50)
        ]
    )
    record = records[-1]

    assert _record_id(record) == "50"
    assert catalog.get(50) == catalog.get("50") == record
    assert catalog.path(50) == catalog.path("50") == tmp_path / "references" / "record-49.nc"
    assert catalog.get(RecordId()) == record
    with pytest.raises(TypeError, match="record_id must not be None"):
        catalog.get(None)


def test_add_reference_records_local_path_without_copying_or_moving(tmp_path: Path) -> None:
    """Local references should record path metadata without file side effects."""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "archive.tar.gz"
    source.write_text("raw", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(source, metadata={"species": "CO2"})

    resolved_source = source.resolve()
    assert record.record_type == "external_reference"
    assert record.storage_mode == "reference"
    assert record.locator == ArtifactLocator.path(resolved_source)
    assert record.path() == resolved_source
    assert record.original_path == str(resolved_source)
    assert record.original_filename == "archive.tar.gz"
    assert record.suffixes == [".tar", ".gz"]
    assert source.read_text(encoding="utf-8") == "raw"
    assert list((catalog.root / catalog.spec.files_root).rglob("archive.tar.gz")) == []


def test_add_reference_resolves_relative_path_locators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path locators should be normalized like direct local path inputs."""

    source = tmp_path / "relative.nc"
    source.write_text("raw", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    monkeypatch.chdir(tmp_path)

    record = catalog.add_reference(ArtifactLocator.path("relative.nc"))

    resolved_source = source.resolve()
    assert record.locator == ArtifactLocator.path(resolved_source)
    assert record.path() == resolved_source
    assert record.original_path == str(resolved_source)
    assert record.original_filename == "relative.nc"
    assert record.suffixes == [".nc"]


def test_add_reference_records_uri_locator_references(tmp_path: Path) -> None:
    """URI locators should be recorded as reference records without path metadata."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    locator = ArtifactLocator(kind="uri", value="https://example.org/data/example.nc")

    record = catalog.add_reference(locator, metadata={"title": "remote data"})

    assert record.locator == locator
    assert record.storage_mode == "reference"
    assert record.path() is None
    assert record.original_path is None
    assert record.original_filename is None
    assert record.suffixes == []


def test_add_reference_records_uri_string_references(tmp_path: Path) -> None:
    """URI-looking strings should be recorded as URI reference locators."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference("https://example.org/data/example.nc")

    assert record.locator == ArtifactLocator(kind="uri", value="https://example.org/data/example.nc")
    assert record.storage_mode == "reference"
    assert record.path() is None


def test_add_reference_records_uri_keyword_references(tmp_path: Path) -> None:
    """The uri keyword should record an explicit URI reference locator."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(uri="https://example.org/data/example.nc")

    assert record.locator == ArtifactLocator(kind="uri", value="https://example.org/data/example.nc")
    assert record.storage_mode == "reference"


def test_add_reference_records_urlpath_locator_references(tmp_path: Path) -> None:
    """URL-path locators should be recorded unchanged as reference records."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    locator = ArtifactLocator.from_urlpath("s3://bucket/data/example.zarr")

    record = catalog.add_reference(locator)

    assert record.locator == locator
    assert record.storage_mode == "reference"


def test_add_reference_records_urlpath_keyword_references(tmp_path: Path) -> None:
    """The urlpath keyword should record an fsspec URL-path reference locator."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(urlpath="s3://bucket/data/example.zarr")

    assert record.locator == ArtifactLocator.from_urlpath("s3://bucket/data/example.zarr")
    assert record.storage_mode == "reference"


def test_add_reference_requires_one_reference_input(tmp_path: Path) -> None:
    """Reference creation should reject missing or ambiguous locator inputs."""

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="Pass exactly one of reference, uri, or urlpath"):
        catalog.add_reference()
    with pytest.raises(ValueError, match="Pass exactly one of reference, uri, or urlpath"):
        catalog.add_reference("local.nc", uri="https://example.org/data/example.nc")


def test_add_reference_allows_explicit_local_path_metadata(tmp_path: Path) -> None:
    """Explicit local reference metadata should override inferred metadata."""

    source = tmp_path / "raw" / "example.nc"
    source.parent.mkdir()
    source.write_text("raw", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_reference(
        source,
        original_path="logical://source/example",
        original_filename="renamed.dat",
        suffixes=[".dat"],
    )

    assert record.original_path == "logical://source/example"
    assert record.original_filename == "renamed.dat"
    assert record.suffixes == [".dat"]


def test_add_artifacts_supports_batch_artifact_creation(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator.path("/tmp/data/first.nc"),
                "metadata": {"site": "AAA", "month": 1},
                "original_filename": "first.nc",
                "suffixes": [".nc"],
            },
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator.path("/tmp/data/second.nc"),
                "metadata": {"site": "BBB", "month": 2},
                "original_filename": "second.nc",
                "suffixes": [".nc"],
            },
        ]
    )

    assert [record.id for record in records] == ["1", "2"]
    assert catalog.describe()["record_count"] == 2
    assert catalog.get("1") is not None
    assert catalog.get("2") is not None


def test_add_artifacts_accepts_locator_dicts(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": {
                    "kind": "path",
                    "value": "/tmp/data/third.nc",
                    "relative_path": None,
                },
                "metadata": {"site": "CCC"},
                "original_filename": "third.nc",
                "suffixes": [".nc"],
            }
        ]
    )

    assert len(records) == 1
    assert records[0].locator == ArtifactLocator.path("/tmp/data/third.nc")


def test_add_artifact_preserves_non_path_original_path_strings(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        original_path="s3://bucket/source/example.zarr",
    )

    assert record.original_path == "s3://bucket/source/example.zarr"


def test_add_artifacts_raises_helpful_error_for_missing_required_keys(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    try:
        catalog.add_artifacts([{"locator": {"kind": "path", "value": "/tmp/data/file.nc"}}])
    except ValueError as exc:
        assert "artifact batch item 0" in str(exc)
        assert "record_type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for missing record_type")


def test_add_artifacts_raises_helpful_error_for_invalid_locator(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    try:
        catalog.add_artifacts(
            [
                {
                    "record_type": "external_reference",
                    "locator": {"kind": "path", "relative_path": None},
                }
            ]
        )
    except Exception as exc:
        assert "artifact batch item 0" in str(exc)
        assert "invalid locator" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected locator validation error")


def test_add_artifacts_rejects_non_dict_metadata_for_schema_validation(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="artifacts",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="title",
                        description="Short title.",
                        required=True,
                    )
                ]
            ),
        ),
    )

    with pytest.raises(
        TypeError,
        match="artifact batch item 0: Metadata for schema default must be a dictionary, got list",
    ):
        catalog.add_artifacts(
            [
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/file.nc"),
                    "metadata": ["not", "metadata"],
                }
            ]
        )


def test_add_artifacts_identifies_batch_item_for_missing_schema_metadata(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="artifacts",
            record_schemas={
                "external_reference": RecordSchema(
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="title",
                            description="Short title.",
                            required=True,
                        )
                    ]
                )
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="artifact batch item 1: Missing required metadata for schema external_reference: title",
    ):
        catalog.add_artifacts(
            [
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/first.nc"),
                    "metadata": {"title": "First"},
                },
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/second.nc"),
                    "metadata": {},
                },
            ]
        )


def test_add_artifacts_assigns_sequential_record_ids_when_missing(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))
    first = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator.path("/tmp/data/existing.nc"),
    )

    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator.path("/tmp/data/first.nc"),
            },
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator.path("/tmp/data/second.nc"),
            },
        ]
    )

    assert first.id == "1"
    assert [record.id for record in records] == ["2", "3"]


@pytest.mark.parametrize("id_key", ["record_id", "id"])
def test_add_artifacts_rejects_id_fields_in_batch_input(tmp_path: Path, id_key: str) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    try:
        catalog.add_artifacts(
            [
                {
                    id_key: "10",
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/first.nc"),
                },
            ]
        )
    except ValueError as exc:
        assert f"must not supply {id_key}" in str(exc)
        assert "artifact batch item 0" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected id rejection for batch input")


def test_add_artifact_rejects_non_callable_artifact_writer(tmp_path: Path) -> None:
    class InvalidWriter:
        write = "not callable"

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(
        TypeError,
        match=r"artifact_writer must provide a callable write\(\) method, got InvalidWriter",
    ):
        catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator.path("/tmp/data/first.nc"),
            artifact_writer=InvalidWriter(),  # type: ignore[arg-type]
        )


def test_add_artifact_rejects_invalid_source(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(TypeError, match="source must be an OperationSource, got str"):
        catalog.add_artifact(
            record_type="external_reference",
            locator=ArtifactLocator.path("/tmp/data/first.nc"),
            source="not a source",  # type: ignore[arg-type]
        )


def test_add_artifacts_rejects_non_callable_artifact_writer(tmp_path: Path) -> None:
    class InvalidWriter:
        write = "not callable"

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(
        TypeError,
        match=(
            r"artifact batch item 0: artifact_writer must provide a callable write\(\) method, "
            "got InvalidWriter"
        ),
    ):
        catalog.add_artifacts(
            [
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/first.nc"),
                    "artifact_writer": InvalidWriter(),
                },
            ]
        )


def test_add_artifacts_keeps_earlier_commits_when_later_item_fails(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    catalog = Catalog.create(
        root,
        CatalogSpec(
            catalog_name="artifacts",
            record_schemas={
                "external_reference": RecordSchema(
                    metadata_fields=[
                        MetadataFieldDescription(
                            name="title",
                            description="Short title.",
                            required=True,
                        )
                    ]
                )
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="artifact batch item 1: Missing required metadata for schema external_reference: title",
    ):
        catalog.add_artifacts(
            [
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/first.nc"),
                    "metadata": {"title": "First"},
                },
                {
                    "record_type": "external_reference",
                    "locator": ArtifactLocator.path("/tmp/data/second.nc"),
                    "metadata": {},
                },
            ]
        )

    records = catalog.repository.all()
    assert len(records) == 1
    assert records[0].user_metadata["title"] == "First"
