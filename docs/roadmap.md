# Roadmap

This roadmap describes likely next steps from the current codebase. Everything in this document is future work unless it is already present in the package today.

## Today

`ogcat` is currently a spec-driven file catalog with:

- managed file ingest by copy or move
- flexible user metadata
- lightweight derived metadata extraction
- template-based naming
- exact, contains, and regex search
- a small Python API and CLI

That is the current product boundary. The near-term roadmap should extend this core without pulling domain-specific logic into the package.

## Near-Term Priorities

### Record Typing and Locator Abstraction

Future work: generalise from managed files to catalogued artefacts with clearer record typing and a more explicit locator model.

Today each record assumes a stored file path. A next step would be to separate "what this record is" from "how it is located", while keeping the current managed-file flow as one concrete case. This would make it easier to represent additional artefact types later without rewriting the catalog interface around a single path shape.

### Typed or Per-Record Schemas

Current direction: keep a broad `default_schema` plus optional named schemas for record types.

Schemas remain lightweight: they describe expected metadata fields and naming templates, and only
required-field presence is validated at ingest. Rich type validation, automatic dispatch, and
domain-specific built-in schemas remain out of scope for the catalog core.

### Reader Hooks

Future work: extend the current extractor model into explicit reader hooks.

Today derived metadata extractors run after ingest and store small summaries. A later step could add structured hooks for opening or interpreting stored artefacts, but that does not exist yet. Any such work should stay narrow and avoid turning the package into a large plugin framework too early.

### Manager Bindings

Future work: add lightweight bindings to external managers or higher-level catalog consumers.

There is no manager API in the code today. If this appears, it should sit on top of the current catalog core rather than leaking manager-specific concepts into record storage and search.

### Catalog Creation From Existing Data

Future work: support creating catalogs from data that already exists on disk.

The current implementation assumes managed ingest into the catalog's own `files/` tree. A practical next step is a scan or import workflow that can build records from existing data with explicit rules, rather than requiring each file to be added one at a time through the current path-based flow.

## Longer-Term Direction

Later work may include:

- broader artefact support beyond managed files
- richer schema behavior where it stays lightweight
- reader and manager integration points
- scan or import workflows for existing datasets

These are not current features. They should only be added if they preserve the package's current small surface area and keep the core catalog logic independent from domain-specific behavior.
