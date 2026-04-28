from __future__ import annotations

import csv
import json
import re
from io import StringIO
from pathlib import Path

from typer.testing import CliRunner

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.cli import app
from ogcat.models import ArtifactLocator, CatalogRecord

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


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
    payload = json.loads(strip_ansi(result.stdout))
    assert [item["id"] for item in payload] == [_record_id(record)]
    assert payload[0]["user_metadata"]["species"] == "CO2"


def test_search_accepts_positional_key_value_filters(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "species=CO2", "--ids"],
    )

    assert result.exit_code == 0
    assert strip_ansi(result.stdout).splitlines() == [_record_id(record)]


def test_search_cli_supports_nested_list_and_missing_filters(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    paris = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/paris.zarr"),
        metadata={"tags": ["paris", "obspack"], "site": {"code": "MHD"}, "title": "Paris ObsPack"},
    )
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/baseline.zarr"),
        metadata={"tags": ["baseline"], "title": "Baseline"},
    )

    result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "tags:paris",
            "user.site.code?",
            "!user.platform?",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert [item["id"] for item in payload] == [_record_id(paris)]
    assert payload[0]["user_metadata"]["site"]["code"] == "MHD"


def test_search_contains_flag_parses_json_values(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/paris.zarr"),
        metadata={"months": [1, 2], "site": {"code": "MHD", "country": "IE"}},
    )

    numeric_result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--contains", "months=2", "--ids"],
    )
    mapping_result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--contains", 'site={"code":"MHD"}', "--ids"],
    )

    assert numeric_result.exit_code == 0
    assert strip_ansi(numeric_result.stdout).splitlines() == [_record_id(record)]
    assert mapping_result.exit_code == 0
    assert strip_ansi(mapping_result.stdout).splitlines() == [_record_id(record)]


def test_search_cli_match_filter_supports_locator_uri(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"title": "ObsPack Paris product"},
    )
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/other.zarr"),
        metadata={"title": "Other product"},
    )

    result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "--match",
            "locator.uri=s3://bucket/*.zarr",
            "title~paris",
            "--ignore-case",
            "--ids",
        ],
    )

    assert result.exit_code == 0
    assert strip_ansi(result.stdout).splitlines() == [_record_id(record)]


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
    stdout = strip_ansi(result.stdout)
    assert "3 result(s)" in stdout
    assert "Showing 2 of 3 matches. Use --limit N, --all, or --json for more." in stdout
    assert _record_id(records[0]) in stdout
    assert _record_id(records[1]) in stdout
    assert "record-2" not in stdout


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
    stdout = strip_ansi(result.stdout)
    assert "51 result(s)" in stdout
    assert "Showing 50 of 51 matches" not in stdout
    assert "record-50" in stdout


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
    stdout = strip_ansi(result.stdout)
    assert "51 result(s)" in stdout
    assert "Showing 50 of 51 matches. Use --limit N, --all, or --json for more." in stdout
    assert "record-49" in stdout
    assert "record-50" not in stdout


def test_search_fields_selects_human_output_columns(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--fields", "id,species,path"],
    )

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "id" in stdout
    assert "species" in stdout
    assert "path" in stdout
    assert _record_id(record) in stdout
    assert "CO2" in stdout
    assert "anthropogenic" in stdout
    assert "files" in stdout
    assert "product" not in stdout
    assert "CTE-HR" not in stdout


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
    stdout = strip_ansi(result.stdout)
    assert "user_metadata.domain" in stdout
    assert "locator.uri" in stdout
    assert "EUROPE" in stdout
    assert "s3://bucket/example.zarr" in stdout


def test_search_format_tsv_outputs_data_without_rich_table(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    records = catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2"},
            }
            for index in range(2)
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
            "id,species,locator.uri",
            "--format",
            "tsv",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert strip_ansi(result.stdout).splitlines() == [
        "id\tspecies\tlocator.uri",
        f"{_record_id(records[0])}\tCO2\ts3://bucket/record-0.zarr",
    ]
    assert "result(s)" not in strip_ansi(result.stdout)
    assert "Showing 1 of 2 matches. Use --limit N, --all, or --json for more." in strip_ansi(result.stderr)


def test_search_format_csv_quotes_values(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2", "title": "A value, with comma"},
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
            "id,title",
            "--format",
            "csv",
        ],
    )

    assert result.exit_code == 0
    rows = list(csv.reader(StringIO(strip_ansi(result.stdout))))
    assert rows == [
        ["id", "title"],
        [_record_id(record), "A value, with comma"],
    ]


def test_search_rejects_unknown_format_for_display_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--format", "yaml"],
    )

    assert result.exit_code != 0
    assert "Expected one of: table, plain, csv, tsv, pipe." in strip_ansi(result.output)


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
            "--format",
            "not-a-format",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
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
    assert "Use either --all or --limit, not both." in strip_ansi(result.output)


def test_show_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]

    result = runner.invoke(app, ["show", _record_id(record), "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert payload["id"] == _record_id(record)
    assert payload["record_type"] == "managed_file"
    assert payload["locator"]["kind"] == "path"
    assert payload["user_metadata"]["title"] == "Anthropogenic test flux"


def test_info_human_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["info", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "catalog info" in stdout
    assert "fluxes" in stdout
    assert "record count" in stdout


def test_info_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["info", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert payload["catalog_name"] == "fluxes"
    assert payload["record_count"] == 1
    assert payload["has_metadata_fields"] is True


def test_fields_human_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "metadata fields" in stdout
    assert "species" in stdout
    assert "Gas species name used for grouping" in stdout


def test_fields_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert payload[0]["name"] == "species"
    assert payload[0]["required"] is True


def test_fields_human_output_handles_missing_field_descriptions(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path, with_fields=False)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    assert "No metadata fields are defined in this catalog." in strip_ansi(result.stdout)


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
    assert "Error: Record not found: missing" in strip_ansi(result.stderr)


def test_missing_catalog_configuration_returns_helpful_error() -> None:
    result = runner.invoke(app, ["info"])

    assert result.exit_code != 0
    assert "Provide --catalog or set OGCAT_CATALOG." in strip_ansi(result.stderr)


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
    lines = [line for line in strip_ansi(result.stdout).splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("source.nc")


def test_search_ids_and_paths_are_not_capped_by_default(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    for index in range(51):
        source = tmp_path / f"source-{index}.nc"
        source.write_text("dummy", encoding="utf-8")
        catalog.add_file(source, metadata={"species": "CO2"})

    ids_result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--ids"],
    )
    paths_result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--paths"],
    )

    assert ids_result.exit_code == 0
    assert paths_result.exit_code == 0
    assert len([line for line in strip_ansi(ids_result.stdout).splitlines() if line.strip()]) == 51
    assert len([line for line in strip_ansi(paths_result.stdout).splitlines() if line.strip()]) == 51


def test_search_limit_can_cap_ids_output(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifacts(
        [
            {
                "record_type": "external_reference",
                "locator": ArtifactLocator(kind="uri", value=f"s3://bucket/record-{index}.zarr"),
                "metadata": {"species": "CO2"},
            }
            for index in range(3)
        ]
    )

    result = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--ids", "--limit", "2"],
    )

    assert result.exit_code == 0
    assert strip_ansi(result.stdout).splitlines() == ["1", "2"]


def test_search_human_output_uses_empty_path_for_non_path_backed_records(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/example.zarr"),
        metadata={"species": "CO2", "title": "Remote artifact"},
    )

    result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "--where", "species=CO2"])

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "Remote artifact" in stdout
    assert "None" not in stdout
