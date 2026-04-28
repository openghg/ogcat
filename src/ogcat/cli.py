"""Command-line interface for ogcat."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from ogcat.catalog import Catalog
from ogcat.models import CatalogRecord
from ogcat.search import SearchQuery, flatten_lookup
from ogcat.spec import CatalogSpec

app = typer.Typer(
    help="Lightweight artifact catalog with managed file ingest.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
error_console = Console(stderr=True)
DEFAULT_SEARCH_LIMIT = 50
DEFAULT_SEARCH_FIELDS = ["id", "title", "product", "species", "path"]
SearchOutputFormat = Literal["table", "plain", "csv", "tsv", "pipe"]


def _fail(message: str, *, code: int = 1) -> NoReturn:
    """Print a consistent error message and exit."""
    error_console.print(f"Error: {message}")
    raise typer.Exit(code=code)


def _resolve_catalog_path(catalog: Path | None) -> Path:
    """Resolve the active catalog path from CLI option or environment."""
    if catalog is not None:
        return catalog
    env_value = os.environ.get("OGCAT_CATALOG")
    if env_value:
        return Path(env_value)
    _fail("Provide --catalog or set OGCAT_CATALOG.")


def _open_catalog_or_fail(catalog: Path | None) -> Catalog:
    """Open a catalog using CLI resolution semantics with friendly errors."""
    catalog_path = _resolve_catalog_path(catalog)
    try:
        return Catalog.open(catalog_path)
    except FileNotFoundError:
        _fail(f"Catalog not found or incomplete at {catalog_path}.")
    except ValueError as exc:
        _fail(str(exc))


def _print_json(payload: object) -> None:
    """Print stable machine-readable JSON."""
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _validate_output_flags(*, json_mode: bool, ids_only: bool = False, paths_only: bool = False) -> None:
    """Reject incompatible output mode combinations."""
    enabled = [flag for flag in [json_mode, ids_only, paths_only] if flag]
    if len(enabled) > 1:
        raise typer.BadParameter("Choose only one of --json, --ids, or --paths.")


def _parse_meta_item(item: str) -> dict[str, Any]:
    """Parse one metadata item from KEY=VALUE or a JSON object."""
    stripped = item.strip()
    if not stripped:
        raise typer.BadParameter("Metadata item cannot be empty.")
    if "=" in stripped:
        key, raw_value = stripped.split("=", 1)
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        return {key: value}
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Metadata item must be KEY=VALUE or a JSON object: {item}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter(f"Metadata JSON must be an object: {item}")
    return value


def _parse_meta_items(items: list[str]) -> dict[str, Any]:
    """Parse repeated KEY=VALUE items from the CLI.

    Values are parsed as JSON when possible, otherwise treated as strings.
    A JSON object can also be supplied to set multiple metadata keys at once.
    """
    parsed: dict[str, Any] = {}
    for item in items:
        parsed.update(_parse_meta_item(item))
    return parsed


def _parse_key_value_options(items: list[str]) -> dict[str, str]:
    """Parse repeated KEY=VALUE options."""
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"Expected KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def _parse_search_expression(item: str) -> SearchQuery:
    """Parse one simple positional search expression."""
    if "~=" in item:
        key, value = item.split("~=", 1)
        if not key:
            raise typer.BadParameter(f"Expected FIELD~=VALUE: {item}")
        return SearchQuery.contains(key, value)
    if "~" in item:
        key, value = item.split("~", 1)
        if not key:
            raise typer.BadParameter(f"Expected FIELD~PATTERN: {item}")
        return SearchQuery.matches(key, value)
    if "=" in item:
        parsed = _parse_meta_item(item)
        if len(parsed) != 1:
            raise typer.BadParameter(f"Expected one search expression: {item}")
        field, value = next(iter(parsed.items()))
        return SearchQuery.equals(field, value)
    raise typer.BadParameter(f"Expected FIELD=VALUE, FIELD~=VALUE, or FIELD~PATTERN: {item}")


def _parse_search_expressions(items: list[str]) -> SearchQuery:
    """Parse positional search expressions and combine them with AND semantics."""
    query = SearchQuery.all()
    for item in items:
        query = query.and_(_parse_search_expression(item))
    return query


def _parse_fields_option(fields: str | None) -> list[str] | None:
    """Parse a comma-separated display field list."""
    if fields is None:
        return None
    parsed = [field.strip() for field in fields.split(",")]
    if any(not field for field in parsed):
        raise typer.BadParameter("--fields must be a comma-separated list of field names.")
    return parsed


def _parse_search_output_format(output_format: str) -> SearchOutputFormat:
    """Parse the search table/delimited output format."""
    if output_format == "table":
        return "table"
    if output_format == "plain":
        return "plain"
    if output_format == "csv":
        return "csv"
    if output_format == "tsv":
        return "tsv"
    if output_format == "pipe":
        return "pipe"
    raise typer.BadParameter("Expected one of: table, plain, csv, tsv, pipe.")


def _resolve_display_field(
    record: CatalogRecord,
    field: str,
    *,
    resolution_order: list[str],
) -> Any:
    """Resolve a display field without changing search semantics."""
    if field == "path":
        return record.path()
    if field == "locator.uri":
        return record.locator.value if record.locator.kind == "uri" else None
    return flatten_lookup(record, field, resolution_order=resolution_order)


def _format_display_value(value: Any) -> str:
    """Format a display value for table output."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _search_display_rows(
    records: list[CatalogRecord],
    *,
    fields: list[str],
    resolution_order: list[str],
) -> list[list[str]]:
    """Resolve search records into display-ready string rows."""
    return [
        [
            _format_display_value(
                _resolve_display_field(
                    record,
                    field,
                    resolution_order=resolution_order,
                )
            )
            for field in fields
        ]
        for record in records
    ]


