"""Public package interface for ogcat."""

from ogcat.catalog import Catalog
from ogcat.models import CatalogRecord
from ogcat.spec import CatalogSpec

__all__ = ["Catalog", "CatalogRecord", "CatalogSpec"]
