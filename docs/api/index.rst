API reference
=============

Catalog
-------

.. autoclass:: ogcat.Catalog
   :members:

Models
------

.. autoclass:: ogcat.CatalogRecord
   :members:

.. autoclass:: ogcat.ArtifactLocator
   :members:

.. autoclass:: ogcat.MetadataFieldDescription
   :members:

Specification
-------------

.. autoclass:: ogcat.CatalogSpec
   :members:

.. autoclass:: ogcat.RecordSchema
   :members:

Hooks
-----

.. automodule:: ogcat.hooks
   :members:
   :undoc-members: False

Plugins
-------

.. autoclass:: ogcat.PluginRegistry
   :members:

Search
------

.. autoclass:: ogcat.SearchQuery
   :members:

.. autoclass:: ogcat.SearchTerm
   :members:

Validation
----------

.. autofunction:: ogcat.validate_metadata
.. autofunction:: ogcat.validate_record
.. autofunction:: ogcat.validate_schema
.. autofunction:: ogcat.validate_spec
.. autoclass:: ogcat.ValidationReport
   :members:
.. autoclass:: ogcat.ValidationIssue
   :members:

Writers
-------

.. automodule:: ogcat.writers
   :members:
   :undoc-members: False

Transactions
------------

.. autoclass:: ogcat.UnitOfWork
   :members:

.. autoclass:: ogcat.OperationState
   :members:

Record sets
-----------

.. autoclass:: ogcat.CatalogRecordSet
   :members:
