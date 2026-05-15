"""Reference locator coercion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ogcat.models import ArtifactLocator


@dataclass(frozen=True, slots=True)
class ReferenceLocatorPlan:
    """Resolved reference locator and optional local path metadata.

    Args:
        locator: Locator to record for the referenced artifact.
        local_path: Resolved local path when the reference is path-backed.
    """

    locator: ArtifactLocator
    local_path: Path | None


def plan_reference_locator(
    reference: str | Path | ArtifactLocator | None,
    *,
    uri: str | None,
    urlpath: str | None,
) -> ReferenceLocatorPlan:
    """Resolve a reference input into a locator and optional local path.

    Args:
        reference: Local path, URI-like string, or explicit artifact locator.
        uri: Explicit URI reference. Mutually exclusive with ``reference`` and
            ``urlpath``.
        urlpath: Explicit fsspec-style URL-path reference. Mutually exclusive
            with ``reference`` and ``uri``.

    Returns:
        Reference locator plan.

    Raises:
        ValueError: If callers do not pass exactly one reference input.
    """
    supplied = [value is not None for value in (reference, uri, urlpath)]
    if sum(supplied) != 1:
        raise ValueError("Pass exactly one of reference, uri, or urlpath.")
    if uri is not None:
        return ReferenceLocatorPlan(ArtifactLocator(kind="uri", value=str(uri)), None)
    if urlpath is not None:
        return ReferenceLocatorPlan(ArtifactLocator.from_urlpath(str(urlpath)), None)
    assert reference is not None
    if isinstance(reference, ArtifactLocator):
        path = reference.as_path()
        if path is None:
            return ReferenceLocatorPlan(reference, None)
        resolved_path = path.expanduser().resolve()
        locator = ArtifactLocator.from_path(resolved_path, relative_path=reference.relative_path)
        return ReferenceLocatorPlan(locator, resolved_path)
    if isinstance(reference, str):
        if "://" in reference:
            return ReferenceLocatorPlan(ArtifactLocator(kind="uri", value=reference), None)
        path = Path(reference).expanduser().resolve()
        return ReferenceLocatorPlan(ArtifactLocator.from_path(path), path)
    path = Path(reference).expanduser().resolve()
    return ReferenceLocatorPlan(ArtifactLocator.from_path(path), path)


__all__ = [
    "ReferenceLocatorPlan",
    "plan_reference_locator",
]
