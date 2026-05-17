"""Naming-template policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogcat.naming import (
    referenced_template_fields,
    render_storage_location,
    validate_human_readable_template_fields,
)


def test_referenced_template_fields_includes_reserved_fallback_fields() -> None:
    """Template field extraction sees internal fields used as fallbacks."""
    assert referenced_template_fields("{title_slug|uuid}_{species}") == frozenset(
        {"title_slug", "uuid", "species"}
    )


@pytest.mark.parametrize("field_name", ["artifact_uuid", "id", "operation_id", "uuid"])
def test_human_readable_templates_reject_internal_identifier_fields(field_name: str) -> None:
    """Human-readable storage templates cannot reference internal identifiers."""
    with pytest.raises(
        ValueError,
        match=f"Naming templates cannot use internal template field\\(s\\): {field_name}",
    ):
        validate_human_readable_template_fields(f"{{{field_name}}}.nc")


@pytest.mark.parametrize("field_name", ["artifact_uuid", "id", "operation_id", "uuid"])
def test_render_storage_location_rejects_internal_identifier_fields(
    tmp_path: Path,
    field_name: str,
) -> None:
    """Storage-path rendering enforces the internal identifier policy."""
    with pytest.raises(
        ValueError,
        match=f"Naming templates cannot use internal template field\\(s\\): {field_name}",
    ):
        render_storage_location(
            files_root=tmp_path / "files",
            directory_template="{species}",
            filename_template=f"{{{field_name}}}.nc",
            context={"species": "co2", field_name: "internal-value"},
        )


def test_render_storage_location_allows_public_fields_and_metadata(tmp_path: Path) -> None:
    """Storage templates can use public generated fields and user metadata."""
    target, relative_path, resolved_filename = render_storage_location(
        files_root=tmp_path / "files",
        directory_template="{year_added}/{species}",
        filename_template="{title_slug|original_stem}{original_suffix}",
        context={
            "year_added": "2026",
            "species": "co2",
            "title_slug": "surface-flux",
            "original_stem": "raw_flux",
            "original_suffix": ".nc",
        },
    )

    assert target == tmp_path / "files" / "2026" / "co2" / "surface-flux.nc"
    assert relative_path == "files/2026/co2/surface-flux.nc"
    assert resolved_filename == "surface-flux.nc"
