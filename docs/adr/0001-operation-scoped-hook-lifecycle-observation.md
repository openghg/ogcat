# ADR 0001: Operation-Scoped Hook Lifecycle Observation

- **Status**: Proposed
- **Date**: 2026-05-14
- **Related issues**: [openghg/ogcat#75](https://github.com/openghg/ogcat/issues/75)

## Context

`ogcat` hooks are currently defined as a loose set of protocols in `hooks.py`.
`HookManager` stores long-lived hook objects and exposes lifecycle methods such
as `before_validate_metadata`, `resolve_artifact_locator`, and
`before_record_write`. `Catalog` controls when those hook methods are called as
part of an add operation.

Catalog-scoped audit logging introduced a new requirement: hook dispatch should
produce structured diagnostics for hook phases without moving hook phase
metadata into `catalog.py` and without making `HookManager` depend directly on
JSON Lines audit logging.

The current add-operation implementation is also large. A broader future
refactor should extract operation orchestration into an `OperationRunner`, but
this ADR focuses only on the hook lifecycle observation boundary.

## Requirements

- Preserve existing public hook method signatures.
- Preserve compatibility with plugin objects that satisfy hook protocols by
  implementing one or more lifecycle methods.
- Keep user-facing hook registration simple: users should pass hooks to
  `Catalog.create`, `Catalog.open`, or `HookManager` as they do today.
- Keep observer/callback lifetime operation-scoped so one catalog object can run
  different operations with different diagnostics behavior.
- Avoid global observer attach/detach requirements on the long-lived
  `HookManager`.
- Keep `HookManager` independent of audit storage details, catalog paths, user
  ids, and JSON Lines.
- Allow tests to target stable interfaces rather than concrete audit
  implementation details.

## Decision

Adopt an operation-scoped hook dispatch model:

- `HookManager` remains the long-lived registry of hook objects.
- A new operation-scoped dispatcher, tentatively `HookDispatcher`, performs hook
  invocation for one catalog operation.
- Hook phase metadata is defined in `hooks.py`, close to `HOOK_METHOD_NAMES` and
  the hook protocols.
- The dispatcher emits hook lifecycle notifications through callbacks supplied
  for that operation.
- Audit logging is implemented as one callback adapter. The callback converts
  hook lifecycle events into `AuditEvent` objects, but the dispatcher does not
  know about audit sinks or JSONL.

The core interface should look conceptually like this:

```python
@dataclass(frozen=True)
class HookPhase:
    name: str
    label: str


@dataclass(frozen=True)
class HookLifecycleEvent:
    phase: HookPhase
    stage: Literal["started", "completed", "failed"]
    context: OperationContext
    hook_count: int
    warnings_added: int = 0
    error: BaseException | None = None


HookLifecycleCallback = Callable[[HookLifecycleEvent], None]
```

`HookManager` should provide an operation-scoped dispatcher:

```python
dispatcher = catalog.hook_manager.dispatcher(notify=operation_hook_callback)
dispatcher.before_validate_metadata(context)
dispatcher.resolve_artifact_locator(context)
```

The existing `HookManager.before_validate_metadata(context)` style methods
should remain available for callers that do not need operation-scoped
observation. They can delegate to a dispatcher with no callback.

## Rationale

`Catalog` owns operation ordering, but not hook definitions. It should decide
that validation precedes locator resolution and that commit follows record
write. It should not need to know every formal hook phase name for audit
purposes.

`HookManager` owns hook dispatch facts. When its `before_record_write` behavior
is invoked, it knows which lifecycle method is being dispatched, how many hooks
implement that method, and whether dispatch started, completed, or failed. Those
facts are exactly what observers need.

Callbacks are operation-scoped rather than attached globally. A catalog object
can perform many operations over its lifetime, and those operations may need
different diagnostics policies. Passing callbacks to a dispatcher avoids
long-lived observer identity, detach ordering, stale observers, and cross-
operation leakage.

The dispatcher should pass the phase as event data. Observers should not store a
mutable "current phase" that `Catalog` sets before calling `HookManager`.
Mutable observer phase state would create temporal coupling and could report the
wrong phase if code forgets to update it or if nested dispatch is introduced.

## Options Considered

### Option 1: HookManager directly owns audit recording

`HookManager` could accept an audit recorder and emit audit events around each
hook method. This keeps hook phases out of `catalog.py`, but it couples hook
dispatch to audit vocabulary. Future metrics or tracing would either reuse
audit-specific interfaces or add another parallel mechanism.

This option is simpler but less general.

### Option 2: Operation-scoped lifecycle callbacks

`HookManager` creates a dispatcher for a single operation. The dispatcher emits
generic lifecycle events to callbacks. Audit is one callback adapter.

This option is slightly more abstract, but it clearly separates hook dispatch
from audit storage and solves callback lifetime problems.

This ADR chooses Option 2.

### Option 3: Global observer attachment on HookManager

`HookManager` could support `attach(observer)` and `detach(observer)` and notify
all attached observers. This follows the classic observer pattern, but it is a
poor fit for operation-specific diagnostics because `HookManager` is long-lived.
It would require careful detach behavior to avoid observers leaking between
operations.

This option is rejected.

### Option 4: Catalog-managed mutable observer phase

`Catalog` could set `observer.phase = "before_record_write"` before invoking a
hook method. This makes `Catalog` own phase naming explicitly, but it relies on
mutable side state and makes incorrect sequencing easy.

This option is rejected.

## Testing Guidance

Tests should target the interfaces created by this decision:

- A dispatcher invokes only hooks that implement the current phase.
- A dispatcher emits `started`, `completed`, and `failed` lifecycle events with
  the expected `HookPhase`, hook count, warning count, context, and exception.
- A dispatcher with no callback preserves current hook behavior.
- A callback failure policy is explicit. Audit callbacks should be best-effort
  and should not fail hook dispatch.
- Audit integration tests should assert conversion from `HookLifecycleEvent` to
  `AuditEvent`, not duplicate dispatcher behavior.
- Catalog operation tests should assert that operation flow receives hook
  diagnostics through the callback interface, not by inspecting private phase
  constants.

Where an interface is internal but important, a small abstract base class or
dataclass should be preferred if it makes the contract clearer than a loose
callable. Protocols remain appropriate for plugin author extension points.

## Consequences

Positive consequences:

- Hook phase metadata moves out of `catalog.py`.
- Audit logging can observe hook dispatch without coupling `HookManager` to
  audit sinks.
- Per-operation callbacks avoid attach/detach lifecycle bugs.
- The design creates a reusable path for metrics, tracing, or debug callbacks.
- Tests can focus on stable dispatch and lifecycle event interfaces.

Negative consequences:

- The hook subsystem gains an additional internal concept: an operation-scoped
  dispatcher.
- Catalog code must create and pass a callback or dispatcher for observed
  operations.
- The implementation must preserve existing `HookManager` methods to avoid
  unnecessary public API churn.

## Deferred Questions

- Should operation-specific hook selection also live on the dispatcher?
- Should `OperationContext` eventually hold optional stage outputs such as a
  validation report?
- Should a future `OperationRunner` own dispatcher creation and audit callback
  wiring once operation orchestration is extracted from `Catalog`?
