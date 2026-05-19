ogcat
=====

``ogcat`` is a lightweight artifact catalog for local files.

It provides a self-describing on-disk catalog layout, a small Python API, and
a CLI for creating catalogs, ingesting files, recording metadata, and
searching records.

.. toctree::
   :maxdepth: 1
   :caption: Getting started

   install
   getting-started

.. toctree::
   :maxdepth: 1
   :caption: Concepts

   concepts/catalog-records
   concepts/locators-and-storage
   concepts/metadata-and-validation
   concepts/transactions-and-logging
   concepts/hooks-and-plugins

.. toctree::
   :maxdepth: 1
   :caption: Tutorials

   tutorials/basic-catalog
   tutorials/verification-games-recipes
   tutorials/intermediate
   tutorials/advanced-real-world
   tutorials/artifact-workflows

.. toctree::
   :maxdepth: 1
   :caption: Reference

   api/index
   glossary
   examples-data-policy
   cli

.. toctree::
   :maxdepth: 1
   :caption: Development notes

   architecture
   roadmap
   ideas
   adr/index
   design-note-record-schemas
   design-note-artifact-locators
   design-note-artifact-descriptors
   design-note-artifact-claims-and-facets
   design-note-hooks-plugins
   design-note-virtual-artifact-filesystem-research
   ogcat_long_term_plan