def _print_search_table(*, fields: list[str], rows: list[list[str]]) -> None:
    """Print search results as a Rich table."""
    table = Table(title="ogcat search results")
    for field in fields:
        table.add_column(field, overflow="fold")
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _print_delimited_search_output(
    *,
    fields: list[str],
    rows: list[list[str]],
    delimiter: str,
) -> None:
    """Print search results as delimiter-separated text with a header row."""
    writer = csv.writer(sys.stdout, delimiter=delimiter, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)


def _displayed_results(
    results: list[CatalogRecord],
    *,
    limit: int | None,
    all_results: bool,
    json_mode: bool,
) -> list[CatalogRecord]:
    """Apply display-only result capping."""
    if json_mode or all_results or limit is None:
        return results
    return results[:limit]


@app.command()
def init(
    root: Annotated[Path, typer.Argument(help="Catalog root directory.")],
    name: Annotated[str, typer.Option("--name", help="Catalog name.")],
) -> None:
    """Create a new catalog."""
    spec = CatalogSpec(catalog_name=name)
    catalog = Catalog.create(root, spec)
    console.print(f"Created catalog at {catalog.root}")


@app.command(
    "add",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": False},
)
def add_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Source file to add.")],
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
    meta: Annotated[
        list[str] | None,
        typer.Option(
            "--meta",
            help=(
                "Metadata item KEY=VALUE. Repeatable. Additional KEY=VALUE items may follow a single --meta."
            ),
        ),
    ] = None,
    operation: Annotated[str | None, typer.Option("--operation", help="copy or move.")] = None,
    record_type: Annotated[
        str | None,
        typer.Option("--record-type", help="Named record schema to use for this file."),
    ] = None,
) -> None:
    """Add a file to the catalog."""
    extra_meta_items = list(ctx.args)
    meta_items = [] if meta is None else meta
    if extra_meta_items and not meta_items:
        raise typer.BadParameter("Unexpected extra arguments. Use --meta KEY=VALUE to supply metadata.")

    active_catalog = _open_catalog_or_fail(catalog)
    metadata = _parse_meta_items([*meta_items, *extra_meta_items])
    try:
        record = active_catalog.add_file(
            path,
            metadata=metadata,
            operation=operation,
            record_type=record_type,
        )
    except ValueError as exc:
        _fail(str(exc))
    console.print(f"Added {record.id}: {record.stored_abspath}")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": False},
)
def search(
    ctx: typer.Context,
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
    where: Annotated[
        list[str] | None,
        typer.Option("--where", help="Equality filter KEY=VALUE. Repeatable."),
    ] = None,
    contains: Annotated[
        list[str] | None,
        typer.Option("--contains", help="Substring filter KEY=VALUE. Repeatable."),
    ] = None,
    regex: Annotated[
        list[str] | None,
        typer.Option("--regex", help="Regex filter KEY=VALUE. Repeatable."),
    ] = None,
    match: Annotated[
        list[str] | None,
        typer.Option("--match", help="Glob or substring filter KEY=VALUE. Repeatable."),
    ] = None,
    exists: Annotated[
        list[str] | None,
        typer.Option("--exists", help="Require FIELD to be present. Repeatable."),
    ] = None,
    missing: Annotated[
        list[str] | None,
        typer.Option("--missing", help="Require FIELD to be absent. Repeatable."),
    ] = None,
    ignore_case: Annotated[
        bool,
        typer.Option("--ignore-case", help="Case-insensitive matching."),
    ] = False,
    json_mode: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print full matching records as JSON. Ignores --fields and default display cap.",
        ),
    ] = False,
    ids_only: Annotated[
        bool,
        typer.Option("--ids", help="Print only matching record ids."),
    ] = False,
    paths_only: Annotated[
        bool,
        typer.Option("--paths", help="Print only matching stored paths."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=0, help="Cap displayed results. Does not affect --json output."),
    ] = None,
    all_results: Annotated[
        bool,
        typer.Option("--all", help="Show all results, disabling the default display cap."),
    ] = False,
    fields: Annotated[
        str | None,
        typer.Option(
            "--fields",
            help=(
                "Comma-separated display fields. Supports flattened names and dotted paths. "
                "Ignored with --json, --ids, and --paths."
            ),
        ),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help=(
                "Display format: table, plain, csv, tsv, or pipe. Ignored with --json, --ids, and --paths."
            ),
        ),
    ] = "table",
) -> None:
    """Search records in a catalog."""
    _validate_output_flags(json_mode=json_mode, ids_only=ids_only, paths_only=paths_only)
    if all_results and limit is not None:
        raise typer.BadParameter("Use either --all or --limit, not both.")
    display_limit = limit if limit is not None else DEFAULT_SEARCH_LIMIT
    display_fields = DEFAULT_SEARCH_FIELDS
    parsed_output_format: SearchOutputFormat = "table"
    if not any([json_mode, ids_only, paths_only]):
        display_fields = _parse_fields_option(fields) or DEFAULT_SEARCH_FIELDS
        parsed_output_format = _parse_search_output_format(output_format)
    active_catalog = _open_catalog_or_fail(catalog)
    query = SearchQuery.from_filters(
        where=_parse_meta_items([] if where is None else where),
        contains=_parse_key_value_options([] if contains is None else contains),
        regex=_parse_key_value_options([] if regex is None else regex),
        match=_parse_key_value_options([] if match is None else match),
        exists=[] if exists is None else exists,
        missing=[] if missing is None else missing,
    ).and_(_parse_search_expressions(list(ctx.args)))
    results = active_catalog.search(
        query=query,
        ignore_case=ignore_case,
    )

    if json_mode:
        _print_json([record.to_dict() for record in results])
        return

    result_limit = limit if any([ids_only, paths_only]) else display_limit
    shown_results = _displayed_results(
        results,
        limit=result_limit,
        all_results=all_results,
        json_mode=json_mode,
    )

    if ids_only:
        for record in shown_results:
            typer.echo(record.id)
        return

    if paths_only:
        for record in shown_results:
            resolved = record.path()
            if resolved is not None:
                typer.echo(str(resolved))
        return

    if not results and parsed_output_format == "table":
        console.print("No records matched.")
        return

    rows = _search_display_rows(
        shown_results,
        fields=display_fields,
        resolution_order=active_catalog.spec.field_resolution_order,
    )

    if parsed_output_format != "table":
        delimiters = {"plain": " ", "csv": ",", "tsv": "\t", "pipe": "|"}
        _print_delimited_search_output(
            fields=display_fields,
            rows=rows,
            delimiter=delimiters[parsed_output_format],
        )
        if len(shown_results) < len(results):
            error_console.print(
                f"Showing {len(shown_results)} of {len(results)} matches. "
                "Use --limit N, --all, or --json for more."
            )
        return

    console.print(f"{len(results)} result(s)")
    if len(shown_results) < len(results):
        console.print(
            f"Showing {len(shown_results)} of {len(results)} matches. "
            "Use --limit N, --all, or --json for more."
        )
    _print_search_table(fields=display_fields, rows=rows)


