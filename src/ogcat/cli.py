"""Command-line interface for ogcat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from ogcat.catalog import Catalog
from ogcat.spec import CatalogSpec

app = typer.Typer(
    help="Lightweight artifact catalog with managed file ingest.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
error_console = Console(stderr=True)


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


@app.command()
def search(
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
    ignore_case: Annotated[
        bool,
        typer.Option("--ignore-case", help="Case-insensitive matching."),
    ] = False,
    json_mode: Annotated[
        bool,
        typer.Option("--json", help="Print matching records as JSON."),
    ] = False,
    ids_only: Annotated[
        bool,
        typer.Option("--ids", help="Print only matching record ids."),
    ] = False,
    paths_only: Annotated[
        bool,
        typer.Option("--paths", help="Print only matching stored paths."),
    ] = False,
) -> None:
    """Search records in a catalog."""
    _validate_output_flags(json_mode=json_mode, ids_only=ids_only, paths_only=paths_only)
    active_catalog = _open_catalog_or_fail(catalog)
    results = active_catalog.search(
        where=_parse_meta_items([] if where is None else where),
        contains=_parse_key_value_options([] if contains is None else contains),
        regex=_parse_key_value_options([] if regex is None else regex),
        ignore_case=ignore_case,
    )

    if json_mode:
        _print_json([record.to_dict() for record in results])
        return

    if ids_only:
        for record in results:
            typer.echo(record.id)
        return

    if paths_only:
        for record in results:
            resolved = record.path()
            if resolved is not None:
                typer.echo(str(resolved))
        return

    if not results:
        console.print("No records matched.")
        return

    table = Table(title="ogcat search results")
    table.add_column("id")
    table.add_column("title")
    table.add_column("product")
    table.add_column("species")
    table.add_column("path")

    for record in results:
        resolved_path = record.path()
        table.add_row(
            record.id,
            str(record.user_metadata.get("title", "")),
            str(record.user_metadata.get("product", "")),
            str(record.user_metadata.get("species", "")),
            "" if resolved_path is None else str(resolved_path),
        )

    console.print(f"{len(results)} result(s)")
    console.print(table)


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
