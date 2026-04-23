"""Catalog specification objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
from typing import Literal


@dataclass(slots=True)
class CatalogSpec:
    """Self-describing configuration for a catalog."""

    catalog_name: str
    db_backend: str = "tinydb"
    db_path: str = "db.json"
    files_root: str = "files"
    directory_template: str = (
        "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/"
        "{version|unversioned}/{flux_type|misc}"
    )
    filename_template: str = (
        "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}"
        "{original_suffix}"
    )
    default_operation: Literal["copy", "move"] = "copy"
    field_resolution_order: list[str] = field(
        default_factory=lambda: ["top_level", "user_metadata", "derived_metadata"]
    )

    def to_dict(self) -> dict[str, object]:
        """Convert the spec to a serialisable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CatalogSpec":
        """Build a spec from a dictionary."""
        return cls(**data)

    def write(self, path: Path) -> None:
        """Write the spec JSON to disk."""
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "CatalogSpec":
        """Read a spec JSON file from disk."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)