@app.command()
def show(
    record_id: Annotated[str, typer.Argument(help="Record id.")],
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
    json_mode: Annotated[
        bool,
        typer.Option("--json", help="Print the record as JSON."),
    ] = False,
) -> None:
    """Show a single record."""
    active_catalog = _open_catalog_or_fail(catalog)
    record = active_catalog.get(record_id)
    if record is None:
        _fail(f"Record not found: {record_id}")

    if json_mode:
        _print_json(record.to_dict())
        return

    table = Table(title=f"record {record.id}", show_header=False)
    table.add_column("field")
    table.add_column("value")
    table.add_row("id", record.id)
    table.add_row("catalog", record.catalog)
    table.add_row("record type", record.record_type)
    table.add_row("locator", json.dumps(record.locator.to_dict(), sort_keys=True))
    table.add_row("stored path", str(record.stored_abspath or ""))
    table.add_row("relative path", str(record.stored_relpath or ""))
    table.add_row("storage mode", str(record.storage_mode or ""))
    table.add_row("time added", record.time_added)
    table.add_row("original path", str(record.original_path or ""))
    table.add_row("original filename", str(record.original_filename or ""))
    table.add_row("suffixes", ", ".join(record.suffixes))
    table.add_row("user metadata", json.dumps(record.user_metadata, sort_keys=True))
    table.add_row("derived metadata", json.dumps(record.derived_metadata, sort_keys=True))
    table.add_row("naming metadata", json.dumps(record.naming_metadata, sort_keys=True))
    console.print(table)


