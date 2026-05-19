Capabilities
============

.. module:: ogcat.capabilities

The capability API is the public surface for #119 reader, writer, and converter
registration and lookup. It is a registry layer: it records typed
declarations and returns matching declarations with optional opaque
implementation objects. Public read handles, ``open_artifact()``, and catalog
writer-result merge are separate follow-up APIs.

Registry
--------

.. autoclass:: ogcat.CapabilityRegistry
   :members:
   :member-order: bysource
   :no-show-inheritance:

Capability declarations
-----------------------

.. autoclass:: ogcat.ArtifactCapability
   :members:
   :member-order: bysource
   :exclude-members: kind, name, namespace, version, input_claims, output_claims, required_facets, options, metadata, implementation
   :no-show-inheritance:

.. autoclass:: ogcat.CapabilityKind
   :members:
   :member-order: bysource
   :no-show-inheritance:

Lookup behavior
---------------

``CapabilityRegistry.find(...)`` returns all matching
``ArtifactCapability`` objects in registration order. ``select(...)`` returns
exactly one match or raises a lookup error.

Artifact lookup uses :class:`ogcat.ArtifactDescriptor` claims and facets. It
does not dispatch by ``CatalogRecord.record_type``. If a descriptor advertises
several interfaces, callers should request the exact desired interface through
``input_claims`` and/or ``output_claims``.

Errors
------

.. autoclass:: ogcat.CapabilityError
   :members:
   :member-order: bysource
   :no-show-inheritance:

.. autoclass:: ogcat.CapabilityRegistrationError
   :members:
   :member-order: bysource
   :no-show-inheritance:

.. autoclass:: ogcat.CapabilityLookupError
   :members:
   :member-order: bysource
   :no-show-inheritance:

.. autoclass:: ogcat.MissingCapabilityError
   :members:
   :member-order: bysource
   :no-show-inheritance:

.. autoclass:: ogcat.UnsupportedInterfaceError
   :members:
   :member-order: bysource
   :no-show-inheritance:

.. autoclass:: ogcat.AmbiguousCapabilityError
   :members:
   :member-order: bysource
   :no-show-inheritance:
