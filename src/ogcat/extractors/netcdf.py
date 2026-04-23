"""Optional netCDF metadata extraction."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

from ogcat.models import JsonValue
from ogcat.extractors import SuffixExtractor

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

        with xarray.open_dataset(path, decode_cf=False) as dataset:
            return {
                "dims": {name: int(size) for name, size in dataset.sizes.items()},
                "data_vars": sorted(dataset.data_vars.keys()),
                "coords": sorted(dataset.coords.keys()),
                "attrs": _select_attrs(dataset.attrs),
            }


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
