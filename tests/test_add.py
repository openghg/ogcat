from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec
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
            directory_template=(
                "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                "{version|unversioned}/{flux_type|misc}"
            ),
            filename_template=(
                "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                "{original_suffix}"
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
            directory_template=(
                "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
                "{version|unversioned}/{flux_type|misc}"
            ),
            filename_template=(
                "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
                "{original_suffix}"
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
            directory_template="{year_added}",
            filename_template="bundle{original_suffix}",
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
