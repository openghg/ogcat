"""Create an ogcat catalog with schemas matching OpenGHG metadata defaults.

This example mirrors the default object-store metadata configuration used by
OpenGHG for the data types listed below:

- ``boundary_conditions``
- ``column``
- ``eulerian_model``
- ``flux``
- ``flux_timeseries``
- ``footprints``
- ``site_met``
- ``surface``

The example does not import OpenGHG or run OpenGHG standardisation. It only shows
how the same broad metadata expectations can be represented in an ``ogcat``
catalog. The generated schema records OpenGHG type names such as ``species``,
``height``, and ``timestamp`` for documentation and future validation work, but
`ogcat` validates required metadata field presence only today.

Command-line usage:

```bash
python examples/catalog_openghg_default_schemas.py /tmp/openghg-ogcat
ogcat info --catalog /tmp/openghg-ogcat
ogcat fields --catalog /tmp/openghg-ogcat --record-type surface
```

Python usage:

```python
from pathlib import Path

from examples.catalog_openghg_default_schemas import create_openghg_catalog

catalog = create_openghg_catalog("/tmp/openghg-ogcat")

# In OpenGHG, the store argument chooses the object store target:
#
# standardise_surface(
#     filepath=hfd_path,
#     site="hfd",
#     instrument="picarro",
#     network="DECC",
#     source_format="CRDS",
#     store="user",
# )
#
# In this ogcat example, choosing the catalog root is analogous to choosing the
# target store. The record type selects the OpenGHG-style metadata schema.
surface_record = catalog.add_file(
    Path("hfd.picarro.1minute.100m.min.dat"),
    record_type="surface",
    metadata={
        "species": "co2",
        "site": "hfd",
        "inlet": "100m",
        "instrument": "picarro",
        "network": "DECC",
        "platform": "surface",
        "data_level": "raw",
        "data_sublevel": "none",
        "dataset_source": "openghg",
        "data_source": "CRDS",
        "sampling_period": "1minute",
        "source_format": "CRDS",
    },
)

flux_record = catalog.add_file(
    Path("co2-gpp-cardamom_EUROPE_2012.nc"),
    record_type="flux",
    metadata={
        "species": "co2",
        "source": "gpp-cardamom",
        "domain": "europe",
    },
)

footprint_record = catalog.add_file(
    Path("footprint_test.nc"),
    record_type="footprints",
    metadata={
        "model": "test_model",
        "domain": "europe",
        "inlet": "10m",
        "time_resolved": True,
        "high_spatial_resolution": True,
        "short_lifetime": False,
        "species": "inert",
        "met_model": "ukv",
    },
)
```

OpenGHG standardisation often derives or normalises metadata before data reaches
the object store. This example is intentionally simpler: callers provide the
metadata explicitly, and `ogcat` checks only that required keys are present.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ogcat import Catalog, CatalogSpec, MetadataFieldDescription, RecordSchema
from ogcat.models import JsonValue

OpenGhgMetadataDefaults = dict[str, dict[str, dict[str, dict[str, list[str]]]]]

OPENGHG_METADATA_DEFAULTS: OpenGhgMetadataDefaults = {
    "boundary_conditions": {
        "required": {
            "species": {"type": ["str"]},
            "bc_input": {"type": ["str"]},
            "domain": {"type": ["str"]},
        },
        "optional": {},
    },
    "eulerian_model": {
        "required": {
            "model": {"type": ["str"]},
            "species": {"type": ["str"]},
            "date": {"type": ["timestamp"]},
        },
        "optional": {},
    },
    "column": {
        "required": {
            "species": {"type": ["species"]},
            "platform": {"type": ["platform"]},
        },
        "optional": {
            "satellite": {"type": ["str"]},
            "selection": {"type": ["str"]},
            "domain": {"type": ["str"]},
            "site": {"type": ["str"]},
            "network": {"type": ["str"]},
            "obs_region": {"type": ["str"]},
        },
    },
    "footprints": {
        "required": {
            "model": {"type": ["str"]},
            "domain": {"type": ["str"]},
            "inlet": {"type": ["height"]},
            "time_resolved": {"type": ["bool"]},
            "high_spatial_resolution": {"type": ["bool"]},
            "short_lifetime": {"type": ["bool"]},
            "species": {"type": ["str", "species"]},
            "met_model": {"type": ["str"]},
        },
        "optional": {
            "site": {"type": ["str"]},
            "satellite": {"type": ["str"]},
            "obs_region": {"type": ["str"]},
            "selection": {"type": ["str"]},
        },
    },
    "flux": {
        "required": {
            "species": {"type": ["str"]},
            "source": {"type": ["str"]},
            "domain": {"type": ["str"]},
        },
        "optional": {
            "database": {"type": ["str"]},
            "database_version": {"type": ["str"]},
            "model": {"type": ["str"]},
        },
    },
    "flux_timeseries": {
        "required": {
            "species": {"type": ["str"]},
            "source": {"type": ["str"]},
            "region": {"type": ["str"]},
        },
        "optional": {
            "database": {"type": ["str"]},
            "database_version": {"type": ["str"]},
            "model": {"type": ["str"]},
        },
    },
    "surface": {
        "required": {
            "species": {"type": ["str"]},
            "site": {"type": ["str"]},
            "inlet": {"type": ["str"]},
            "instrument": {"type": ["str"]},
            "network": {"type": ["str"]},
            "platform": {"type": ["str"]},
            "data_level": {"type": ["str"]},
            "data_sublevel": {"type": ["str"]},
            "dataset_source": {"type": ["str"]},
            "data_source": {"type": ["str"]},
            "sampling_period": {"type": ["str"]},
            "source_format": {"type": ["str"]},
        },
        "optional": {},
    },
    "site_met": {
        "required": {
            "site": {"type": ["str"]},
            "network": {"type": ["str"]},
            "met_source": {"type": ["str"]},
        },
        "optional": {},
    },
}

EXAMPLE_METADATA_VALUES: dict[str, dict[str, JsonValue]] = {
    "boundary_conditions": {
        "species": "co2",
        "bc_input": "cams",
        "domain": "europe",
    },
    "eulerian_model": {
        "model": "GEOS-Chem",
        "species": "co2",
        "date": "2024-01-01T00:00:00Z",
    },
    "column": {
        "species": "ch4",
        "platform": "satellite",
        "satellite": "GOSAT",
        "selection": "LAND",
        "domain": "EUROPE",
        "site": "THW",
        "network": "TCCON",
        "obs_region": "BRAZIL",
    },
    "footprints": {
        "model": "test_model",
        "domain": "europe",
        "inlet": "10m",
        "time_resolved": True,
        "high_spatial_resolution": True,
        "short_lifetime": False,
        "species": "inert",
        "met_model": "ukv",
        "site": "TMB",
        "satellite": "GOSAT",
        "obs_region": "BRAZIL",
        "selection": "LAND",
    },
    "flux": {
        "species": "co2",
        "source": "gpp-cardamom",
        "domain": "europe",
        "database": "EDGAR",
        "database_version": "v50",
        "model": "cardamom",
    },
    "flux_timeseries": {
        "species": "ch4",
        "source": "crf",
        "region": "uk",
        "database": "UK GHGI",
        "database_version": "2023",
        "model": "inventory",
    },
    "surface": {
        "species": "co2",
        "site": "hfd",
        "inlet": "100m",
        "instrument": "picarro",
        "network": "DECC",
        "platform": "surface",
        "data_level": "raw",
        "data_sublevel": "none",
        "dataset_source": "openghg",
        "data_source": "CRDS",
        "sampling_period": "1minute",
        "source_format": "CRDS",
    },
    "site_met": {
        "site": "TAC",
        "network": "DECC",
        "met_source": "ukv",
    },
}


def build_openghg_catalog_spec(*, catalog_name: str = "openghg") -> CatalogSpec:
    """Build a catalog spec with one record schema per OpenGHG data type.

    Args:
        catalog_name: Name to store in the generated catalog specification.

    Returns:
        Catalog specification containing OpenGHG-style record schemas.
    """
    return CatalogSpec(
        catalog_name=catalog_name,
        record_schemas={
            data_type: _record_schema(data_type, config)
            for data_type, config in OPENGHG_METADATA_DEFAULTS.items()
        },
    )


def create_openghg_catalog(root: str | Path, *, catalog_name: str = "openghg") -> Catalog:
    """Create or open an ogcat catalog with OpenGHG-style schemas.

    Args:
        root: Catalog root directory.
        catalog_name: Name to use when creating a new catalog.

    Returns:
        Opened or newly created catalog.
    """
    root_path = Path(root).expanduser()
    if (root_path / "catalog.json").exists():
        return Catalog.open(root_path)
    return Catalog.create(root_path, build_openghg_catalog_spec(catalog_name=catalog_name))


def _record_schema(data_type: str, config: dict[str, dict[str, dict[str, list[str]]]]) -> RecordSchema:
    """Translate one OpenGHG metadata config block into an ogcat record schema."""
    metadata_fields: list[MetadataFieldDescription] = []
    for field_name, field_config in config.get("required", {}).items():
        metadata_fields.append(_metadata_field(data_type, field_name, field_config, required=True))
    for field_name, field_config in config.get("optional", {}).items():
        metadata_fields.append(_metadata_field(data_type, field_name, field_config, required=False))

    return RecordSchema(
        description=f"OpenGHG default metadata schema for {data_type}.",
        directory_template=_directory_template(data_type),
        filename_template="{original_stem}{original_suffix}",
        metadata_fields=metadata_fields,
    )


def _directory_template(data_type: str) -> str:
    """Return a readable storage layout for one OpenGHG-style data type."""
    templates = {
        "boundary_conditions": "boundary_conditions/{species|unknown}/{domain|unknown}",
        "column": "column/{species|unknown}/{platform|unknown}",
        "eulerian_model": "eulerian_model/{model|unknown}/{species|unknown}",
        "flux": "flux/{species|unknown}/{domain|unknown}/{source|unknown}",
        "flux_timeseries": "flux_timeseries/{species|unknown}/{region|unknown}/{source|unknown}",
        "footprints": "footprints/{model|unknown}/{domain|unknown}/{inlet|unknown}",
        "site_met": "site_met/{site|unknown}/{network|unknown}",
        "surface": "surface/{species|unknown}/{site|unknown}/{inlet|unknown}",
    }
    return templates[data_type]


def _metadata_field(
    data_type: str,
    field_name: str,
    field_config: dict[str, list[str]],
    *,
    required: bool,
) -> MetadataFieldDescription:
    """Translate one OpenGHG metadata field config into an ogcat field description."""
    type_names = field_config.get("type", [])
    return MetadataFieldDescription(
        name=field_name,
        description="OpenGHG metadata field.",
        example=EXAMPLE_METADATA_VALUES.get(data_type, {}).get(field_name),
        required=required,
        value_types=type_names,
    )


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create an ogcat catalog with OpenGHG-style default schemas."
    )
    parser.add_argument("catalog_root", type=Path, help="Catalog root directory to create or open.")
    parser.add_argument("--name", default="openghg", help="Catalog name to use when creating a catalog.")
    parser.add_argument(
        "--print-schemas",
        action="store_true",
        help="Print the available record schema names after opening the catalog.",
    )
    return parser.parse_args()


def main() -> None:
    """Create or open a catalog with OpenGHG-style default schemas."""
    args = _parse_args()
    catalog = create_openghg_catalog(args.catalog_root, catalog_name=args.name)
    print(f"Catalog ready at {catalog.root}")
    if args.print_schemas:
        print("Record schemas:")
        for schema_name in catalog.list_record_schemas():
            print(f"- {schema_name}")


if __name__ == "__main__":
    main()
