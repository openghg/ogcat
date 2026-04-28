from pathlib import Path

from ogcat import Catalog, CatalogSpec


def test_search_supports_flattened_lookup_and_ignore_case(tmp_path: Path) -> None:
    source = tmp_path / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(
        source,
        metadata={
            "product": "CTE-HR",
            "species": "CO2",
            "domain": "EUROPE",
            "flux_type": "anthropogenic",
            "version": "v4.2",
            "title": "Anthropogenic test flux",
        },
    )

    by_species = catalog.search(where={"species": "CO2"})
    by_title = catalog.search(contains={"title": "anthropogenic"}, ignore_case=True)
    by_id = catalog.search(where={"id": record.id})
    by_version_regex = catalog.search(regex={"version": r"^v4\.[0-9]+$"})

    assert [r.id for r in by_species] == [record.id]
    assert [r.id for r in by_title] == [record.id]
    assert [r.id for r in by_id] == [record.id]
    assert [r.id for r in by_version_regex] == [record.id]


def test_search_prefers_user_metadata_and_allows_dotted_paths(tmp_path: Path) -> None:
    source = tmp_path / "anthropogenic.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(source, metadata={"species": "CO2", "product": "CTE-HR"})
    record.derived_metadata["species"] = "CH4"
    record.derived_metadata["netcdf"] = {"dims": {"time": 12}}
    catalog.repository.update(record)

    by_unqualified_species = catalog.search(where={"species": "CO2"})
    by_derived_species = catalog.search(where={"derived_metadata.species": "CH4"})
    by_nested_dim = catalog.search(where={"derived_metadata.netcdf.dims.time": 12})

    assert [r.id for r in by_unqualified_species] == [record.id]
    assert [r.id for r in by_derived_species] == [record.id]
    assert [r.id for r in by_nested_dim] == [record.id]


def test_search_prefers_top_level_fields_when_names_are_ambiguous(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(
        source,
        metadata={"catalog": "user-catalog", "time_added": "user-time", "title": "Ambiguous record"},
    )
    record.derived_metadata["catalog"] = "derived-catalog"
    record.derived_metadata["time_added"] = "derived-time"
    catalog.repository.update(record)

    by_catalog = catalog.search(where={"catalog": "fluxes"})
    by_time_added = catalog.search(where={"time_added": record.time_added})

    assert [r.id for r in by_catalog] == [record.id]
    assert [r.id for r in by_time_added] == [record.id]
    assert catalog.search(where={"catalog": "user-catalog"}) == []
    assert catalog.search(where={"time_added": "user-time"}) == []


def test_search_prefers_user_metadata_before_derived_metadata(tmp_path: Path) -> None:
    source = tmp_path / "precedence.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(source, metadata={"species": "CO2"})
    record.derived_metadata["species"] = "CH4"
    catalog.repository.update(record)

    assert [r.id for r in catalog.search(where={"species": "CO2"})] == [record.id]
    assert catalog.search(where={"species": "CH4"}) == []


def test_search_supports_dotted_lookup_into_nested_dicts(tmp_path: Path) -> None:
    source = tmp_path / "nested.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(
        source,
        metadata={"product": {"family": {"name": "flux-suite", "revision": 2}}},
    )
    record.derived_metadata["netcdf"] = {"dims": {"time": 12}}
    catalog.repository.update(record)

    by_user_metadata_name = catalog.search(where={"user_metadata.product.family.name": "flux-suite"})
    by_user_metadata_revision = catalog.search(where={"user_metadata.product.family.revision": 2})
    by_missing_nested_path = catalog.search(where={"user_metadata.product.family.missing": "x"})

    assert [r.id for r in by_user_metadata_name] == [record.id]
    assert [r.id for r in by_user_metadata_revision] == [record.id]
    assert by_missing_nested_path == []


def test_get_and_path_return_none_for_missing_record(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    assert catalog.get("missing") is None
    assert catalog.path("missing") is None
