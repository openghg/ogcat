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

## Rollback

Rollback is best-effort.  Each rollback action is tried in turn; if one
fails, the remaining actions still run and the original error is preserved.

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
