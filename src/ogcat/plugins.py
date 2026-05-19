"""Simple plugin registry for catalog lifecycle hooks."""

from __future__ import annotations

from collections.abc import Iterable

from ogcat.capabilities import ArtifactCapability, CapabilityRegistry
from ogcat.hooks import HookManager


class PluginRegistry:
    """Registry for direct Python plugin registration.

    Args:
        hooks: Hook objects to register in dispatch order.
        capabilities: Artifact capabilities to register in lookup order.
    """

    def __init__(
        self,
        hooks: Iterable[object] = (),
        *,
        capabilities: Iterable[ArtifactCapability] = (),
    ) -> None:
        """Create a plugin registry.

        Args:
            hooks: Hook objects to register in dispatch order.
            capabilities: Artifact capabilities to register in lookup order.
        """
        self._hooks: list[object] = list(hooks)
        self._capabilities = CapabilityRegistry(capabilities)

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

    def register_capability(self, capability: ArtifactCapability) -> ArtifactCapability:
        """Register an artifact capability and return it."""
        return self._capabilities.register(capability)

    def list_capabilities(self) -> tuple[ArtifactCapability, ...]:
        """Return registered artifact capabilities in insertion order."""
        return self._capabilities.list()

    def capability_registry(self) -> CapabilityRegistry:
        """Return the plugin capability registry."""
        return self._capabilities


__all__ = ["PluginRegistry"]
