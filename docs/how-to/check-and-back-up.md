# Check and back up a local catalog

Inspect registered local paths without changing the catalog:

```bash
ogcat check --catalog /path/to/catalog
ogcat check --catalog /path/to/catalog --include-deleted --json
```

The Python equivalent is `catalog.check(include_deleted=False)`. Each issue
contains `record_id`, `artifact_id`, `code`, `path`, and `message`. The codes are
`missing_path`, `broken_symlink`, `incorrect_symlink`, and `unreadable_path`.
Recorded `view_of` symlinks are checked against their target artifact descriptor.
Tombstoned records are excluded by default; purged artifacts are always skipped.

Exit status is 0 when no issues are observed and 1 when issues are found.
An empty issue list only describes registered local paths at inspection time.
The check does not validate file contents, contact remote services, scan collection
members, discover orphan files, or repair anything. Concurrent changes to external
files can affect the result. Review each message and restore the missing path,
correct the symlink, or update the record as appropriate.

## Make a backup

Choose a new destination outside the catalog root. Acquire the catalog's writer
lock while copying its complete directory tree:

```python
from pathlib import Path
from shutil import copytree

from ogcat import Catalog

root = Path("/path/to/catalog")
destination = Path("/path/to/backups/catalog-2026-10-02")
with Catalog.open(root):
    copytree(root, destination, symlinks=True)
```

Preserving symlinks avoids copying their external targets. The tree includes
`catalog.json`, the record database, managed objects, readable links, and audit
logs when they use the default locations under the root. If the spec configures
external database, storage, or audit paths, copy those separately while holding
the same lock. External reference targets need their own backup; this procedure
leaves them untouched. Stop applications that might independently modify managed
files while the backup runs. `copytree` requires a destination that does not exist;
a failed copy can leave an incomplete destination, which should not be used as a
backup.

## Restore a backup

Stop all catalog processes first. Preserve the current directory if it contains
work you may need, then restore the backup tree to the **original absolute root**
using `copytree(backup, root, symlinks=True)` with a new destination. Restore any
configured external storage/database/audit paths to their original locations too.
Records can contain absolute paths, so opening a backup at a different root does
not relocate its artifacts. Keep external reference targets available at their
recorded paths. Run `ogcat check --include-deleted` after reopening the restored
catalog and review any issues before resuming writes.
