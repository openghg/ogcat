from __future__ import annotations

import csv
import json
import re
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ogcat.operation_runner as operation_runner
from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.cli import app
from ogcat.models import ArtifactLocator, CatalogRecord

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
RICH_BOX_CHARS = ("╭", "╮", "╰", "╯", "│", "─", "├", "┤", "┬", "┴", "┼")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def assert_no_rich_boxes(text: str) -> None:
    """Assert CLI output does not contain Rich panel box-drawing characters."""
    assert not any(char in text for char in RICH_BOX_CHARS)


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


def test_init_preserves_existing_catalog(tmp_path: Path) -> None:
    """Repeated CLI init must fail without replacing the catalog spec."""
    root = tmp_path / "catalog"
    created = runner.invoke(app, ["init", str(root), "--name", "first"])
    original_spec = (root / "catalog.json").read_bytes()

    repeated = runner.invoke(app, ["init", str(root), "--name", "second"])

    assert created.exit_code == 0
    assert repeated.exit_code == 1
    assert "Catalog already exists" in strip_ansi(repeated.output)
    assert "Created catalog" not in strip_ansi(repeated.output)
    assert (root / "catalog.json").read_bytes() == original_spec
    assert Catalog.open(root).spec.catalog_name == "first"


def test_cli_releases_catalogs_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Command teardown closes database resources even when a command fails."""
    closed: list[Catalog] = []
    original_close = Catalog.close

    def tracked_close(catalog: Catalog) -> None:
        """Keep closed catalogs available to check their subsequent behavior."""
        original_close(catalog)
        closed.append(catalog)

    monkeypatch.setattr(Catalog, "close", tracked_close)
    root = str(tmp_path / "catalog")
    commands = [
        (["init", root, "--name", "files"], 0),
        (["search", "--catalog", root, "--json"], 0),
        (["add", str(tmp_path / "missing.nc"), "--catalog", root], 1),
        (["reference", "--uri", "s3://bucket/file.nc", "--catalog", root, "--meta", "invalid"], 2),
    ]
    for command, expected_exit in commands:
        previous_count = len(closed)
        result = runner.invoke(app, command)
        assert result.exit_code == expected_exit, result.output
        assert len(closed) == previous_count + 1
        with pytest.raises(RuntimeError, match="closed"):
            closed[-1].get("1")


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


def test_reference_cli_registers_local_and_remote_locators(tmp_path: Path) -> None:
    """CLI references keep path, URI, and fsspec URL path kinds distinct."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="refs"))
    source = tmp_path / "existing.nc"
    source.write_text("existing", encoding="utf-8")

    local = runner.invoke(
        app,
        ["reference", str(source), "--catalog", str(catalog.root), "--meta", "site=MHD", "--json"],
    )
    uri = runner.invoke(
        app,
        ["reference", "--uri", "s3://bucket/data.nc", "--catalog", str(catalog.root), "--json"],
    )
    urlpath = runner.invoke(
        app,
        ["reference", "--urlpath", "s3://bucket/data.nc", "--catalog", str(catalog.root), "--json"],
    )
    invalid = runner.invoke(
        app,
        ["reference", str(source), "--uri", "s3://bucket/data.nc", "--catalog", str(catalog.root)],
    )

    assert local.exit_code == uri.exit_code == urlpath.exit_code == 0
    assert json.loads(local.stdout)["locator"] == {
        "kind": "path",
        "value": str(source),
        "relative_path": None,
    }
    assert json.loads(local.stdout)["user_metadata"]["site"] == "MHD"
    assert json.loads(uri.stdout)["locator"]["kind"] == "uri"
    assert json.loads(urlpath.stdout)["locator"]["kind"] == "urlpath"
    assert invalid.exit_code != 0
    assert "exactly one" in strip_ansi(invalid.output)


def test_collection_cli_registers_existing_directory_and_lists_members(tmp_path: Path) -> None:
    """CLI collection registration and member listing preserve the stored pattern."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="footprints"))
    source = tmp_path / "MHD"
    source.mkdir()
    first = source / "MHD_202101.nc"
    second = source / "MHD_202102.nc"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    (source / "readme.txt").write_text("ignore", encoding="utf-8")

    added = runner.invoke(
        app,
        [
            "collection",
            str(source),
            "--catalog",
            str(catalog.root),
            "--meta",
            "site=MHD",
            "--record-type",
            "footprint_series",
            "--pattern",
            "*.nc",
            "--member-format",
            "netcdf",
            "--json",
        ],
    )

    assert added.exit_code == 0
    payload = json.loads(added.stdout)
    assert payload["record_type"] == "footprint_series"
    assert payload["derived_metadata"]["classification"]["collection_pattern"] == "*.nc"
    listed = runner.invoke(app, ["members", payload["id"], "--catalog", str(catalog.root)])
    assert listed.exit_code == 0
    assert listed.stdout.splitlines() == [str(first), str(second)]


def test_search_one_and_raw_locator_output(tmp_path: Path) -> None:
    """Strict search rejects ambiguity and raw locators cover remote records."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="refs"))
    first = catalog.add_reference(uri="s3://bucket/first.nc", metadata={"site": "MHD", "name": "first"})
    catalog.add_reference(urlpath="s3://bucket/second.nc", metadata={"site": "MHD", "name": "second"})

    ambiguous = runner.invoke(
        app, ["search", "--catalog", str(catalog.root), "site=MHD", "--one", "--locators"]
    )
    unique = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "name=first", "--one", "--locators"],
    )
    missing = runner.invoke(
        app, ["search", "--catalog", str(catalog.root), "site=ZZZ", "--one", "--locators"]
    )
    locator = runner.invoke(app, ["locator", _record_id(first), "--catalog", str(catalog.root)])

    assert ambiguous.exit_code != 0
    assert "multiple records" in strip_ansi(ambiguous.output)
    assert unique.exit_code == 0
    assert unique.stdout.splitlines() == ["s3://bucket/first.nc"]
    assert missing.exit_code != 0
    assert "no records" in strip_ansi(missing.output)
    assert locator.exit_code == 0
    assert locator.stdout.strip() == "s3://bucket/first.nc"


def test_add_and_show_surface_readable_template_path(tmp_path: Path) -> None:
    """Human CLI output includes the readable symlink beside the UUID primary."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    source = tmp_path / "example.nc"
    source.write_text("data", encoding="utf-8")

    added = runner.invoke(app, ["add", str(source), "--catalog", str(catalog.root)])

    assert added.exit_code == 0
    record = Catalog.open(catalog.root).search()[0]
    readable = Path(str(record.naming_metadata["template_replica_path"]))
    assert readable.is_symlink()
    assert f"Readable path: {readable}" in strip_ansi(added.output)
    shown = runner.invoke(app, ["show", _record_id(record), "--catalog", str(catalog.root)])
    assert shown.exit_code == 0
    assert "readable path" in strip_ansi(shown.output)
    path_result = runner.invoke(
        app, ["path", _record_id(record), "--catalog", str(catalog.root), "--readable"]
    )
    assert path_result.exit_code == 0
    assert path_result.stdout.strip() == str(readable)


def test_inspection_commands_open_catalog_read_only(tmp_path: Path, monkeypatch) -> None:
    """CLI reads use read-only repository access while registration stays writable."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    source = tmp_path / "example.nc"
    source.write_text("data", encoding="utf-8")
    record = catalog.add_file(source)
    collection_root = tmp_path / "footprints"
    collection_root.mkdir()
    collection = catalog.add_collection(collection_root, collection_pattern="*.nc")
    opened_read_only: list[bool] = []
    original_open = Catalog.open

    def tracked_open(cls: type[Catalog], root: str | Path, *, read_only: bool = False) -> Catalog:
        """Record the mode selected by CLI commands."""
        opened_read_only.append(read_only)
        return original_open(root, read_only=read_only)

    monkeypatch.setattr(Catalog, "open", classmethod(tracked_open))
    inspections = [
        ["search", "--ids"],
        ["show", _record_id(record), "--json"],
        ["path", _record_id(record)],
        ["locator", _record_id(record)],
        ["members", _record_id(collection)],
        ["info", "--json"],
        ["fields", "--json"],
        ["logs", "--json"],
        ["spec", "show-schema", "--json"],
    ]
    for command in inspections:
        result = runner.invoke(app, [*command, "--catalog", str(catalog.root)])
        assert result.exit_code == 0, result.output
        assert opened_read_only[-1] is True

    added = runner.invoke(app, ["reference", "--uri", "s3://bucket/new.nc", "--catalog", str(catalog.root)])
    assert added.exit_code == 0
    assert opened_read_only[-1] is False


def test_delete_restore_and_deleted_search_flags(tmp_path: Path) -> None:
    """CLI delete tombstones records and deleted-search flags expose them."""
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]
    record_id = _record_id(record)

    delete_result = runner.invoke(
        app,
        ["delete", record_id, "--catalog", str(catalog.root), "--reason", "duplicate", "--json"],
    )
    default_search = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--ids"],
    )
    include_search = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--where", "species=CO2", "--include-deleted", "--ids"],
    )
    only_deleted_search = runner.invoke(
        app,
        ["search", "--catalog", str(catalog.root), "--only-deleted", "--ids"],
    )
    restore_result = runner.invoke(app, ["restore", record_id, "--catalog", str(catalog.root), "--json"])

    assert delete_result.exit_code == 0
    delete_payload = json.loads(strip_ansi(delete_result.stdout))
    assert delete_payload["status"] == "deleted"
    assert delete_payload["lifecycle_metadata"]["delete_reason"] == "duplicate"
    assert default_search.exit_code == 0
    assert strip_ansi(default_search.stdout).splitlines() == []
    assert include_search.exit_code == 0
    assert strip_ansi(include_search.stdout).splitlines() == [record_id]
    assert only_deleted_search.exit_code == 0
    assert strip_ansi(only_deleted_search.stdout).splitlines() == [record_id]
    assert restore_result.exit_code == 0
    assert json.loads(strip_ansi(restore_result.stdout))["status"] == "active"


def test_search_rejects_conflicting_deleted_flags(tmp_path: Path) -> None:
    """CLI search should reject mutually exclusive deleted-record flags."""
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "--include-deleted",
            "--only-deleted",
        ],
    )

    assert result.exit_code != 0
    assert "Use either --include-deleted or --only-deleted" in strip_ansi(result.output)


def test_purge_cli_requires_confirmation_and_removes_deleted_record(tmp_path: Path) -> None:
    """CLI purge should require --yes and then permanently remove a tombstone."""
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]
    record_id = _record_id(record)
    path = record.path()
    assert path is not None
    delete_result = runner.invoke(app, ["delete", record_id, "--catalog", str(catalog.root)])

    rejected = runner.invoke(app, ["purge", record_id, "--catalog", str(catalog.root)])
    purged = runner.invoke(app, ["purge", record_id, "--catalog", str(catalog.root), "--yes", "--json"])
    show_result = runner.invoke(app, ["show", record_id, "--catalog", str(catalog.root), "--json"])

    assert delete_result.exit_code == 0
    assert rejected.exit_code == 2
    assert "Pass --yes to confirm" in strip_ansi(rejected.output)
    assert purged.exit_code == 0
    assert json.loads(strip_ansi(purged.stdout)) == {"id": record_id, "purged": True}
    assert show_result.exit_code != 0
    assert not path.exists()


def test_purge_cli_reports_incomplete_purge_without_success_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """CLI purge should not print a success payload when cleanup is incomplete."""
    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]
    record_id = _record_id(record)
    path = record.path()
    assert path is not None
    original_remove_target = operation_runner.remove_target

    def fail_remove_target(
        locator: ArtifactLocator,
        *,
        target_kind: operation_runner.TargetKind = "file",
    ) -> None:
        """Fail the managed file removal used by CLI purge."""
        if locator.as_path() == path:
            raise PermissionError("locked managed artifact")
        original_remove_target(locator, target_kind=target_kind)

    delete_result = runner.invoke(app, ["delete", record_id, "--catalog", str(catalog.root)])
    monkeypatch.setattr(operation_runner, "remove_target", fail_remove_target)

    result = runner.invoke(app, ["purge", record_id, "--catalog", str(catalog.root), "--yes", "--json"])

    assert delete_result.exit_code == 0
    assert result.exit_code != 0
    output = strip_ansi(result.output)
    assert "Purge incomplete" in output
    assert '"purged": true' not in output


