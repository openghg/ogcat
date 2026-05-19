"""Bundled plugin registration helpers shipped with ogcat.

The bundled namespace contains dependency-light examples that register through
the same extension points as external plugins. The current implementation
exports the stdlib I/O capability examples for tests and documentation.
"""

from ogcat.bundled_plugins.stdlib_io import register_stdlib_capabilities, stdlib_capabilities

__all__ = ["register_stdlib_capabilities", "stdlib_capabilities"]
