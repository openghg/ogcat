"""Simple plugin registry for catalog lifecycle hooks."""

from __future__ import annotations

from collections.abc import Iterable

from ogcat.hooks import HookManager


class PluginRegistry:
    """Registry for direct Python hook registration.

    Args:
        hooks: Hook objects to register in dispatch order.
    """

    def __init__(self, hooks: Iterable[object] = ()) -> None:
        self._hooks: list[object] = list(hooks)

    @property
    def hooks(self) -> tuple[object, ...]:
        """Registered hooks in insertion order."""
        return tuple(self._hooks)

    def register(self, hook: object) -> object:
        """Register a hook object and return it for decorator-style usage."""
        self._hooks.append(hook)
        return hook

    def hook_manager(self) -> HookManager:
        """Build a hook manager from the currently registered hooks."""
        return HookManager(self._hooks)


__all__ = ["PluginRegistry"]
