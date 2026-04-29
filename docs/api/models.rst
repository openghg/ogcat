Models and specifications
=========================

.. py:data:: ogcat.models.JsonValue

   JSON-compatible scalar, list, or object value accepted in catalog metadata.

.. py:data:: ogcat.models.MetadataDict

   Mapping from metadata field names to :py:data:`~ogcat.models.JsonValue`.

.. autoclass:: ogcat.CatalogRecord
   :members:
   :exclude-members: catalog, time_added, id, record_type, locator, stored_abspath, stored_relpath, storage_mode, original_path, original_filename, suffixes, user_metadata, derived_metadata, naming_metadata
   :member-order: bysource

.. autoclass:: ogcat.ArtifactLocator
   :members:
   :exclude-members: kind, value, relative_path
   :member-order: bysource

.. autoclass:: ogcat.MetadataFieldDescription
   :members:
   :exclude-members: name, description, example, required, value_types
   :member-order: bysource

.. autoclass:: ogcat.CatalogSpec
   :members:
   :exclude-members: catalog_name, db_backend, db_path, files_root, default_operation, field_resolution_order, default_schema, record_schemas
   :member-order: bysource

.. autoclass:: ogcat.RecordSchema
   :members:
   :exclude-members: description, directory_template, filename_template, metadata_fields, allow_unknown_metadata
   :member-order: bysource
