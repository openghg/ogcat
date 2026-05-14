from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

import ogcat.example_data as example_data


def test_example_flux_collection_path_uses_generic_hpc_root() -> None:
    """The example collection path looks like realistic HPC storage."""
    path = example_data.example_flux_collection_path()

    assert path == example_data.EXAMPLE_HPC_ROOT / example_data.EXAMPLE_FLUX_COLLECTION_RELATIVE_PATH
    assert path.as_posix() == "/hpc/shared/atmos/fluxes/EUROPE/CO2/edgarv8/agriculture"


def test_example_flux_collection_path_accepts_custom_root(tmp_path: Path) -> None:
    """Callers can place the realistic collection under a temporary root."""
    assert (
        example_data.example_flux_collection_path(tmp_path)
        == tmp_path / example_data.EXAMPLE_FLUX_COLLECTION_RELATIVE_PATH
    )


def test_example_flux_cdl_describes_flux_dimensions_and_metadata() -> None:
    """The CDL text includes recognizable flux dimensions, units, and attrs."""
    cdl = example_data.example_flux_cdl()

    assert "dimensions:" in cdl
    assert "time = 1" in cdl
    assert "lat = 2" in cdl
    assert "lon = 3" in cdl
    assert "float flux(time, lat, lon)" in cdl
    assert "kg m-2 s-1" in cdl
    assert "CF-1.8" in cdl
    assert "history" in cdl


def test_write_example_flux_netcdf_or_placeholder_falls_back_without_optional_xarray(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A minimal install writes a readable .nc placeholder instead of failing."""
    real_import_module = importlib.import_module

    def import_without_optional_netcdf(name: str, package: str | None = None) -> ModuleType:
        if name in {"numpy", "xarray"}:
            raise ImportError(name)
        return real_import_module(name, package)

    monkeypatch.setattr(example_data.importlib, "import_module", import_without_optional_netcdf)

    target = tmp_path / example_data.EXAMPLE_FLUX_FILENAME
    written = example_data.write_example_flux_netcdf_or_placeholder(target)

    assert written == target
    assert target.read_text(encoding="utf-8") == example_data.PLACEHOLDER_TEXT
    assert "Dummy .nc placeholder" in example_data.PLACEHOLDER_TEXT
