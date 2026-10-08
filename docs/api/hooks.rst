Hooks and plugins
=================

.. automodule:: ogcat.hooks
   :no-members:

Operation context
-----------------

Hook methods receive an ``OperationContext`` unless their signature documents
additional arguments such as a validation report or exception. The context is
the main coordination object for metadata mutation, locator planning, rollback
registration, and source information.

.. autoclass:: ogcat.OperationContext
   :members:
   :member-order: bysource
   :exclude-members: catalog_root, operation_id, operation_type, record_type, user_metadata, derived_metadata, planned_locators, register_rollback, source, storage_mode, original_path, original_filename, suffixes, warnings

.. autoclass:: ogcat.OperationSource
   :members:
   :member-order: bysource
   :exclude-members: kind, path, descriptor, metadata, payload

.. autoclass:: ogcat.HookWarning
   :members:
   :member-order: bysource
   :exclude-members: hook_name, message, code

Plugin registry
---------------

.. autoclass:: ogcat.PluginRegistry
   :members:
   :member-order: bysource

Hook protocols
--------------

Reusable path extractors
~~~~~~~~~~~~~~~~~~~~~~~~

``MetadataExtractorHook`` adapts a callable receiving a local ``Path`` and
returning a metadata mapping or ``None`` to ingest. It runs during
``before_validate_metadata`` and updates derived metadata from the local source
path. The same callable can be passed as ``extractor=`` to ``Catalog.members()``
or ``CollectionEntry.members()``. Using the ``monthly_metadata`` function from
:doc:`../concepts/locators-and-storage`:

.. code-block:: python

   from ogcat import Catalog, MetadataExtractorHook

   with Catalog.open("./my-catalog", hooks=[MetadataExtractorHook(monthly_metadata)]) as catalog:
       record = catalog.add_file("incoming/month_202101.nc", operation="move")
       assert record.derived_metadata["month"] == "2021-01"

The adapter reads the source before copying or moving it into managed storage,
preserving source-filename conventions even when UUID storage changes the
destination basename. Operations without a local source path are skipped; use
a custom lifecycle hook to extract from memory-backed outputs or materialized
destinations. Ingest persists the derived metadata; live browsing uses it only
for returned entries and never dispatches this hook. Callers remain responsible
for keeping extractors read-only.

.. autoclass:: ogcat.MetadataExtractorHook
   :members:
   :member-order: bysource

Lifecycle protocols
~~~~~~~~~~~~~~~~~~~

.. autoclass:: ogcat.hooks.BeforeValidateMetadataHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.AfterValidateMetadataHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.ResolveArtifactLocatorHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.BeforeRecordWriteHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.AfterRecordWriteHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.ExtractMetadataHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.BeforeCommitHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.AfterCommitHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.ErrorHook
   :members:
   :member-order: bysource

.. autoclass:: ogcat.hooks.RollbackHook
   :members:
   :member-order: bysource

Dispatch
--------

.. autoclass:: ogcat.HookManager
   :members:
   :member-order: bysource
