"""Claim and facet examples for common artifact shapes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ogcat import (
    ArtifactDescriptor,
    ArtifactFacet,
    DataTypeClaim,
    InterfaceClaim,
    RepresentationClaim,
    has_claim,
    has_facet,
)


def test_zarr_directory_store_example_claims_directory_storage_and_zarr_interfaces() -> None:
    """A Zarr directory store should be one directory-backed dataset artifact."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("directory", evidence="inferred"),
            DataTypeClaim("zarr", namespace="zarr.dev", evidence="inferred"),
            InterfaceClaim("directory-listing"),
            InterfaceClaim("zarr-group", namespace="zarr.dev"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray"),
        ],
        facets=[
            ArtifactFacet(
                kind="suffix",
                name="suffixes",
                evidence="inferred",
                metadata={"suffixes": [".zarr"]},
            )
        ],
    )

    assert has_claim(descriptor, kind="representation", name="directory")
    assert has_claim(descriptor, kind="data_type", name="zarr", namespace="zarr.dev")
    assert has_claim(descriptor, kind="interface", name="zarr-group", namespace="zarr.dev")
    assert has_facet(descriptor, kind="suffix", name="suffixes")


def test_netcdf_collection_example_claims_collection_interface_and_member_facets() -> None:
    """A directory of NetCDF members should opt into collection semantics explicitly."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("directory", evidence="declared"),
            InterfaceClaim("collection", evidence="declared"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray", evidence="declared"),
        ],
        facets=[
            ArtifactFacet(
                kind="collection",
                name="members",
                evidence="declared",
                metadata={
                    "pattern": "*.nc",
                    "member_format": "netcdf",
                    "member_suffixes": [".nc"],
                    "reader_hint": "xarray.open_mfdataset",
                },
            )
        ],
    )

    assert has_claim(descriptor, kind="representation", name="directory")
    assert has_claim(descriptor, kind="interface", name="collection")
    assert has_claim(descriptor, kind="interface", name="xarray-dataset", namespace="pydata.xarray")
    assert has_facet(descriptor, kind="collection", name="members")


def test_csv_like_data_example_claims_text_and_table_interfaces() -> None:
    """A CSV-like artifact should be text representation with a table interface."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("text", evidence="inferred", metadata={"source": "suffix"}),
            DataTypeClaim("csv", namespace="iana.media-types", evidence="inferred"),
            InterfaceClaim("text"),
            InterfaceClaim("table"),
        ],
        facets=[
            ArtifactFacet(
                kind="suffix",
                name="suffixes",
                evidence="inferred",
                metadata={"suffixes": [".csv"]},
            )
        ],
    )

    assert has_claim(descriptor, kind="representation", name="text")
    assert has_claim(descriptor, kind="data_type", name="csv", namespace="iana.media-types")
    assert has_claim(descriptor, kind="interface", name="table")


