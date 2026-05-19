Writers and transactions
========================

.. automodule:: ogcat.writers
   :no-members:

Writer protocol
---------------

Writer classes do not need to inherit from a concrete base class. They satisfy
the structural :class:`ogcat.hooks.ArtifactWriter` protocol by providing a
``write(request)`` method. The request carries the operation context, source,
planned target descriptor, and storage plan; the method returns an
:class:`ogcat.ArtifactWriteResult` describing the artifact facts to merge into
the catalog record.

.. autoclass:: ogcat.hooks.ArtifactWriter
   :members:
   :member-order: bysource

.. autoclass:: ogcat.ArtifactWriteRequest
   :members:
   :member-order: bysource
   :exclude-members: context, source, target, storage_plan
   :no-index:

.. autoclass:: ogcat.ArtifactWriteResult
   :members:
   :member-order: bysource
   :exclude-members: artifacts, diagnostics, provenance
   :no-index:

The operation runner creates a planned ``data`` artifact descriptor before
calling the writer. Writer results may enrich that descriptor with claims,
facets, relationship metadata, or state. If a writer returns only auxiliary
artifact descriptors, the planned data descriptor is preserved. Diagnostics and
provenance are normalized to JSON-compatible shapes and included only in the
artifact-write audit event.

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

   Callable that writes from an ``OperationSource`` to a target path. It may
   return ``None``, ``MetadataDict`` derived metadata, an
   ``ArtifactDescriptor``, or an ``ArtifactWriteResult``; the adapter converts
   all supported return shapes into an ``ArtifactWriteResult``.

.. py:attribute:: FunctionArtifactWriter.target_kind

   Target artifact kind, either ``"file"`` or ``"directory"``.

.. py:attribute:: FunctionArtifactWriter.source_kind

   Optional required source kind. When set, ``write`` rejects sources whose
   ``OperationSource.kind`` does not match.

Methods
~~~~~~~

.. automethod:: ogcat.writers.FunctionArtifactWriter.write

CopyArtifactWriter
------------------

.. autoclass:: ogcat.writers.CopyArtifactWriter
   :members:
   :member-order: bysource
   :exclude-members: source_kind, target_kind
   :no-show-inheritance:

MoveArtifactWriter
------------------

.. autoclass:: ogcat.writers.MoveArtifactWriter
   :members:
   :member-order: bysource
   :exclude-members: source_kind, target_kind
   :no-show-inheritance:

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

UnzipSingleFileArtifactWriter
-----------------------------

.. autoclass:: ogcat.writers.UnzipSingleFileArtifactWriter
   :exclude-members: source_kind, target_kind, write_mode
   :member-order: bysource
   :no-members:
   :no-show-inheritance:

Attributes
~~~~~~~~~~

.. py:attribute:: UnzipSingleFileArtifactWriter.member_name

   Optional archive member to extract. When omitted, the archive must contain
   exactly one non-directory member.

.. py:attribute:: UnzipSingleFileArtifactWriter.source_kind

   Required source kind for zip extraction. Defaults to ``"zip_file"``.

Methods
~~~~~~~

.. automethod:: ogcat.writers.UnzipSingleFileArtifactWriter.write

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
