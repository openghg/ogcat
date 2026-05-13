# CLI reference

``ogcat`` is the command-line interface.

## Commands

### ``ogcat init``

Create a new catalog.

```
ogcat init <root> [--name NAME]
```

### ``ogcat add``

Ingest a file into a catalog.

```
ogcat add <file> --catalog <root> [--meta KEY=VALUE ...] [--operation copy|move]
```

### ``ogcat search``

Search catalog records.

```
ogcat search --catalog <root> [FILTER ...] [OPTIONS]
```

**Positional filter syntax**

| Syntax | Meaning |
|--------|---------|
| ``field=value`` | Exact equality |
| ``field:value`` | Contains / list membership |
| ``field~pattern`` | Glob or substring match |
| ``field?`` | Field exists |
| ``!field?`` | Field is missing |

**Output options**

| Flag | Behaviour |
|------|-----------|
| ``--json`` | Print full matching records as JSON |
| ``--ids`` | Print record ids only |
| ``--paths`` | Print stored paths only |
| ``--fields a,b,c`` | Choose displayed columns |
| ``--format table\|plain\|csv\|tsv\|pipe`` | Table format |
| ``--limit N`` | Cap on displayed results |
| ``--all`` | Show every match (no cap) |

**Compatibility flags** (also available):
``--where``, ``--contains``, ``--match``, ``--regex``, ``--exists``, ``--missing``, ``--ignore-case``

Use ``--ids`` when piping search results into ID-based commands:

```bash
record_id=$(ogcat search --catalog <root> species=CO2 --ids --limit 1)
ogcat show "$record_id" --catalog <root>
ogcat path "$record_id" --catalog <root>
```

### ``ogcat show``

Print a single record.

```
ogcat show <id> --catalog <root>
```

### ``ogcat path``

Print the stored path of a record.

```
ogcat path <id> --catalog <root>
```

### ``ogcat info``

Print catalog statistics.

```
ogcat info --catalog <root>
```

### ``ogcat fields``

Print schema-declared metadata fields from the catalog spec, or inspect fields
found in stored records.

```
ogcat fields --catalog <root> [--record-type TYPE] [--stored] [--values FIELD] [--json]
```

``--stored`` lists field paths currently present in records, including nested
metadata paths such as ``user_metadata.site.code``. ``--values FIELD`` prints
unique scalar values for one field.

### ``ogcat spec``

Update small, safe parts of ``catalog.json``.

```
ogcat spec show-schema [TYPE] --catalog <root> [--json]
ogcat spec add-schema NAME --catalog <root> --schema-json JSON_OR_PATH [--overwrite]
ogcat spec set-default-schema NAME --catalog <root>
ogcat spec set FIELD=VALUE ... --catalog <root>
```

``ogcat spec show-schema --json`` prints the full serialisable schema,
including ``display_fields`` when configured.

``ogcat spec set`` supports simple fields such as ``catalog_name``,
``default_operation``, and ``field_resolution_order``. ``files_root`` changes
are rejected because they require a dedicated file migration operation.
