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

Inclusive date ranges
---------------------

``SearchQuery.date_between(field, start, end, format="%Y-%m-%d")`` matches an
inclusive range. Both bounds and every non-null metadata value must be strings
in the same explicit ``datetime.strptime`` format. For a monthly coordinate:

.. code-block:: python

   from ogcat import SearchQuery

   query = SearchQuery.date_between("month", "2021-01", "2021-12", format="%Y-%m")
   records = catalog.search(query=query)
   entries = catalog.members(collection.id, extractor=monthly_metadata, query=query)

Record search evaluates persisted metadata; member browsing evaluates the plain
mapping returned by the extractor. See :doc:`../concepts/locators-and-storage`
for ``monthly_metadata`` and live browsing examples. Date terms can also be
chained, for example ``SearchQuery.eq("site", "MHD").date_between(...)``.

Missing or null date fields do not match. Malformed date values or bounds raise
an error; values are not guessed or silently skipped. Comparisons preserve the
full parsed datetime, including time and timezone information supplied by the
format. An end bound denotes that exact parsed instant, not an expanded final
day or month. Reversed bounds and incompatible timezone awareness are rejected.

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