@app.command()
def path(
    record_id: Annotated[str, typer.Argument(help="Record id.")],
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
) -> None:
    """Print the stored path for a record."""
    active_catalog = _open_catalog_or_fail(catalog)
    record = active_catalog.get(record_id)
    if record is None:
        _fail(f"Record not found: {record_id}")
    resolved = record.path()
    if resolved is None:
        _fail(f"Record is not path-backed: {record_id}")
    console.print(str(resolved))


@app.command()
def info(
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
    json_mode: Annotated[
        bool,
        typer.Option("--json", help="Print catalog info as JSON."),
    ] = False,
) -> None:
    """Show a curated catalog overview."""
    active_catalog = _open_catalog_or_fail(catalog)
    description = active_catalog.describe()

    if json_mode:
        _print_json(description)
        return

    table = Table(title="catalog info", show_header=False)
    table.add_column("field")
    table.add_column("value")
    table.add_row("catalog name", str(description["catalog_name"]))
    table.add_row("root path", str(description["root_path"]))
    table.add_row("backend", str(description["backend"]))
    table.add_row("database path", str(description["database_path"]))
    table.add_row("files root", str(description["files_root"]))
    table.add_row("default operation", str(description["default_operation"]))
    table.add_row("directory template", str(description["directory_template"]))
    table.add_row("filename template", str(description["filename_template"]))
    field_resolution_order = description["field_resolution_order"]
    if not isinstance(field_resolution_order, list):
        field_resolution_order = []
    table.add_row(
        "field resolution order",
        " -> ".join(str(item) for item in field_resolution_order),
    )
    table.add_row("record count", str(description["record_count"]))
    table.add_row("metadata fields present", "yes" if description["has_metadata_fields"] else "no")
    record_schemas = description["record_schemas"]
    if not isinstance(record_schemas, list):
        record_schemas = []
    table.add_row("record schemas", ", ".join(str(item) for item in record_schemas) or "none")
    console.print(table)


@app.command()
def fields(
    catalog: Annotated[Path | None, typer.Option("--catalog", help="Catalog root.")] = None,
    record_type: Annotated[
        str | None,
        typer.Option("--record-type", help="Named record schema whose fields should be shown."),
    ] = None,
    json_mode: Annotated[
        bool,
        typer.Option("--json", help="Print metadata field descriptions as JSON."),
    ] = False,
) -> None:
    """List important metadata fields from the catalog spec."""
    active_catalog = _open_catalog_or_fail(catalog)
    try:
        metadata_fields = active_catalog.list_metadata_fields(record_type=record_type)
    except ValueError as exc:
        _fail(str(exc))

    if json_mode:
        _print_json(metadata_fields)
        return

    if not metadata_fields:
        console.print("No metadata fields are defined in this catalog.")
        return

    table = Table(title="metadata fields", expand=True)
    table.add_column("name")
    table.add_column("required")
    table.add_column("type")
    table.add_column("description", overflow="fold")
    table.add_column("example")

    for field_description in metadata_fields:
        value_types = field_description.get("type", [])
        if not isinstance(value_types, list):
            value_types = []
        table.add_row(
            str(field_description["name"]),
            "yes" if field_description.get("required") else "no",
            " | ".join(str(item) for item in value_types),
            str(field_description["description"]),
            "" if field_description.get("example") is None else json.dumps(field_description["example"]),
        )

    console.print(table)
