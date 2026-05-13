Search and record sets
======================

Contains semantics
------------------

``Catalog.search(contains=...)`` keeps the comparison rules simple and
type-directed:

* strings use substring containment;
* lists and other stored sequences use membership matching, and a list expected
  value requires every expected item to be present;
* mappings match an expected mapping as a subset of key/value pairs;
* scalar values fall back to equality.

Search filter arguments such as ``where``, ``contains``, ``regex``, and
``match`` must be mappings from field name to expected value. ``exists`` and
``missing`` must be sequences of field names, not bare strings.

.. autoclass:: ogcat.SearchQuery
   :members:
   :member-order: bysource

.. autoclass:: ogcat.SearchTerm
   :members:
   :member-order: bysource

.. autoclass:: ogcat.FieldPath
   :members:
   :member-order: bysource

.. autoclass:: ogcat.CatalogRecordSet
   :members:
   :member-order: bysource
