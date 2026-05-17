# examples/data

This directory is the home for shared example data that does not belong to a
single example.

See the [examples data policy](../../docs/examples-data-policy.md) for the rules
that govern what may be committed here.

## Listing fixtures

``acrg_name_footprints_recursive_ls.txt``
:   A small sanitized ``ls -R`` sample derived from the structure of an ACRG
    NAME footprint tree. It preserves realistic domain/site/file naming while
    keeping only a few monthly NetCDF members.

``personal_fluxes_recursive_ls.txt``
:   A small sanitized ``ls -R`` sample derived from a personal flux-data tree.
    The path uses the fake owner code ``OC`` rather than a real personal
    directory name.

These fixtures are intentionally tiny. They validate the path parsing and
catalog-building examples without committing the complete recursive listings or
any scientific payload files.
