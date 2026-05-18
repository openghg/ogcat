"""Naming and template rendering helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_TEMPLATE_PATTERN = re.compile(r"\{([^{}]+)\}")
_SLUG_SEPARATOR_PATTERN = re.compile(r"[\s_]+")
_COMPRESSED_SUFFIXES = {".gz", ".bz2", ".xz", ".zip", ".zst"}
_PUBLIC_NAMING_TEMPLATE_FIELDS = frozenset(
    {
        "date_added",
        "original_filename",
        "original_stem",
        "original_suffix",
        "title_slug",
        "year_added",
        "year_month_or_original_stem",
    }
)
_INTERNAL_NAMING_TEMPLATE_FIELDS = frozenset(
    {
        "artifact_uuid",
        "id",
        "operation_id",
        "uuid",
    }
)
_RESERVED_TEMPLATE_FIELDS = _PUBLIC_NAMING_TEMPLATE_FIELDS | _INTERNAL_NAMING_TEMPLATE_FIELDS


PUBLIC_NAMING_TEMPLATE_FIELDS = _PUBLIC_NAMING_TEMPLATE_FIELDS
INTERNAL_NAMING_TEMPLATE_FIELDS = _INTERNAL_NAMING_TEMPLATE_FIELDS
RESERVED_TEMPLATE_FIELDS = _RESERVED_TEMPLATE_FIELDS


def _normalise_segment(value: str) -> str:
    """Normalise a path or filename segment conservatively."""
    cleaned = value.strip().replace(" ", "_")
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "unknown"


def _slugify(value: object) -> str:
    """Create a simple readable slug."""
    text = str(value).strip().lower()
    text = re.sub(r"[\\/:*?\"<>|]+", " ", text)
    text = _SLUG_SEPARATOR_PATTERN.sub("-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _split_name_and_suffixes(name: str) -> tuple[str, str]:
    """Split a filename into the base name and its naming suffix string."""
    suffixes = _naming_suffixes(name)
    full_suffix = "".join(suffixes)
    if full_suffix:
        return name[: -len(full_suffix)], full_suffix
    return name, ""


def normalise_segment(value: str) -> str:
    """Normalise a path or filename segment conservatively."""
    return _normalise_segment(value)


def split_name_and_suffixes(name: str) -> tuple[str, str]:
    """Split a filename into the base name and its naming suffix string."""
    return _split_name_and_suffixes(name)


def _naming_suffixes(name: str) -> list[str]:
    """Return the suffixes that should be preserved during naming."""
    suffixes = Path(name).suffixes
    if not suffixes:
        return []

    last_suffix = suffixes[-1]
    if last_suffix.lower() not in _COMPRESSED_SUFFIXES:
        return [last_suffix]

    preserved = [last_suffix]
    index = len(suffixes) - 2
    while index >= 0:
        candidate = suffixes[index]
        body = candidate[1:].lower()
        if body.isalpha() and len(body) <= 4:
            preserved.insert(0, candidate)
            index -= 1
            continue
        break
    return preserved


def _stringify_template_value(value: object) -> str:
    """Stringify template values without exposing Python repr details."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "-".join(_stringify_template_value(item) for item in value if item is not None)
    return str(value)


