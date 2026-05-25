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

.. autoclass:: ogcat.ArtifactWriteRequest
   :members:
   :member-order: bysource
   :exclude-members: context, source, target, storage_plan

.. autoclass:: ogcat.ArtifactMaterializer
   :members:
   :member-order: bysource
   :no-index:

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