def test_root_help_uses_plain_click_formatting() -> None:
    """Root help output should not contain Rich box-drawing panels."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "Options:" in stdout
    assert "Commands:" in stdout
    assert_no_rich_boxes(stdout)


def test_subcommand_help_uses_plain_click_formatting() -> None:
    """Subcommand help output should stay plain for direct and nested apps."""
    search_result = runner.invoke(app, ["search", "--help"])
    spec_result = runner.invoke(app, ["spec", "--help"])

    assert search_result.exit_code == 0
    search_stdout = strip_ansi(search_result.stdout)
    assert "Options:" in search_stdout
    assert_no_rich_boxes(search_stdout)

    assert spec_result.exit_code == 0
    spec_stdout = strip_ansi(spec_result.stdout)
    assert "Commands:" in spec_stdout
    assert_no_rich_boxes(spec_stdout)


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


def test_search_rejects_empty_search_fields_cleanly(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    positional_result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "=CO2"])
    contains_result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "--contains", "=CO2"])

    assert positional_result.exit_code != 0
    assert "Expected FIELD=VALUE: =CO2" in strip_ansi(positional_result.output)
    assert contains_result.exit_code != 0
    assert "Search option key cannot be empty: =CO2" in strip_ansi(contains_result.output)


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
    resolved_path = record.path()
    assert resolved_path is not None
    assert "objects" in stdout
    assert "product" not in stdout
    assert "CTE-HR" not in stdout

    plain_result = runner.invoke(
        app,
        [
            "search",
            "--catalog",
            str(catalog.root),
            "--where",
            "species=CO2",
            "--fields",
            "id,species,path",
            "--format",
            "pipe",
        ],
    )

    assert plain_result.exit_code == 0
    assert str(resolved_path) in strip_ansi(plain_result.stdout)


def test_search_uses_schema_display_fields_when_fields_are_omitted(tmp_path: Path) -> None:
    """Default search output uses schema display fields for homogeneous results."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="fluxes",
            record_schemas={
                "flux": RecordSchema(display_fields=["id", "species", "locator.uri"]),
            },
        ),
    )
    record = catalog.add_artifact(
        record_type="flux",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/flux.zarr"),
        metadata={"species": "CO2", "product": "GridFED"},
    )

    result = runner.invoke(app, ["search", "--catalog", str(catalog.root), "--where", "species=CO2"])

    assert result.exit_code == 0
    stdout = strip_ansi(result.stdout)
    assert "locator.uri" in stdout
    assert "s3://bucket/flux.zarr" in stdout
    assert _record_id(record) in stdout
    assert "product" not in stdout
    assert "GridFED" not in stdout


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


def test_show_and_path_commands_handle_numeric_looking_ids(tmp_path: Path) -> None:
    """Show and path commands should handle TinyDB numeric-looking record ids."""

    catalog = _create_catalog(tmp_path)
    record = catalog.search()[0]
    record_id = _record_id(record)

    show_result = runner.invoke(app, ["show", record_id, "--catalog", str(catalog.root), "--json"])
    path_result = runner.invoke(app, ["path", record_id, "--catalog", str(catalog.root)])

    assert show_result.exit_code == 0
    assert json.loads(strip_ansi(show_result.stdout))["id"] == record_id
    assert path_result.exit_code == 0
    assert strip_ansi(path_result.stdout).strip() == str(record.path())


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
    assert "schema-declared metadata fields" in stdout
    assert "species" in stdout
    assert "Gas species name used for grouping" in stdout


def test_fields_json_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert payload[0]["name"] == "species"
    assert payload[0]["required"] is True


def test_fields_stored_and_values_output(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path)

    stored_human_result = runner.invoke(app, ["fields", "--catalog", str(catalog.root), "--stored"])
    stored_result = runner.invoke(app, ["fields", "--catalog", str(catalog.root), "--stored", "--json"])
    values_result = runner.invoke(
        app,
        ["fields", "--catalog", str(catalog.root), "--values", "species", "--json"],
    )

    assert stored_human_result.exit_code == 0
    assert "stored record fields" in strip_ansi(stored_human_result.stdout)
    assert stored_result.exit_code == 0
    stored_payload = json.loads(strip_ansi(stored_result.stdout))
    assert "user_metadata.species" in stored_payload
    assert "path" in stored_payload
    assert values_result.exit_code == 0
    assert json.loads(strip_ansi(values_result.stdout)) == ["CO2"]


def test_fields_human_output_handles_missing_field_descriptions(tmp_path: Path) -> None:
    catalog = _create_catalog(tmp_path, with_fields=False)

    result = runner.invoke(app, ["fields", "--catalog", str(catalog.root)])

    assert result.exit_code == 0
    assert "No schema-declared metadata fields are defined in this catalog." in strip_ansi(result.stdout)


