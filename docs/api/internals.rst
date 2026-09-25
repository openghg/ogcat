Internal architecture reference
===============================

These modules describe maintainer-facing seams below the public Python API.
They are documented so refactors and plugin-boundary discussions have concrete
interfaces to point at. They should not be treated as stable public API unless
a public concept page or release note promotes a specific name.

Application orchestration
-------------------------

``Catalog`` delegates add-operation setup into an internal application service,
which builds a request for the concrete add coordinator. Record lifecycle
operations have their own concrete coordinator; there is no generic runner
interface.

.. automodule:: ogcat.catalog_application
   :members:
   :member-order: bysource

.. automodule:: ogcat.operation_runner
   :members:
   :member-order: bysource

Materialisation and storage planning
------------------------------------

``StoragePlan`` is the concrete decision about where the primary artifact
belongs and how it is written. Materialisation helpers derive and validate that
plan from locators and optional writers; they do not define another plan layer.

.. automodule:: ogcat.materialization
   :members:
   :member-order: bysource

.. automodule:: ogcat.storage_planning
   :members:
   :member-order: bysource

Secondary artifacts
-------------------

Secondary artifacts are ordered follow-up operations, such as the required
template-link symlink created after a UUID-primary file record is staged.

.. automodule:: ogcat.secondary_artifacts
   :members:
   :member-order: bysource

.. automodule:: ogcat.template_replicas
   :members:
   :member-order: bysource

.. automodule:: ogcat.replica_context
   :members:
   :member-order: bysource

.. automodule:: ogcat.replica_links
   :members:
   :member-order: bysource

Repository boundary
-------------------

Repository implementations own persistence. Catalog and operation services
depend on the protocol rather than a concrete backend.

.. automodule:: ogcat.repository
   :members:
   :member-order: bysource
