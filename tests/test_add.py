from pathlib import Path
from typing import Any

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.models import CatalogRecord


def _record_id(record: CatalogRecord) -> str:
    assert record.id is not None
    return record.id


def _stored_path(record: CatalogRecord) -> Path:
    assert record.stored_abspath is not None
    return Path(record.stored_abspath)


def test_add_file_uses_generic_default_storage_layout(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "example.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "files" / record.time_added[:4] / "example" / "example.nc"
    assert _stored_path(record) == expected
    assert record.record_type == "managed_file"
    assert record.locator == ArtifactLocator.path(expected, relative_path=record.stored_relpath)
    assert expected.exists()
    assert record.original_filename == "example.nc"
    assert record.suffixes == [".nc"]
    assert record.storage_mode == "copy"


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
        / "files"
        / "CO2"
        / "EUROPE"
        / "CTE-HR"
        / "v4.2"
        / "anthropogenic"
        / "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc"
    )
    assert _stored_path(record) == expected
    assert expected.exists()
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

    expected = root / "files" / "CO2" / "GLOBAL" / "CTE-HR" / "CTE-HR_CO2_202401.nc"
    assert _stored_path(record) == expected
    assert record.record_type == "flux"
    assert record.naming_metadata["record_schema"] == "flux"
    assert expected.exists()


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

    expected = root / "files" / "CTE-HR.nc"
    assert _stored_path(record) == expected
    assert record.naming_metadata["directory_template"] == ""
    assert expected.exists()


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

    assert _stored_path(record).name == "floatyear.nc"


@pytest.mark.parametrize("field_name", ["id", "uuid", "operation_id"])
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
    assert list((root / "files").rglob("reserved.nc")) == []


def test_add_file_preserves_dotted_stems_and_simple_suffixes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    root = tmp_path / "catalog"
    catalog = Catalog.create(root, CatalogSpec(catalog_name="files"))

    record = catalog.add_file(source)

    expected = root / "files" / record.time_added[:4] / "anthropogenic.202401" / "anthropogenic.202401.nc"
    assert _stored_path(record) == expected
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

    expected = root / "files" / record.time_added[:4] / "archive" / "archive.tar.gz"
    assert _stored_path(record) == expected
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

    assert _stored_path(first_record).name == "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401.nc"
    assert _stored_path(second_record).name == "CTE-HR_v4.2_CO2_EUROPE_anthropogenic_202401_2.nc"


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

    assert _stored_path(first_record).name == "bundle.tar.gz"
    assert _stored_path(second_record).name == "bundle_2.tar.gz"


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

    monkeypatch.setattr("ogcat.catalog.render_storage_location", fail_render_storage_location)

    with pytest.raises(ValueError, match="simulated naming failure"):
        catalog.add_file(source)

    assert catalog.describe()["record_count"] == 0
    assert catalog.repository.all() == []
    assert list((root / "files").rglob("*.nc")) == []


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

    monkeypatch.setattr("ogcat.catalog.render_storage_location", fail_before_file_write)

    with pytest.raises(RuntimeError, match="simulated post-insert failure"):
        catalog.add_file(source)

    assert catalog.repository.all() == []
    assert list((root / "files").rglob("*.nc")) == []


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

    monkeypatch.setattr("ogcat.catalog.shutil.copy2", fail_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        catalog.add_file(source)

    assert catalog.describe()["record_count"] == 0
    assert list((root / "files").rglob("*.nc")) == []


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
    assert list((root / "files").rglob("copied.nc")) == []


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

    monkeypatch.setattr("ogcat.catalog.extract_derived_metadata", fail_extract)

    with pytest.raises(ValueError, match="simulated metadata failure"):
        catalog.add_file(source)

    assert source.exists()
    assert catalog.describe()["record_count"] == 0
    assert list((root / "files").rglob("metadata.nc")) == []


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

    assert list((root / "files").rglob("moved.nc")) == []
    assert source.exists()
    assert source.read_text(encoding="utf-8") == "dummy"
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
    assert record.stored_abspath is None
    assert catalog.path(_record_id(record)) is None


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
