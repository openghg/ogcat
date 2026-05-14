"""Small example-data helpers for documentation snippets."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

EXAMPLE_HPC_ROOT = Path("/hpc/shared/atmos")
"""Generic HPC-style root used in documentation examples."""

EXAMPLE_FLUX_COLLECTION_RELATIVE_PATH = Path("fluxes/EUROPE/CO2/edgarv8/agriculture")
"""Relative collection path for realistic flux examples."""

EXAMPLE_FLUX_FILENAME = "EUROPE-co2-edgarv8-agriculture-2012.nc"
"""Filename for the tiny example flux artifact."""

_EXAMPLE_FLUX_CDL = """netcdf EUROPE-co2-edgarv8-agriculture-2012 {
dimensions:
    time = 1 ;
    lat = 2 ;
    lon = 3 ;
variables:
    double time(time) ;
        time:standard_name = "time" ;
        time:units = "days since 2012-01-01 00:00:00" ;
    float lat(lat) ;
        lat:standard_name = "latitude" ;
        lat:units = "degrees_north" ;
    float lon(lon) ;
        lon:standard_name = "longitude" ;
        lon:units = "degrees_east" ;
    float flux(time, lat, lon) ;
        flux:standard_name = "surface_upward_mass_flux_of_carbon_dioxide_expressed_as_carbon" ;
        flux:units = "kg m-2 s-1" ;
        flux:cell_methods = "time: mean" ;

// global attributes:
    :title = "Tiny fake CO2 agriculture flux over Europe" ;
    :institution = "ogcat documentation examples" ;
    :source = "synthetic test data" ;
    :Conventions = "CF-1.8" ;
    :history = "Created from a tiny synthetic flux grid; normalized to a single flux variable." ;
}
"""
"""CDL-style text describing the tiny flux example."""

PLACEHOLDER_TEXT = (
    "Dummy .nc placeholder for ogcat documentation examples.\n\n"
    "Optional xarray, numpy, or a writable NetCDF backend was unavailable, so this is plain text.\n\n"
    f"{_EXAMPLE_FLUX_CDL}"
)
"""Text written when optional NetCDF dependencies are unavailable."""


def example_flux_collection_path(root: Path | str | None = None) -> Path:
    """Return the directory path for the example flux collection.

    The returned path is suitable for examples that catalog an external
    HPC-style collection of flux files.

    Args:
        root: Optional HPC root directory. When omitted, ``EXAMPLE_HPC_ROOT`` is used.

    Returns:
        Full directory path containing the tiny example flux artifact.
    """
    base = Path(root) if root is not None else EXAMPLE_HPC_ROOT
    return base / EXAMPLE_FLUX_COLLECTION_RELATIVE_PATH


def example_flux_cdl() -> str:
    """Return CDL-style text for the tiny example flux artifact.

    Returns:
        CDL-style text with realistic flux dimensions, variable metadata, and
        CF-style global attributes.
    """
    return _EXAMPLE_FLUX_CDL


def write_example_flux_netcdf_or_placeholder(path: Path | str) -> Path:
    """Write a tiny realistic flux NetCDF artifact or text placeholder.

    The function writes a real NetCDF file when optional ``xarray``, ``numpy``,
    and a usable NetCDF writer backend are available. If any optional dependency
    or backend is missing, it writes a small text placeholder with a ``.nc``
    suffix so examples can run in a minimal ogcat installation.

    Args:
        path: Destination artifact path.

    Returns:
        The destination path that was written.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if _write_netcdf_if_available(target):
        return target

    target.write_text(PLACEHOLDER_TEXT, encoding="utf-8")
    return target


def _write_netcdf_if_available(path: Path) -> bool:
    """Write a small real NetCDF file if optional dependencies are available."""
    try:
        np = importlib.import_module("numpy")
        xr = importlib.import_module("xarray")
    except ImportError:
        return False

    dataset = _tiny_flux_dataset(np=np, xr=xr)
    try:
        dataset.to_netcdf(path)
    except Exception:
        if path.exists():
            path.unlink()
        return False
    return True


def _tiny_flux_dataset(*, np: Any, xr: Any) -> Any:
    """Build the in-memory xarray dataset used for real NetCDF examples."""
    return xr.Dataset(
        data_vars={
            "flux": (
                ("time", "lat", "lon"),
                np.array(
                    [
                        [
                            [1.1e-9, 1.3e-9, 1.2e-9],
                            [9.0e-10, 1.0e-9, 1.4e-9],
                        ]
                    ],
                    dtype="float32",
                ),
                {
                    "standard_name": "surface_upward_mass_flux_of_carbon_dioxide_expressed_as_carbon",
                    "units": "kg m-2 s-1",
                    "cell_methods": "time: mean",
                },
            ),
        },
        coords={
            "time": (
                "time",
                np.array(["2012-01-16"], dtype="datetime64[D]"),
                {"standard_name": "time"},
            ),
            "lat": (
                "lat",
                np.array([50.0, 51.0], dtype="float32"),
                {"standard_name": "latitude", "units": "degrees_north"},
            ),
            "lon": (
                "lon",
                np.array([-2.0, -1.0, 0.0], dtype="float32"),
                {"standard_name": "longitude", "units": "degrees_east"},
            ),
        },
        attrs={
            "title": "Tiny fake CO2 agriculture flux over Europe",
            "institution": "ogcat documentation examples",
            "source": "synthetic test data",
            "Conventions": "CF-1.8",
            "history": (
                "Created from a tiny synthetic flux grid; normalized to a single "
                "flux variable for ogcat docs."
            ),
        },
    )


__all__ = [
    "EXAMPLE_FLUX_COLLECTION_RELATIVE_PATH",
    "EXAMPLE_FLUX_FILENAME",
    "EXAMPLE_HPC_ROOT",
    "PLACEHOLDER_TEXT",
    "example_flux_cdl",
    "example_flux_collection_path",
    "write_example_flux_netcdf_or_placeholder",
]
