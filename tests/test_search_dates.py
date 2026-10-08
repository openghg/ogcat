"""Date query and plain metadata search regression tests."""

from pathlib import Path

import pytest

from ogcat import Catalog, CatalogSpec, SearchQuery
from ogcat.search import matches_metadata, matches_record


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-01-01", True),
        ("2024-01-31", True),
        ("2024-01-15", True),
        ("2023-12-31", False),
        ("2024-02-01", False),
        (None, False),
    ],
)
def test_date_range_includes_bounds(value: str | None, expected: bool) -> None:
    """Date ranges include both bounds and reject null or outside values."""
    query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31")
    assert matches_metadata({"date": value}, query=query) is expected
    assert not matches_metadata({}, query=query)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2024-02-30", "2024-03-01", "Date range start"),
        ("2024-01-01", "bad", "Date range end"),
        ("2024-02-01", "2024-01-01", "before or equal"),
    ],
)
def test_invalid_bounds_fail_before_empty_search(tmp_path: Path, start: str, end: str, message: str) -> None:
    """Invalid bounds fail at construction even when a catalog has no records."""
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="dates"))
    with pytest.raises(ValueError, match=message):
        catalog.search(query=SearchQuery.date_between("date", start, end))


@pytest.mark.parametrize("value", ["nonsense", "2024-01-10T12:00:00", "10/01/2024"])
def test_malformed_metadata_reports_field_and_format(value: str) -> None:
    """Present malformed dates raise without guessing formats or truncating time."""
    query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31")
    with pytest.raises(ValueError, match=r"Date field 'date'.*%Y-%m-%d"):
        matches_metadata({"date": value}, query=query)


def test_explicit_format_preserves_time_and_timezone() -> None:
    """An explicit format applies equally to bounds and timezone-aware metadata."""
    query = SearchQuery.date_between(
        "time",
        "01/01/2024 12:00 +0000",
        "01/01/2024 14:00 +0000",
        format="%d/%m/%Y %H:%M %z",
    )
    assert matches_metadata({"time": "01/01/2024 15:00 +0100"}, query=query)
    assert not matches_metadata({"time": "01/01/2024 15:01 +0100"}, query=query)
    with pytest.raises(ValueError, match="Date range start"):
        SearchQuery.date_between("time", "2024-01-01", "2024-01-31", format="%d/%m/%Y")


def test_metadata_and_record_queries_share_operators(tmp_path: Path) -> None:
    """Chained date predicates use the same evaluator for records and metadata."""
    metadata = {"species": "CO2", "period": {"start": "2024-01-15"}, "null": None}
    source = tmp_path / "source.nc"
    source.write_text("test", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="dates"))
    record = catalog.add_file(source, metadata=metadata)
    query = SearchQuery.eq("species", "CO2").date_between("user.period.start", "2024-01-01", "2024-01-31")
    assert matches_record(record, query=query, where=None, contains=None, regex=None, ignore_case=False)
    assert catalog.search(query=query).ids == [record.id]
    plain_query = SearchQuery.eq("species", "CO2").date_between("period.start", "2024-01-01", "2024-01-31")
    assert matches_metadata(
        metadata,
        query=plain_query,
        contains={"species": "co"},
        regex={"species": "^co"},
        match={"species": "c*"},
        exists=["null"],
        missing=["absent"],
        ignore_case=True,
    )
    assert not matches_metadata(metadata, where={"species": "CH4"})
    assert not matches_metadata(metadata, exists=["absent"])
    assert not matches_metadata(metadata, missing=["null"])


def test_metadata_paths_preserve_literal_namespace_names() -> None:
    """Plain metadata resolves user and derived as literal nested mapping keys."""
    assert matches_metadata({"user": {"species": "CO2"}}, where={"user.species": "CO2"})


def test_non_string_dates_fail_clearly() -> None:
    """Date queries do not silently coerce numeric metadata or bounds to strings."""
    with pytest.raises(TypeError, match="Date range start must be a date string"):
        SearchQuery.date_between("date", 20240101, "2024-01-31")
    query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31")
    with pytest.raises(TypeError, match="Date field 'date' must be a date string"):
        matches_metadata({"date": 20240101}, query=query)


@pytest.mark.parametrize("only_deleted", [False, True])
def test_date_search_ignores_malformed_dates_in_hidden_records(tmp_path: Path, only_deleted: bool) -> None:
    """Lifecycle visibility is applied before strict date parsing of candidates."""
    source = tmp_path / "source.nc"
    source.touch()
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="dates")) as catalog:
        valid = catalog.add_reference(source, metadata={"date": "2024-01-15"})
        malformed = catalog.add_reference(source, metadata={"date": "bad"})
        catalog.delete(valid.id if only_deleted else malformed.id)
        query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31")
        assert catalog.search(query=query, only_deleted=only_deleted).ids == [valid.id]
        with pytest.raises(ValueError, match="Date field 'date'"):
            catalog.search(query=query, include_deleted=True)


def test_date_search_applies_other_filters_before_parsing(tmp_path: Path) -> None:
    """An unrelated series with a malformed date cannot break a constrained search."""
    source = tmp_path / "source.nc"
    source.touch()
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="dates")) as catalog:
        valid = catalog.add_reference(source, metadata={"date": "2024-01-15", "site": "MHD"})
        catalog.add_reference(source, metadata={"date": "bad", "site": "OTHER"})
        query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31").eq("site", "MHD")
        assert catalog.search(query=query).ids == [valid.id]


def test_metadata_date_queries_filter_unrelated_series_before_parsing(tmp_path: Path) -> None:
    """A date-first query filters an unrelated malformed series before parsing."""
    query = SearchQuery.date_between("date", "2024-01-01", "2024-01-31").eq("site", "MHD")
    metadata = {"date": "bad", "site": "OTHER"}
    assert not matches_metadata(metadata, query=query)
    source = tmp_path / "source.nc"
    source.touch()
    with Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="dates")) as catalog:
        record = catalog.add_reference(source, metadata=metadata)
        assert not matches_record(
            record, query=query, where=None, contains=None, regex=None, ignore_case=False
        )
    with pytest.raises(ValueError, match="Date field 'date'"):
        matches_metadata({"date": "bad", "site": "MHD"}, query=query)


def test_empty_date_format_is_rejected() -> None:
    """An empty explicit format cannot turn empty metadata into a valid date."""
    with pytest.raises(ValueError, match="Date format cannot be empty"):
        SearchQuery.date_between("date", "", "", format="")
