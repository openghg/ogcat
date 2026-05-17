Catalog API
===========

``Catalog`` is the main public Python facade. It owns user-facing argument
handling, schema selection, and delegation into internal application services.
Search results are returned as :class:`ogcat.CatalogRecordSet` by default; the
record-set helpers are documented with search.

.. autoclass:: ogcat.Catalog
   :members:
   :member-order: bysource
