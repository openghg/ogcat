"""Sphinx configuration for ogcat documentation."""

from __future__ import annotations

import importlib.metadata
import os
from types import UnionType
from typing import Any, Union, get_args, get_origin

project = "ogcat"
author = "OpenGHG Contributors"
release = importlib.metadata.version("ogcat")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
autodoc_member_order = "bysource"
suppress_warnings = [
    "config.cache",
    "sphinx_autodoc_typehints.forward_reference",
    "sphinx_autodoc_typehints.local_function",
]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False

intersphinx_mapping = {}
if os.environ.get("OGCAT_DOCS_OFFLINE") != "1":
    intersphinx_mapping = {
        "python": ("https://docs.python.org/3", None),
        "pydantic": ("https://docs.pydantic.dev/latest", None),
    }

myst_enable_extensions = ["colon_fence"]


def typehints_formatter(annotation: Any, _config: object) -> str | None:
    """Render recursive JSON aliases compactly in API docs."""
    if _is_optional_alias(annotation, "MetadataDict"):
        return ":py:data:`~ogcat.models.MetadataDict` | :py:obj:`None`"
    if _is_optional_alias(annotation, "JsonValue"):
        return ":py:data:`~ogcat.models.JsonValue` | :py:obj:`None`"
    if _is_alias(annotation, "MetadataDict"):
        return ":py:data:`~ogcat.models.MetadataDict`"
    if _is_alias(annotation, "JsonValue"):
        return ":py:data:`~ogcat.models.JsonValue`"
    return None


def _is_optional_alias(annotation: Any, alias_name: str) -> bool:
    """Return whether an annotation is one supported alias or None."""
    origin = get_origin(annotation)
    if origin not in {UnionType, Union}:
        return False
    args = get_args(annotation)
    non_none_args = [arg for arg in args if arg is not type(None)]
    return (
        len(non_none_args) == 1
        and any(arg is type(None) for arg in args)
        and _is_alias(non_none_args[0], alias_name)
    )


def _is_alias(annotation: Any, alias_name: str) -> bool:
    """Return whether an annotation has the runtime shape of a JSON alias."""
    if alias_name == "JsonValue":
        return _is_json_value_annotation(annotation)
    if alias_name == "MetadataDict":
        return _is_metadata_dict_annotation(annotation)
    return False


def _is_json_value_annotation(annotation: Any, *, depth: int = 0) -> bool:
    """Return whether an annotation has the shape of ``JsonValue``."""
    if depth > 2:
        return _is_json_value_forward_ref(annotation)

    origin = get_origin(annotation)
    if origin not in {UnionType, Union}:
        return _is_json_value_forward_ref(annotation)

    args = set(get_args(annotation))
    required_scalars = {str, int, float, bool, type(None)}
    if not required_scalars.issubset(args):
        return False

    remaining = args - required_scalars
    return (
        len(remaining) == 2
        and any(_is_json_value_list(arg, depth=depth) for arg in remaining)
        and any(_is_json_value_mapping(arg, depth=depth) for arg in remaining)
    )


def _is_metadata_dict_annotation(annotation: Any) -> bool:
    """Return whether an annotation has the shape of ``MetadataDict``."""
    origin = get_origin(annotation)
    if origin is not dict:
        return False
    args = get_args(annotation)
    return len(args) == 2 and args[0] is str and _is_json_value_annotation(args[1])


def _is_json_value_list(annotation: Any, *, depth: int) -> bool:
    """Return whether an annotation is a JSON-value list."""
    if get_origin(annotation) is not list:
        return False
    args = get_args(annotation)
    return len(args) == 1 and (
        _is_json_value_forward_ref(args[0]) or _is_json_value_annotation(args[0], depth=depth + 1)
    )


def _is_json_value_mapping(annotation: Any, *, depth: int) -> bool:
    """Return whether an annotation is a JSON-value mapping."""
    if get_origin(annotation) is not dict:
        return False
    args = get_args(annotation)
    return (
        len(args) == 2
        and args[0] is str
        and (_is_json_value_forward_ref(args[1]) or _is_json_value_annotation(args[1], depth=depth + 1))
    )


def _is_json_value_forward_ref(annotation: Any) -> bool:
    """Return whether an annotation is a recursive ``JsonValue`` reference."""
    return repr(annotation) in {"ForwardRef('JsonValue')", "'JsonValue'", "JsonValue"}
