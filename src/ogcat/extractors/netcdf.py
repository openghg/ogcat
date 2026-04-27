"""Optional netCDF metadata extraction."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

from ogcat.extractors import SuffixExtractor
from ogcat.models import JsonValue

_SELECTED_ATTRS = (
    "title",
    "summary",
    "Conventions",
    "institution",
    "source",
    "featureType",
    "history",
)


class NetcdfExtractor(SuffixExtractor):
    """Extract a small netCDF metadata summary when xarray is available."""

    def __init__(self) -> None:
        super().__init__(name="netcdf", suffixes=(".nc", ".nc4", ".cdf"))

    def extract(self, path: Path) -> JsonValue | None:
        xarray = _import_xarray()
        if xarray is None:
            return None

        engines = _candidate_engines(path)
        if not engines:
            return None

        last_error: Exception | None = None
        for engine in engines:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        category=RuntimeWarning,
                        module=r"xarray\.backends\.api",
                    )
                    with xarray.open_dataset(path, decode_cf=False, engine=engine) as dataset:
                        return {
                            "dims": {name: int(size) for name, size in dataset.sizes.items()},
                            "data_vars": sorted(dataset.data_vars.keys()),
                            "coords": sorted(dataset.coords.keys()),
                            "attrs": _select_attrs(dataset.attrs),
                        }
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        return None


def _candidate_engines(path: Path) -> tuple[str, ...]:
    """Return likely xarray engines from the file signature.

    This avoids xarray's backend guessing path for files that merely have a
    ``.nc`` suffix but are not readable netCDF payloads.
    """
    try:
        with path.open("rb") as stream:
            header = stream.read(8)
    except OSError:
        return ()
    if header.startswith(b"CDF"):
        return ("scipy", "netcdf4")
    if header.startswith(b"\x89HDF\r\n\x1a\n"):
        return ("h5netcdf", "netcdf4")
    return ()


def _import_xarray() -> Any | None:
    try:
        return import_module("xarray")
    except ImportError:
        return None


def _select_attrs(attrs: Mapping[str, Any]) -> dict[str, JsonValue]:
    selected: dict[str, JsonValue] = {}
    for key in _SELECTED_ATTRS:
        value = attrs.get(key)
        json_value = _coerce_json_value(value)
        if json_value is not None:
            selected[key] = json_value
    return selected


def _coerce_json_value(value: Any) -> JsonValue | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        scalar = value.item()
        if isinstance(scalar, (str, int, float, bool)):
            return scalar
    return str(value)


__all__ = ["NetcdfExtractor"]
