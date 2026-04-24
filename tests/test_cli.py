from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription
from ogcat.cli import app
from ogcat.models import ArtifactLocator

runner = CliRunner()


def _create_catalog(tmp_path: Path, *, with_fields: bool = True) -> Catalog:
    metadata_fields = (
        [
            MetadataFieldDescription(
                name="species",
                description="Gas species name used for grouping and search.",
                example="CO2",
                required=True,
            ),
            MetadataFieldDescription(
                name="product",
                description="Upstream product identifier.",
                example="CTE-HR",
            ),
        ]
        if with_fields
        else []
    )
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="fluxes", metadata_fields=metadata_fields),
    )
    source = tmp_path / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")
    catalog.add_file(
        source,
        metadata={
            "title": "Anthropogenic test flux",
            "product": "CTE-HR",
            "species": "CO2",
            "version": "v4.2",
        },
    )
    return catalog


def test_search_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [item["id"] for item in payload] == ["rec_000001"]
    assert payload[0]["user_metadata"]["species"] == "CO2"


def test_show_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["show", "rec_000001", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == "rec_000001"
    assert payload["record_type"] == "managed_file"
    assert payload["locator"]["kind"] == "path"
    assert payload["user_metadata"]["title"] == "Anthropogenic test flux"


def test_info_human_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["info", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    assert "catalog info" in result.stdout
    assert "fluxes" in result.stdout
    assert "record count" in result.stdout


def test_info_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["info", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["catalog_name"] == "fluxes"
    assert payload["record_count"] == 1
    assert payload["has_metadata_fields"] is True


def test_fields_human_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    assert "metadata fields" in result.stdout
    assert "species" in result.stdout
    assert "Gas species name used for grouping" in result.stdout


def test_fields_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["name"] == "species"
    assert payload[0]["required"] is True


def test_fields_human_output_handles_missing_field_descriptions(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path, with_fields=False)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    assert "No metadata fields are defined in this catalog." in result.stdout


def test_add_accepts_multiple_metadata_items_after_single_meta_flag(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    source = tmp_path / "source.nc"
    source.write_text("dummy", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "add",
            str(source),
            "--catalog",
            str(catalog.root),
            "--meta",
            "species=CO2",
            "product=CTE-HR",
            'version="v4.2"',
        ],
    )

    assert result.exit_code == 0
    record = Catalog.open(catalog.root).get("rec_000001")
    assert record is not None
    assert record.user_metadata == {"species": "CO2", "product": "CTE-HR", "version": "v4.2"}


def test_add_accepts_json_object_metadata(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    source = tmp_path / "source.nc"
    source.write_text("dummy", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "add",
            str(source),
            "--catalog",
            str(catalog.root),
            "--meta",
            '{"species": "CO2", "month": 1}',
        ],
    )

    assert result.exit_code == 0
    record = Catalog.open(catalog.root).get("rec_000001")
    assert record is not None
    assert record.user_metadata == {"species": "CO2", "month": 1}


def test_show_missing_record_returns_helpful_error(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    result = runner.invoke(app, ["show", "missing", "--catalog", str(catalog.root)])

    assert result.exit_code == 1
    assert "Error: Record not found: missing" in result.stderr


def test_missing_catalog_configuration_returns_helpful_error() -> None:
    result = runner.invoke(app, ["info"])

    assert result.exit_code != 0
    assert "Provide --catalog or set OGCAT_CATALOG." in result.stderr


def test_search_paths_skips_non_path_backed_records(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    source = tmp_path / "source.nc"
    source.write_text("dummy", encoding="utf-8")
    catalog.add_file(source, metadata={"species": "CO2"})
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2"},
    )

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--paths"],
    )

    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("source.nc")


def test_search_human_output_uses_empty_path_for_non_path_backed_records(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2", "title": "Remote artifact"},
    )

    result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "--where", "species=CO2"])

    assert result.exit_code == 0
    assert "Remote artifact" in result.stdout
    assert "None" not in result.stdout
