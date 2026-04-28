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
ogcat add <file> --catalog <root> [--meta KEY=VALUE ...] [--mode copy|move]
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

Print declared metadata fields from the catalog spec.

```
ogcat fields --catalog <root> [--record-type TYPE] [--json]
```
