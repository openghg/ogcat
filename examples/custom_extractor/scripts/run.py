"""Custom extractor hook example.

Demonstrates two ogcat hooks:

- ``TitleFromFilenameHook``: sets ``title`` in user metadata when the caller
  did not supply one, using the source filename stem.
- ``ChecksumExtractor``: computes a SHA-256 checksum of the ingested file and
  stores it in ``derived_metadata["sha256"]``.

Run from the repository root after installing ogcat:

    uv run python examples/custom_extractor/scripts/run.py
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from ogcat import Catalog, CatalogSpec, PluginRegistry
from ogcat.hooks import OperationContext


class TitleFromFilenameHook:
    """Set ``title`` from the source filename when none is supplied.

    This hook fires before metadata validation.  It only sets the title when
    the caller has not already provided one.
    """

    def before_validate_metadata(self, context: OperationContext) -> None:
        """Set title from the source filename stem when missing.

        Args:
            context: Mutable operation context.
        """
        if context.source_path is not None:
            context.user_metadata.setdefault("title", context.source_path.stem)


class ChecksumExtractor:
    """Compute a SHA-256 checksum and store it in derived metadata.

    This hook fires during derived metadata extraction.  It returns ``None``
    when no source file is available, which is silently ignored by ogcat.
    """

    def extract_metadata(self, context: OperationContext) -> dict[str, str] | None:
        """Compute SHA-256 of the source file.

        Args:
            context: Mutable operation context.

        Returns:
            A dictionary with a ``sha256`` key, or ``None`` when no source
            file is available.
        """
        if context.source_path is None or not context.source_path.is_file():
            return None
        digest = hashlib.sha256(context.source_path.read_bytes()).hexdigest()
        return {"sha256": digest}


_SAMPLE_FILES = [
    ("sensor_readings_2024.txt", {}),  # title will come from filename
    ("calibration_run.txt", {}),  # title will come from filename
    ("notes.txt", {"title": "Lab notes Jan 2024"}),  # explicit title
]


def run(catalog_root: Path, source_dir: Path) -> None:
    """Ingest sample files and print derived metadata.

    Args:
        catalog_root: Directory where the catalog will be created.
        source_dir: Directory containing the source files to ingest.
    """
    plugins = PluginRegistry([TitleFromFilenameHook(), ChecksumExtractor()])
    catalog = Catalog.create(catalog_root, CatalogSpec(catalog_name="demo"), plugins=plugins)

    for filename, meta in _SAMPLE_FILES:
        source = source_dir / filename
        source.write_text(f"content of {filename}", encoding="utf-8")
        catalog.add_file(source, metadata=meta)

    print(f"{'ID':<6}  {'Title':<35}  SHA-256 (first 12)")
    print("-" * 65)
    for rec in catalog.search():
        title = str(rec.user_metadata.get("title", ""))
        sha = str(rec.derived_metadata.get("sha256", ""))[:12]
        print(f"{rec.id!s:<6}  {title:<35}  {sha}")


def main() -> int:
    """Entry point for the custom extractor example.

    Returns:
        Exit code (0 for success).
    """
    with tempfile.TemporaryDirectory(prefix="ogcat-extractor-example-") as tmp:
        tmp_path = Path(tmp)
        catalog_root = tmp_path / "catalog"
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        print(f"Catalog root: {catalog_root}")
        print()
        run(catalog_root, source_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
