API reference
=============

The public API pages document interfaces intended for normal users. Some public
pages, especially hooks, writers, validation, and models, also describe the
stable extension points plugin authors can use. The internals page documents
package-internal boundaries used by maintainers and architecture work; those
names are not a public stability promise unless a public page says so
explicitly.

Public API
----------

.. toctree::
   :maxdepth: 1

   catalog
   audit
   models
   search
   storage
   replicas
   hooks
   validation
   writers-transactions

Internals
---------

.. toctree::
   :maxdepth: 1

   internals
