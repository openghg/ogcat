from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.cli import app
from ogcat.models import ArtifactLocator, CatalogRecord

runner = CliRunner()


def _record_id(record: CatalogRecord) -> str:
    assert record.id is not None
    return record.id


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
        CatalogSpec(
            catalog_name="fluxes",
            default_schema=RecordSchema(metadata_fields=metadata_fields),
        ),
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
    record = catalog.search()[0]

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [item["id"] for item in payload] == [_record_id(record)]
    assert payload[0]["user_metadata"]["species"] == "CO2"


def test_search_limit_caps_human_output(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2", "title": f"record-{index}"},
            }
            for index in range(3)
        ]
    )

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--limit", "2"],
    )

    assert result.exit_code == 0
    assert "3 result(s)" in result.stdout
    assert "Showing 2 of 3 matches. Use --limit N, --all, or --json for more." in result.stdout
    assert records[0].id in result.stdout
    assert records[1].id in result.stdout
    assert "record-2" not in result.stdout


def test_search_all_disables_default_cap(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2", "title": f"record-{index}"},
            }
            for index in range(51)
        ]
    )

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--all"],
    )

    assert result.exit_code == 0
    assert "51 result(s)" in result.stdout
    assert "Showing 50 of 51 matches" not in result.stdout
    assert "record-50" in result.stdout


def test_search_default_cap_limits_human_output(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2", "title": f"record-{index}"},
            }
            for index in range(51)
        ]
    )

    result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "--where", "species=CO2"])

    assert result.exit_code == 0
    assert "51 result(s)" in result.stdout
    assert "Showing 50 of 51 matches. Use --limit N, --all, or --json for more." in result.stdout
    assert "record-49" in result.stdout
    assert "record-50" not in result.stdout


def test_search_fields_selects_human_output_columns(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--fields", "id,species,path"],
    )

    assert result.exit_code == 0
    assert "id" in result.stdout
    assert "species" in result.stdout
    assert "path" in result.stdout
    assert _record_id(record) in result.stdout
    assert "CO2" in result.stdout
    assert "anthropogenic" in result.stdout
    assert "files" in result.stdout
    assert "product" not in result.stdout
    assert "CTE-HR" not in result.stdout


def test_search_fields_supports_dotted_paths_and_locator_uri(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2", "domain": "EUROPE"},
    )

    result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "--where",
            "species=CO2",
            "--fields",
            "id,user_metadata.domain,locator.uri",
        ],
    )

    assert result.exit_code == 0
    assert "user_metadata.domain" in result.stdout
    assert "locator.uri" in result.stdout
    assert "EUROPE" in result.stdout
    assert "s3://bucket/example.zarr" in result.stdout


def test_search_json_ignores_fields_and_display_cap(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2", "title": f"record-{index}"},
            }
            for index in range(51)
        ]
    )

    result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "--where",
            "species=CO2",
            "--fields",
            "id,,species",
            "--limit",
            "2",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 51
    assert set(payload[0]) >= {"id", "locator", "user_metadata"}
    assert payload[0]["user_metadata"]["species"] == "CO2"


def test_search_rejects_limit_with_all(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--limit", "1", "--all"],
    )

    assert result.exit_code != 0
    assert "Use either --all or --limit, not both." in result.output


def test_show_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]

    result = runner.invoke(app, ["show", _record_id(record), "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == _record_id(record)
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
    record = Catalog.open(catalog.root).get("1")
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
    record = Catalog.open(catalog.root).get("1")
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