def _resolve_template_value(expr: str, context: dict[str, Any]) -> str:
    """Resolve a template expression against the naming context."""
    if "|" in expr:
        field, fallback = expr.split("|", 1)
    else:
        field, fallback = expr, ""

    field = field.strip()
    fallback = fallback.strip()

    value = context.get(field)
    if value in (None, ""):
        value = context.get(fallback) if fallback in context else fallback
    return _stringify_template_value(value)


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render a simple template with optional fallback values.

    Supported forms:
    - `{field}`
    - `{field|fallback}`
    """

    def replace(match: re.Match[str]) -> str:
        return _resolve_template_value(match.group(1), context)

    return _TEMPLATE_PATTERN.sub(replace, template)


def referenced_template_fields(template: str) -> frozenset[str]:
    """Return possible context fields referenced by a template.

    Fallback tokens are ambiguous because :func:`render_template` treats them
    as context lookups when a matching context key exists, otherwise as literal
    fallback text. This extractor reports non-empty fallback tokens as possible
    field references so callers do not miss metadata dependencies such as
    ``{title_slug|dataset_id}``.
    """
    fields: set[str] = set()
    for match in _TEMPLATE_PATTERN.finditer(template):
        expr = match.group(1)
        if "|" in expr:
            field, fallback = expr.split("|", 1)
            fallback = fallback.strip()
        else:
            field, fallback = expr, ""
        field = field.strip()
        if field:
            fields.add(field)
        if fallback:
            fields.add(fallback)
    return frozenset(fields)


def validate_human_readable_template_fields(*templates: str) -> None:
    """Reject internal storage identifiers in human-readable naming templates.

    Schema storage templates are intended to be stable, readable paths based on
    user metadata and cheap derived naming context. Internal identifiers remain
    available to storage planning, audit, and operation internals, but should
    not become user-facing naming policy.

    Args:
        *templates: Directory or filename templates to validate.

    Raises:
        ValueError: If any template references an internal naming field.
    """
    referenced = set().union(*(referenced_template_fields(template) for template in templates))
    internal_fields = sorted(referenced & _INTERNAL_NAMING_TEMPLATE_FIELDS)
    if internal_fields:
        joined = ", ".join(internal_fields)
        raise ValueError(f"Naming templates cannot use internal template field(s): {joined}")


def ensure_unique_path(path: Path, *, exists: Callable[[Path], bool] | None = None) -> Path:
    """Return a unique path by appending numeric suffixes if needed."""
    path_exists = Path.exists if exists is None else exists
    if not path_exists(path):
        return path

    parent = path.parent
    stem, suffix = _split_name_and_suffixes(path.name)

    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not path_exists(candidate):
            return candidate
        counter += 1


def build_naming_context(
    *,
    record_id: str,
    operation_id: str | None = None,
    original_path: Path,
    metadata: Mapping[str, object],
    date_added: str,
) -> dict[str, object]:
    """Build a simple naming context for template rendering."""
    clashing_fields = sorted(field for field in metadata if field in _RESERVED_TEMPLATE_FIELDS)
    if clashing_fields:
        joined = ", ".join(clashing_fields)
        raise ValueError(f"Metadata cannot use reserved template field(s): {joined}")

    context: dict[str, object] = {**metadata}
    source_name = original_path.name
    original_stem, original_suffix = _split_name_and_suffixes(source_name)

    context["id"] = record_id
    context["uuid"] = operation_id or record_id
    context["operation_id"] = operation_id or record_id
    context["date_added"] = date_added
    context["year_added"] = date_added[:4]
    context["original_filename"] = source_name
    context["original_stem"] = _normalise_segment(original_stem)
    context["original_suffix"] = original_suffix

    year = metadata.get("year")
    month = metadata.get("month")
    context["year_month_or_original_stem"] = _derive_year_month_or_original_stem(
        year=year,
        month=month,
        original_stem=str(context["original_stem"]),
    )

    title = metadata.get("title")
    if title not in (None, ""):
        title_slug = _slugify(title)
        if title_slug:
            context["title_slug"] = title_slug

    return context


def _derive_year_month_or_original_stem(*, year: object, month: object, original_stem: str) -> str:
    """Return a compact year-month key when available, otherwise the original stem."""
    try:
        if year is not None and month is not None:
            return f"{_coerce_int(year):04d}{_coerce_int(month):02d}"
        if year is not None:
            return f"{_coerce_int(year):04d}"
    except (TypeError, ValueError):
        return original_stem
    return original_stem


def _coerce_int(value: object) -> int:
    """Coerce template metadata values that are intended to be integer-like."""
    if isinstance(value, bool):
        raise TypeError("Boolean values are not integer-like metadata values")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"Expected an integer-like float, got {value!r}")
    if isinstance(value, str | bytes | bytearray):
        return int(value)
    raise TypeError(f"Expected an integer-like value, got {type(value).__name__}")


def render_storage_location(
    *,
    files_root: Path,
    directory_template: str,
    filename_template: str,
    context: dict[str, object],
    exists: Callable[[Path], bool] | None = None,
) -> tuple[Path, str, str]:
    """Render directory and filename templates into a final storage path."""
    validate_human_readable_template_fields(directory_template, filename_template)
    rel_dir = render_template(directory_template, context)
    rel_dir = "/".join(_normalise_segment(part) for part in rel_dir.split("/") if part)

    filename = _render_filename(filename_template, context)
    target = files_root / rel_dir / filename
    target = ensure_unique_path(target, exists=exists)

    rel_path = target.relative_to(files_root.parent)
    return target, str(rel_path), target.name


def _render_filename(template: str, context: dict[str, object]) -> str:
    """Render and normalise a filename while preserving multi-part suffixes."""
    rendered = render_template(template, context)
    stem, suffix = _split_name_and_suffixes(rendered)
    normalised_stem = _normalise_segment(stem)
    return f"{normalised_stem}{suffix}"
