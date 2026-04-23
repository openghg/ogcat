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

    assert [r.id for r in by_species] == [record.id]
    assert [r.id for r in by_title] == [record.id]
    assert [r.id for r in by_id] == [record.id]
