# Transactions and logging

## Operation lifecycle

Every ``add_file()`` or ``add_artifact()`` call runs inside a *unit of work*.
The unit of work tracks rollback actions so that a failed operation leaves the
catalog in a consistent state.

The typical lifecycle is:

1. Hooks fire in order (``before_validate_metadata``, ``resolve_artifact_locator``, …).
2. Any file write happens.
3. The record is written to the database.
4. Post-write hooks fire (``after_record_write``, ``before_commit``, ``after_commit``).

If any step raises an exception, registered rollback actions run in reverse
order to undo any partial writes.

## Audit events

Each catalog writes structured audit events to
``<catalog-root>/.ogcat/logs/events.jsonl``. The log is append-only during
normal operation and is intended for maintainers who need to answer which
operation ran, which user ran it, which record was touched, and where a failure
occurred.

Add operations emit lifecycle events for operation start, validation, storage
or record writes, commit, failure, and rollback. Delete and restore operations
emit the same operation-start, lifecycle, commit, failure, and rollback events.
Purge operations additionally emit one ``purge_artifact`` event for each
managed artifact removed or skipped, then hard-delete the record after artifact
cleanup succeeds. If one artifact removal fails, purge continues with later
artifacts, audits the failures, retains the tombstoned record with incomplete
purge metadata, and reports failure to the caller. Events include an
``operation_id``; CLI operation failures
include that id in the user-facing error message when available:

```bash
ogcat logs --catalog ./catalog --operation OPERATION_ID --json
ogcat logs --catalog ./catalog --user alice --level error
```

Audit details intentionally avoid recording file contents, in-memory
``OperationSource.payload`` values, or full user metadata payloads. Metadata key
names and operation context are logged for diagnostics, and sensitive-looking
keys such as tokens, passwords, credentials, API keys, and private keys are
redacted when values are included in audit details.

## Rollback

Rollback is best-effort.  Each rollback action is tried in turn; if one
fails, the remaining actions still run and the original error is preserved.

``Catalog.delete()`` and ``Catalog.restore()`` update record lifecycle state
through the same unit-of-work rollback model used by metadata updates, so an
uncommitted caller-owned transaction restores the previous record state.
``Catalog.purge()`` is permanent and best-effort: it removes only managed
catalog-local path-backed artifacts through storage adapters, audits skipped
external or user-owned locators, and hard-deletes the repository record last
only when cleanup succeeds. Partial purge failures cannot restore artifacts
that were already removed, so ogcat commits an updated tombstone describing the
incomplete attempt before raising the error.

You can register rollback actions from within a hook:

```python
class MyHook:
    def before_record_write(self, context):
        path = write_something(context)
        context.rollback(
            lambda: path.unlink(missing_ok=True),
            description=f"remove {path}",
        )
```

## Operation state

``OperationState`` records the outcome of work tracked by the internal
``UnitOfWork``. ``add_file()`` and ``add_artifact()`` return a ``CatalogRecord``;
they do not expose the operation context or final unit-of-work state. Callers
only inspect ``OperationState`` directly when they manage a transaction
themselves, for example by passing a caller-owned transaction into
``add_artifact()``.

## Using UnitOfWork directly

Most callers never need ``UnitOfWork`` directly.  It is used internally by the
catalog and exposed for advanced use cases such as building a custom catalog
method that must participate in the same rollback lifecycle.

```python
from ogcat.transactions import UnitOfWork

with UnitOfWork(catalog.repository) as uow:
    uow.register_rollback(
        lambda: cleanup(),
        description="cleanup on failure",
    )
    do_work()
    uow.commit()
    # If commit is not called, the context manager rolls back on exit.
```