def test_spec_cli_adds_schema_sets_default_and_updates_simple_fields(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    schema_json = json.dumps(
        {
            "metadata_fields": [
                {"name": "title", "description": "Short title.", "required": True},
            ]
        }
    )

    add_result = runner.invoke(
        app,
        ["spec", "add-schema", "paper", "--catalog", str(catalog.root), "--schema-json", schema_json],
    )
    default_result = runner.invoke(
        app,
        ["spec", "set-default-schema", "paper", "--catalog", str(catalog.root)],
    )
    set_result = runner.invoke(
        app,
        [
            "spec",
            "set",
            "catalog_name=library",
            'field_resolution_order=["user_metadata","top_level"]',
            "--catalog",
            str(catalog.root),
        ],
    )

    reopened = Catalog.open(catalog.root)
    assert add_result.exit_code == 0
    assert default_result.exit_code == 0
    assert set_result.exit_code == 0
    assert reopened.spec.default_record_schema == "paper"
    assert reopened.spec.catalog_name == "library"
    assert reopened.spec.field_resolution_order == ["user_metadata", "top_level"]


def test_spec_cli_show_schema_json_includes_display_fields(tmp_path: Path) -> None:
    """spec show-schema --json emits the full serialisable schema."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="files",
            record_schemas={
                "flux": RecordSchema(
                    display_fields=["id", "species", "locator.uri"],
                    metadata_fields=[
                        MetadataFieldDescription(name="species", description="Gas species."),
                    ],
                ),
            },
        ),
    )

    result = runner.invoke(
        app,
        ["spec", "show-schema", "flux", "--catalog", str(catalog.root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert payload["display_fields"] == ["id", "species", "locator.uri"]
    assert payload["metadata_fields"][0]["name"] == "species"


def test_spec_cli_add_schema_accepts_file_path_with_outer_whitespace(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"metadata_fields": []}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "spec",
            "add-schema",
            "paper",
            "--catalog",
            str(catalog.root),
            "--schema-json",
            f"  {schema_path}  ",
        ],
    )

    assert result.exit_code == 0
    assert "paper" in Catalog.open(catalog.root).list_record_schemas()


def test_spec_cli_rejects_unknown_field_resolution_order_value(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    result = runner.invoke(
        app,
        ["spec", "set", 'field_resolution_order=["user_metadata","unknown"]', "--catalog", str(catalog.root)],
    )

    assert result.exit_code == 1
    assert "Unsupported field_resolution_order value(s): unknown" in strip_ansi(result.stderr)


def test_spec_cli_rejects_files_root_update(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    result = runner.invoke(
        app,
        ["spec", "set", "files_root=renamed-files", "--catalog", str(catalog.root)],
    )

    assert result.exit_code == 1
    assert "Changing files_root requires a storage-root migration operation." in strip_ansi(result.stderr)


def test_spec_cli_rejects_objects_root_update(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="files"))

    result = runner.invoke(
        app,
        ["spec", "set", "objects_root=renamed-objects", "--catalog", str(catalog.root)],
    )

    assert result.exit_code == 1
    assert "Changing objects_root requires a storage-root migration operation." in strip_ansi(result.stderr)


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


def test_logs_json_filters_by_user(tmp_path: Path) -> None:
    """The logs command should expose filtered structured audit events."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(catalog_name="fluxes"),
        audit_user_id="cli-user",
    )
    source = tmp_path / "source.nc"
    source.write_text("dummy", encoding="utf-8")
    record = catalog.add_file(source, metadata={"species": "CO2"})

    result = runner.invoke(
        app,
        ["logs", "--catalog", str(catalog.root), "--user", "cli-user", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(strip_ansi(result.stdout))
    assert {event["user_id"] for event in payload} == {"cli-user"}
    assert "operation-started" in {event["event_type"] for event in payload}
    assert any(
        event["event_type"] == "commit" and event["record_id"] == _record_id(record) for event in payload
    )


def test_add_failure_error_includes_operation_id(tmp_path: Path) -> None:
    """CLI add failures should expose the operation id for audit correlation."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="fluxes",
            default_schema=RecordSchema(
                metadata_fields=[
                    MetadataFieldDescription(
                        name="species",
                        description="Gas species.",
                        required=True,
                    )
                ]
            ),
        ),
    )
    source = tmp_path / "source.nc"
    source.write_text("dummy", encoding="utf-8")

    result = runner.invoke(app, ["add", str(source), "--catalog", str(catalog.root)])

    assert result.exit_code == 1
    stderr = strip_ansi(result.stderr)
    assert "Missing required metadata for schema default: species" in stderr
    assert "operation_id:" in stderr


def test_show_missing_record_returns_helpful_error(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    result = runner.invoke(app, ["show", "missing", "--catalog", str(catalog.root)])

    assert result.exit_code == 1
    assert "Error: Record not found: missing" in strip_ansi(result.stderr)


def test_missing_catalog_configuration_returns_helpful_error() -> None:
    result = runner.invoke(app, ["info"])

    assert result.exit_code != 0
    assert "Provide --catalog or set OGCAT_CATALOG." in strip_ansi(result.stderr)


def test_missing_command_error_is_plain_text() -> None:
    """Missing-command usage errors should not render Rich error panels."""
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    stderr = strip_ansi(result.stderr)
    assert "Error: Missing command." in stderr
    assert_no_rich_boxes(stderr)


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
    stored_record = catalog.search(where={"species": "CO2"}, as_record_set=False)[0]
    assert lines[0] == str(stored_record.path())


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
