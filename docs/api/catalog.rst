Catalog API
===========

``Catalog`` is the main public Python facade. It owns user-facing argument
handling, schema selection, and delegation into internal application services.
Search results are returned as :class:`ogcat.CatalogRecordSet` by default; the
record-set helpers are documented with search.

Record deletion is trash-style by default: ``Catalog.delete()`` tombstones a
record and hides it from normal search, ``Catalog.restore()`` makes the record
active again, and ``Catalog.purge()`` permanently removes a tombstoned record
after removing managed catalog-local artifacts. Purge is best-effort across
artifacts; incomplete cleanup raises ``PurgeIncompleteError`` after retaining
the tombstone with purge outcome metadata.

.. autoclass:: ogcat.Catalog
   :members:
   :member-order: bysource
