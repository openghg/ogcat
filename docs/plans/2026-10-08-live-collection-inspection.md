# Live collection inspection

Status: implemented and locally reviewed; BP1 integration pending environment
setup approval, 2026-10-08.

## Need and scope

Collection records currently describe a root and member pattern. Listing paths
alone leaves every caller to invent metadata extraction and filtering. Add a
read-side view of member locators and metadata, without registering one record
per file or running an ingest transaction during inspection.

The catalog continues to search its stored records. A collection enumerates its
current matching files. Both use `SearchQuery` predicates; their sources of
membership remain distinct. This recovers the useful browsing part of the
archived virtual-filesystem proposal without its storage-resource and lease
machinery.

## Decisions

- `Catalog.members(record_id, extractor=..., query=...)` returns ordinary
  `CollectionEntry` objects with a locator and flat metadata. Entries have no
  persisted ID, lifecycle, or ownership. Existing `member_paths()` stays usable.
- A member extractor accepts a local `Path` and returns metadata. An ingest
  hook adapter calls the same function on the local source before writing,
  preserving filename metadata when UUID storage renames or moves the source.
  Operations without a local source use the existing custom hook interface.
  Discovery never dispatches the catalog's mutation hooks. Extractor callbacks
  are trusted caller code and must keep inspection read-only.
- Nested traversal is explicit: an entry needs a declared member pattern or
  a pattern passed to its `members()` call. Plain directories and Zarr stores
  remain leaves until explicitly opened as collections. There is no automatic
  recursive walk or metadata inheritance.
- Reuse existing local pattern/containment checks. Logical collections can
  share one physical directory and select different members. Membership never
  grants permission to purge discovered files.
- `SearchQuery.date_between(field, start, end, format=...)` uses the same
  explicit string format for values and inclusive bounds. Missing/null dates
  do not match; malformed dates and invalid bounds raise. No pandas dependency
  or automatic date guessing is introduced.
- The NAME example supplies month metadata using its existing filename
  parsing. Its month selector uses the general member API while retaining
  complete-coverage and duplicate-month validation.

## Validation and limits

Tests cover metadata extraction shared with ingest, no browse-side persistence
or mutation-hook dispatch, live membership changes, explicit nested traversal,
directory leaves, path containment, and the same date query on stored records
and discovered metadata. Existing NAME selection regressions remain required.

Run the candidate in an isolated BP1 checkout under the requested working
directory against the existing footprint catalog in read-only mode. Keep
private paths/results in the private tutorial and test report, outside GitHub.
Record exact validation outcomes here before delivery.

Local validation: 679 tests passed with four optional-backend skips in 37.98
seconds. Full Ruff checks/formatting and configured Pyright passed. The offline
Sphinx HTML build passed with warnings treated as errors. Independent review
found and resolved two edge cases: dates are parsed after ordinary filters and
record lifecycle visibility, and source-less generated writers no longer expose
an unwritten destination as their input to extraction hooks. The actual NAME
filename extractor was also exercised through managed move ingestion, preserving
its original month metadata after UUID renaming.

The isolated BP1 run requires a new project virtual environment. Repository
instructions require confirmation before creating it; the request is pending.
The prepared read-only integration check compares the direct member query with
the NAME coverage-checking helper, distinguishes meteorological series, and
checks that production catalog bytes and record count remain unchanged.

This change does not add remote listing, a persisted member index, automatic
recursion, atomic filesystem snapshots, generic completeness validation, or
new CLI date syntax. Expensive file-content parsing is opt-in through the
extractor; ordinary filename parsing does not open scientific data.
