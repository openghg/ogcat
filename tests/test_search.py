from pathlib import Path

import pytest

from ogcat import ArtifactLocator, Catalog, CatalogSpec, SearchOp, SearchQuery


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


def test_search_query_supports_nested_user_and_derived_shortcuts(tmp_path: Path) -> None:
    source = tmp_path / "nested-alias.202401.nc"
    source.write_text("dummy", encoding="utf-8")

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_file(
        source,
        metadata={"site": {"code": "MHD", "country": "IE"}, "species": "co2"},
    )
    record.derived_metadata["netcdf"] = {"dims": {"time": 12}}
    catalog.repository.update(record)

    query = (
        SearchQuery.eq("user.site.code", "MHD")
        .match("user.species", "co*")
        .eq("derived.netcdf.dims.time", 12)
    )

    assert [r.id for r in catalog.search(query)] == [record.id]
    assert query.terms[0].field.stored == "user_metadata.site.code"
    assert query.terms[0].op == SearchOp.EQ


def test_search_supports_list_membership_and_contains(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    tagged = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/tagged.zarr"),
        metadata={"tags": ["paris", "obspack"], "title": "Paris selection"},
    )
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/other.zarr"),
        metadata={"tags": ["baseline"], "title": "Baseline selection"},
    )

    by_contains = catalog.search(contains={"tags": "paris"})
    by_contains_all = catalog.search(contains={"tags": ["paris", "obspack"]})
    by_contains_missing = catalog.search(contains={"tags": ["paris", "missing"]})
    by_query_contains = catalog.search(query=SearchQuery.contains("tags", "obspack"))
    by_title_substring = catalog.search(contains={"title": "Paris"})

    assert [r.id for r in by_contains] == [tagged.id]
    assert [r.id for r in by_contains_all] == [tagged.id]
    assert by_contains_missing == []
    assert [r.id for r in by_query_contains] == [tagged.id]
    assert [r.id for r in by_title_substring] == [tagged.id]


def test_search_contains_supports_mapping_subset_and_unhashable_values(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/site.zarr"),
        metadata={"site": {"code": "MHD", "tags": ["coastal", "baseline"]}},
    )

    by_subset = catalog.search(contains={"site": {"code": "MHD"}})
    by_mismatch = catalog.search(contains={"site": {"code": "TAC"}})
    by_unhashable_key = catalog.search(contains={"site": ["code"]})

    assert [r.id for r in by_subset] == [record.id]
    assert by_mismatch == []
    assert by_unhashable_key == []


def test_search_supports_exists_missing_and_heterogeneous_records(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    with_site = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/site.zarr"),
        metadata={"site": {"code": "MHD"}, "species": "co2"},
    )
    without_site = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/no-site.zarr"),
        metadata={"species": "ch4", "platform": None},
    )

    by_exists = catalog.search(exists=["user.site.code"])
    by_missing = catalog.search(missing=["user.site.code"])
    by_null_exists = catalog.search(exists=["user.platform"])

    assert [r.id for r in by_exists] == [with_site.id]
    assert [r.id for r in by_missing] == [without_site.id]
    assert [r.id for r in by_null_exists] == [without_site.id]


def test_search_shortcuts_resolve_for_whole_metadata_namespaces(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/site.zarr"),
        metadata={"site": {"code": "MHD"}},
        derived_metadata={"checksum": "abc123"},
    )

    assert [r.id for r in catalog.search(query=SearchQuery.exists("user"))] == [record.id]
    assert [r.id for r in catalog.search(query=SearchQuery.exists("derived"))] == [record.id]
    assert catalog.search(query=SearchQuery.missing("user")) == []


def test_search_equality_against_list_requires_exact_list(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    tagged = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/tagged.zarr"),
        metadata={"tags": ["paris", "obspack"]},
    )

    assert catalog.search(where={"tags": "paris"}) == []
    assert [r.id for r in catalog.search(where={"tags": ["paris", "obspack"]})] == [tagged.id]


def test_search_query_supports_locator_uri_and_string_match(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/path/example.zarr"),
        metadata={"title": "ObsPack Paris product"},
    )

    query = SearchQuery.matches("locator.uri", "s3://bucket/*/example.zarr").and_(
        SearchQuery.matches("title", "paris")
    )

    assert [r.id for r in catalog.search(query=query, ignore_case=True)] == [record.id]


def test_get_and_path_return_none_for_missing_record(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    assert catalog.get("missing") is None
    assert catalog.path("missing") is None


def test_find_and_find_ids_use_search_filters(tmp_path: Path) -> None:
    """Selection helpers return records and stable string ids in search order."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    co2 = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/co2.zarr"),
        metadata={"species": "co2", "provenance": "derived", "keywords": ["paris_verification_games"]},
    )
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/ch4.zarr"),
        metadata={"species": "ch4", "provenance": "derived", "keywords": ["baseline"]},
    )

    records = catalog.find(
        where={"provenance": "derived", "species": "co2"},
        contains={"keywords": "paris_verification_games"},
    )
    ids = catalog.find_ids(
        where={"provenance": "derived", "species": "co2"},
        contains={"keywords": "paris_verification_games"},
    )

    assert records == [co2]
    assert ids == [co2.id]
    assert all(isinstance(record_id, str) for record_id in ids)


def test_get_one_returns_record_for_exactly_one_match(tmp_path: Path) -> None:
    """get_one returns the matching record for notebook-style lookup workflows."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    record = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/gridfed-total-co2.zarr"),
        metadata={"product": "GridFED", "sector": "TOTAL", "species": "co2"},
    )

    selected = catalog.get_one(
        where={
            "product": "GridFED",
            "sector": "TOTAL",
            "species": "co2",
        }
    )

    assert selected == record
    assert selected.locator.value == "s3://bucket/gridfed-total-co2.zarr"


def test_get_one_raises_clear_errors_for_zero_and_multiple_matches(tmp_path: Path) -> None:
    """get_one reports empty and ambiguous selections clearly."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))
    first = catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/first.zarr"),
        metadata={"species": "co2"},
    )
    catalog.add_artifact(
        record_type="external_reference",
        locator=ArtifactLocator(kind="uri", value="s3://bucket/second.zarr"),
        metadata={"species": "co2"},
    )

    with pytest.raises(ValueError, match="no records"):
        catalog.get_one(where={"site": "NO_SUCH_SITE"})
    with pytest.raises(ValueError, match="multiple records"):
        catalog.get_one(where={"species": "co2"})

    assert catalog.get_one(where={"species": "co2"}, allow_many=True) == first


def test_search_rejects_set_where_filters(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    with pytest.raises(TypeError, match="where must be a mapping of field names to expected values"):
        catalog.search(where={"product", "gridfed"})  # type: ignore[arg-type]


def test_search_rejects_bare_string_exists_filters(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="fluxes"))

    with pytest.raises(TypeError, match="exists must be a sequence of field names"):
        catalog.search(exists="site")  # type: ignore[arg-type]


def test_search_query_from_filters_validates_filter_shapes() -> None:
    with pytest.raises(TypeError, match="contains must be a mapping of field names to expected values"):
        SearchQuery.from_filters(contains=["site"])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match=r"regex\['version'\] must be a string pattern"):
        SearchQuery.from_filters(regex={"version": 42})  # type: ignore[dict-item]

    with pytest.raises(TypeError, match=r"match\['title'\] must be a string pattern"):
        SearchQuery.from_filters(match={"title": ["Paris"]})  # type: ignore[dict-item]

    with pytest.raises(TypeError, match="missing must be a sequence of field names"):
        SearchQuery.from_filters(missing=b"site")  # type: ignore[arg-type]
