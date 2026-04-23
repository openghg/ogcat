"""Naming and template rendering helpers."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


_TEMPLATE_PATTERN = re.compile(r"\{([^{}]+)\}")


def _normalise_segment(value: str) -> str:
    """Normalise a path or filename segment conservatively."""
    cleaned = value.strip().replace(" ", "_")
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "unknown"


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render a simple template with optional fallback values.

    Supported forms:
    - `{field}`
    - `{field|fallback}`
    """

    def replace(match: re.Match[str]) -> str:
        expr = match.group(1)
        if "|" in expr:
            field, fallback = expr.split("|", 1)
        else:
            field, fallback = expr, ""
        value = context.get(field, fallback)
        return str(value)

    return _TEMPLATE_PATTERN.sub(replace, template)


def ensure_unique_path(path: Path) -> Path:
    """Return a unique path by appending numeric suffixes if needed."""
    if not path.exists():
        return path

    parent = path.parent
    suffix = "".join(path.suffixes)
    stem = path.name[: -len(suffix)] if suffix else path.name

    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_naming_context(
    *,
    record_id: str,
    original_path: Path,
    metadata: dict[str, object],
    date_added: str,
) -> dict[str, object]:
    """Build a simple naming context for template rendering."""
    context: dict[str, object] = {**metadata}
    context["id"] = record_id
    context["date_added"] = date_added
    context["year_added"] = date_added[:4]
    context["original_stem"] = _normalise_segment(original_path.stem)
    context["original_suffix"] = original_path.suffix

    year = metadata.get("year")
    month = metadata.get("month")
    if year is not None and month is not None:
        context["year_month_or_original_stem"] = f"{int(year):04d}{int(month):02d}"
    elif year is not None:
        context["year_month_or_original_stem"] = f"{int(year):04d}"
    else:
        context["year_month_or_original_stem"] = context["original_stem"]

    return context


def render_storage_location(
    *,
    files_root: Path,
    directory_template: str,
    filename_template: str,
    context: dict[str, object],
) -> tuple[Path, str, str]:
    """Render directory and filename templates into a final storage path."""
    rel_dir = render_template(directory_template, context)
    rel_dir = "/".join(_normalise_segment(part) for part in rel_dir.split("/") if part)

    filename = _normalise_segment(render_template(filename_template, context))
    target = files_root / rel_dir / filename
    target = ensure_unique_path(target)

    rel_path = target.relative_to(files_root.parent)
    return target, str(rel_path), target.name
