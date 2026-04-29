Writers and transactions
========================

.. automodule:: ogcat.writers
   :no-members:

Writer protocol
---------------

Writer classes do not need to inherit from a concrete base class. They satisfy
the structural :class:`ogcat.hooks.ArtifactWriter` protocol by providing a
``write(context, source, target)`` method.

.. autoclass:: ogcat.hooks.ArtifactWriter
   :members:
   :member-order: bysource

Writer helpers
--------------

.. autofunction:: ogcat.writers.memory_source

.. autofunction:: ogcat.writers.path_source

.. autofunction:: ogcat.writers.source_writer

.. autofunction:: ogcat.writers.memory_writer

.. autofunction:: ogcat.writers.path_writer

FunctionArtifactWriter
----------------------

.. autoclass:: ogcat.writers.FunctionArtifactWriter
   :exclude-members: write_function, target_kind, source_kind
   :member-order: bysource
   :no-members:
   :no-show-inheritance:

Attributes
~~~~~~~~~~

.. py:attribute:: FunctionArtifactWriter.write_function

   Callable that writes from an ``OperationSource`` to a target path and may
   return ``MetadataDict`` derived metadata.

.. py:attribute:: FunctionArtifactWriter.target_kind

   Target artifact kind, either ``"file"`` or ``"directory"``.

.. py:attribute:: FunctionArtifactWriter.source_kind

   Optional required source kind. When set, ``write`` rejects sources whose
   ``OperationSource.kind`` does not match.

Methods
~~~~~~~

.. automethod:: ogcat.writers.FunctionArtifactWriter.write

UnzipArtifactWriter
-------------------

.. autoclass:: ogcat.writers.UnzipArtifactWriter
   :exclude-members: source_kind
   :member-order: bysource
   :no-members:
   :no-show-inheritance:

Attributes
~~~~~~~~~~

.. py:attribute:: UnzipArtifactWriter.source_kind

   Required source kind for zip extraction. Defaults to ``"zip_file"``.

Methods
~~~~~~~

.. automethod:: ogcat.writers.UnzipArtifactWriter.write

Transactions
------------

.. automodule:: ogcat.transactions
   :no-members:

.. autoclass:: ogcat.UnitOfWork
   :members:
   :member-order: bysource
   :exclude-members: repository, operation_id, state, rollback_errors
   :no-show-inheritance:

.. autoclass:: ogcat.OperationState
   :members:
   :member-order: bysource
