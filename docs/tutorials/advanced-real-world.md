# Tutorial: advanced real-world catalogs

The scripts in this section are optional, larger examples for existing ACRG
data trees. They are useful demonstrations of external-reference catalogs, but
they are intentionally not the first learning path.

Both scripts can work from a mounted filesystem or from saved recursive
`ls -R` output. Listing mode records paths and filename-derived metadata only;
mounted-scan mode can also inspect file sizes, archive contents, and optional
netCDF summaries.

## Personal flux catalog

The flux script catalogs files under a tree like `/group/chem/acrg/OC/fluxes`
as external path-backed records. It infers fields such as `top_collection`,
`product`, `species`, `domain`, `sector`, `temporal_resolution`, `year`,
`archive_format`, and `file_role`.

Run it from a saved listing:

```bash
uv run python examples/catalog_fluxes.py catalog /tmp/ogcat-fluxes --listing examples/data/personal_fluxes_recursive_ls.txt --no-enrich
```

Run it from a mounted source tree:

```bash
uv run python examples/catalog_fluxes.py catalog /tmp/ogcat-fluxes --source-root /group/chem/acrg/OC/fluxes
```

The script uses batched `add_artifacts()` calls with `ArtifactLocator.path(...)`
and `storage_mode="external"`, so source files stay where they are.

## ACRG NAME footprint catalog

The footprint script catalogs monthly NAME footprint series under a tree like
`/group/chem/acrg/LPDM/fp_NAME` as collection artifacts. Each record represents
one logical series and stores fields such as `site`, `inlet`, `model`,
`met_model`, `domain`, `species`, `years`, `year_start`, `year_end`,
`member_count`, and `collection_pattern`.

Run it from a saved listing:

```bash
uv run python examples/catalog_acrg_name_footprints.py /tmp/ogcat-footprints --listing examples/data/acrg_name_footprints_recursive_ls.txt
```

Run it from a mounted source tree:

```bash
uv run python examples/catalog_acrg_name_footprints.py /tmp/ogcat-footprints --source-root /group/chem/acrg/LPDM/fp_NAME
```

The footprint script uses a named metadata schema for required fields and
records collection roots with `Catalog.add_collection(...)`. It does not copy
or open NetCDF member files. Listing mode uses URI locators for collection roots
that are not mounted locally; mounted mode records path-backed directory
locators.

## After building

Inspect records with the CLI:

```bash
uv run ogcat info --catalog /tmp/ogcat-fluxes
uv run ogcat search --catalog /tmp/ogcat-fluxes species=CO2 --fields id,product,year,path
uv run ogcat search --catalog /tmp/ogcat-footprints site=BCOB --fields id,domain,year_start,year_end,member_count,path
```
