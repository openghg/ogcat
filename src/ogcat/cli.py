"""Command-line interface for ogcat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ogcat.catalog import Catalog
from ogcat.spec import CatalogSpec

app = typer.Typer(
    help="Lightweight local file catalog.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


def _resolve_catalog_path(catalog: Path | None) -> Path:
    """Resolve the active catalog path from CLI option or environment."""
    if catalog is not None:
        return catalog
    env_value = os.environ.get("OGCAT_CATALOG")
    if env_value:
        return Path(env_value)
    raise typer.BadParameter("Provide --catalog or set OGCAT_CATALOG.")


def _parse_meta_items(items: list[str]) -> dict[str, Any]:
    """Parse repeated KEY=VALUE items from the CLI.

    Values are parsed as JSON when possible, otherwise treated as strings.
    """
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"Metadata item must be KEY=VALUE: {item}")
        key, raw_value = item.split("=", 1)
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        parsed[key] = value
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
    root: Path = typer.Argument(..., help="Catalog root directory."),
    name: str = typer.Option(..., "--name", help="Catalog name."),
) -> None:
    """Create a new catalog."""
    spec = CatalogSpec(catalog_name=name)
    catalog = Catalog.create(root, spec)
    console.print(f"Created catalog at {catalog.root}")


@app.command("add")
def add_command(
    path: Path = typer.Argument(..., help="Source file to add."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Catalog root."),
    meta: list[str] = typer.Option([], "--meta", help="Metadata item KEY=VALUE. Repeatable."),
    operation: str | None = typer.Option(None, "--operation", help="copy or move."),
) -> None:
    """Add a file to the catalog."""
    active_catalog = Catalog.open(_resolve_catalog_path(catalog))
    metadata = _parse_meta_items(meta)
    record = active_catalog.add_file(path, metadata=metadata, operation=operation)
    console.print(f"Added {record.id}: {record.stored_abspath}")


@app.command()
def search(
    catalog: Path | None = typer.Option(None, "--catalog", help="Catalog root."),
    where: list[str] = typer.Option([], "--where", help="Equality filter KEY=VALUE. Repeatable."),
    contains: list[str] = typer.Option(
        [], "--contains", help="Substring filter KEY=VALUE. Repeatable."
    ),
    regex: list[str] = typer.Option([], "--regex", help="Regex filter KEY=VALUE. Repeatable."),
    ignore_case: bool = typer.Option(False, "--ignore-case", help="Case-insensitive matching."),
) -> None:
    """Search records in a catalog."""
    active_catalog = Catalog.open(_resolve_catalog_path(catalog))
    results = active_catalog.search(
        where=_parse_meta_items(where),
        contains=_parse_key_value_options(contains),
        regex=_parse_key_value_options(regex),
        ignore_case=ignore_case,
    )

    table = Table(title="ogcat search results")
    table.add_column("id")
    table.add_column("path")
    table.add_column("title")
    table.add_column("product")
    table.add_column("species")

    for record in results:
        table.add_row(
            record.id,
            record.stored_abspath,
            str(record.user_metadata.get("title", "")),
            str(record.user_metadata.get("product", "")),
            str(record.user_metadata.get("species", "")),
        )

    console.print(table)


@app.command()
def show(
    record_id: str = typer.Argument(..., help="Record id."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Catalog root."),
) -> None:
    """Show a record as JSON."""
    active_catalog = Catalog.open(_resolve_catalog_path(catalog))
    record = active_catalog.get(record_id)
    if record is None:
        raise typer.Exit(code=1)
    console.print_json(json.dumps(record.to_dict(), indent=2))


@app.command()
def path(
    record_id: str = typer.Argument(..., help="Record id."),
    catalog: Path | None = typer.Option(None, "--catalog", help="Catalog root."),
) -> None:
    """Print the stored path for a record."""
    active_catalog = Catalog.open(_resolve_catalog_path(catalog))
    resolved = active_catalog.path(record_id)
    if resolved is None:
        raise typer.Exit(code=1)
    console.print(str(resolved))