def test_single_netcdf_example_can_claim_xarray_without_importing_xarray() -> None:
    """A single NetCDF artifact can advertise optional interfaces without core imports."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("file", evidence="inferred"),
            DataTypeClaim("netcdf", namespace="org.unidata", evidence="inferred"),
            InterfaceClaim("bytes"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray", evidence="declared"),
        ],
        facets=[
            ArtifactFacet(
                kind="suffix",
                name="suffixes",
                evidence="inferred",
                metadata={"suffixes": [".nc"]},
            )
        ],
    )

    assert has_claim(descriptor, kind="data_type", name="netcdf", namespace="org.unidata")
    assert has_claim(descriptor, kind="interface", name="xarray-dataset", namespace="pydata.xarray")


def test_grouped_netcdf_example_can_represent_groups_without_opening_file() -> None:
    """Grouped NetCDF/HDF5 files can carry group facets without reader imports."""
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("file", evidence="declared"),
            DataTypeClaim("netcdf", namespace="org.unidata", evidence="declared"),
            InterfaceClaim("netcdf-groups", namespace="org.unidata", evidence="declared"),
        ],
        facets=[
            ArtifactFacet(
                kind="netcdf",
                name="groups",
                namespace="org.unidata",
                evidence="declared",
                metadata={"groups": ["/", "/observations", "/metadata"]},
            )
        ],
    )

    assert has_claim(descriptor, kind="interface", name="netcdf-groups", namespace="org.unidata")
    assert has_facet(descriptor, kind="netcdf", name="groups", namespace="org.unidata")


def test_csv_example_can_be_read_with_pandas_when_available(tmp_path: Path) -> None:
    """The CSV example should align with pandas when that optional stack is present."""
    pandas = pytest.importorskip("pandas")
    path = tmp_path / "example.csv"
    path.write_text("time,value\n2026-01-01,1.0\n", encoding="utf-8")
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[DataTypeClaim("csv", namespace="iana.media-types"), InterfaceClaim("table")],
    )

    frame = pandas.read_csv(path)

    assert list(frame.columns) == ["time", "value"]
    assert has_claim(descriptor, kind="interface", name="table")


def test_zarr_example_can_be_opened_with_xarray_when_available(tmp_path: Path) -> None:
    """The Zarr example should align with xarray/zarr when that optional stack is present."""
    xarray = pytest.importorskip("xarray")
    pytest.importorskip("zarr")
    store = tmp_path / "example.zarr"
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            RepresentationClaim("directory"),
            DataTypeClaim("zarr", namespace="zarr.dev"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray"),
        ],
    )

    dataset = xarray.Dataset({"value": ("time", [1.0, 2.0])})
    try:
        dataset.to_zarr(store)
        opened = xarray.open_zarr(store)
    except Exception as exc:
        pytest.skip(f"xarray/zarr stack cannot write and open a tiny Zarr store: {exc}")

    assert "value" in opened
    assert has_claim(descriptor, kind="data_type", name="zarr", namespace="zarr.dev")


def test_single_netcdf_example_can_be_opened_with_xarray_when_available(tmp_path: Path) -> None:
    """The NetCDF example should align with xarray when a NetCDF backend is present."""
    xarray = pytest.importorskip("xarray")
    engine = _available_netcdf_engine()
    path = tmp_path / "example.nc"
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[
            DataTypeClaim("netcdf", namespace="org.unidata"),
            InterfaceClaim("xarray-dataset", namespace="pydata.xarray"),
        ],
    )

    dataset = xarray.Dataset({"co2": ("time", [400.0, 401.0])})
    try:
        dataset.to_netcdf(path, engine=engine)
        opened = xarray.open_dataset(path, engine=engine)
    except Exception as exc:
        pytest.skip(f"xarray/{engine} stack cannot write and open a tiny NetCDF file: {exc}")

    assert "co2" in opened
    assert has_claim(descriptor, kind="interface", name="xarray-dataset", namespace="pydata.xarray")


def test_grouped_netcdf_example_can_be_opened_when_backend_supports_groups(tmp_path: Path) -> None:
    """The grouped NetCDF example should align with xarray backends that support groups."""
    xarray = pytest.importorskip("xarray")
    engine = _available_grouped_netcdf_engine()
    path = tmp_path / "grouped.nc"
    descriptor = ArtifactDescriptor(
        id="data",
        role="data_artifact",
        claims=[InterfaceClaim("netcdf-groups", namespace="org.unidata")],
        facets=[
            ArtifactFacet(
                kind="netcdf",
                name="groups",
                namespace="org.unidata",
                metadata={"groups": ["/", "/observations"]},
            )
        ],
    )

    dataset = xarray.Dataset({"co2": ("time", [400.0])})
    try:
        dataset.to_netcdf(path, engine=engine)
        dataset.to_netcdf(path, engine=engine, mode="a", group="observations")
        opened = xarray.open_dataset(path, engine=engine, group="observations")
    except Exception as exc:
        pytest.skip(f"xarray/{engine} stack cannot write and open a grouped NetCDF file: {exc}")

    assert "co2" in opened
    assert has_facet(descriptor, kind="netcdf", name="groups", namespace="org.unidata")


def _available_netcdf_engine() -> str:
    """Return a usable xarray NetCDF engine or skip the optional integration test."""
    if importlib.util.find_spec("h5netcdf") is not None:
        return "h5netcdf"
    if importlib.util.find_spec("netCDF4") is not None:
        return "netcdf4"
    pytest.skip("h5netcdf or netCDF4 is required for this optional NetCDF example test")


def _available_grouped_netcdf_engine() -> str:
    """Return a grouped NetCDF-capable xarray engine or skip the optional test."""
    if importlib.util.find_spec("h5netcdf") is not None:
        return "h5netcdf"
    if importlib.util.find_spec("netCDF4") is not None:
        return "netcdf4"
    pytest.skip("h5netcdf or netCDF4 is required for this optional grouped NetCDF example test")
