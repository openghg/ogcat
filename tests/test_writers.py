from __future__ import annotations

import shutil
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

import ogcat.writers as writers_module
from ogcat import (
    ArtifactDescriptor,
    ArtifactFacet,
    ArtifactLocator,
    ArtifactWriteRequest,
    ArtifactWriteResult,
    Catalog,
    CatalogSpec,
    CopyArtifactWriter,
    InterfaceClaim,
    MoveArtifactWriter,
    OperationSource,
    PluginRegistry,
    RecordSchema,
    RepresentationClaim,
    UnzipArtifactWriter,
    UnzipSingleFileArtifactWriter,
    memory_source,
    memory_writer,
    path_source,
    path_writer,
    plan_storage,
    source_writer,
)
from ogcat.hooks import OperationContext
from ogcat.models import MetadataDict


def test_memory_writer_writes_file_and_persists_metadata(tmp_path: Path) -> None:
    """Memory writer helper writes file data and records returned metadata."""

    def write_text(data: object, target: Path) -> MetadataDict:
        text = str(data)
        target.write_text(text, encoding="utf-8")
        return {"byte_count": target.stat().st_size}

    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    target = tmp_path / "outputs" / "generated.txt"

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source("hello", kind="text", descriptor="inline text"),
        artifact_writer=memory_writer(write_text, target_kind="file", source_kind="text"),
    )

    assert target.read_text(encoding="utf-8") == "hello"
    assert record.derived_metadata["byte_count"] == 5


def test_path_writer_writes_directory_and_rolls_back_on_hook_failure(tmp_path: Path) -> None:
    """Path writer helper directory targets are cleaned up on later hook failure."""

    def copy_tree(source: Path, target: Path) -> MetadataDict:
        (target / "copied.txt").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return {"copied": True}

    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop after write")

    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "directory-target"
    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.raises(RuntimeError, match="stop after write"):
        catalog.add_artifact(
            record_type="processed_directory",
            locator=ArtifactLocator.path(target),
            source=path_source(source, kind="local_text"),
            artifact_writer=path_writer(copy_tree, target_kind="directory", source_kind="local_text"),
        )

    assert not target.exists()
    assert catalog.repository.all() == []


def test_locator_materializer_fallback_plan_uses_materializer_intent(tmp_path: Path) -> None:
    """Plain locator-plus-materializer calls expose storage intent in context."""
    seen: list[tuple[str, str]] = []

    def copy_tree(source: Path, target: Path) -> MetadataDict:
        (target / "copied.txt").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return {}

    class InspectPlanHook:
        def extract_metadata(self, context: OperationContext) -> None:
            assert context.storage_plan is not None
            seen.append((context.storage_plan.target_kind, context.storage_plan.write_mode))

    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "directory-target"
    registry = PluginRegistry([InspectPlanHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    catalog.add_artifact(
        record_type="processed_directory",
        locator=ArtifactLocator.path(target),
        source=path_source(source, kind="local_text"),
        artifact_writer=path_writer(copy_tree, target_kind="directory", source_kind="local_text"),
    )

    assert seen == [("directory", "write")]


def test_source_writer_receives_full_operation_source(tmp_path: Path) -> None:
    """Source writer helper receives the full OperationSource object."""

    def write_source(source: OperationSource, target: Path) -> MetadataDict:
        assert source.path is None
        assert source.payload == {"value": 3}
        target.write_text(str(source.metadata["label"]), encoding="utf-8")
        return {"source_kind": source.kind}

    target = tmp_path / "source-writer.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source(
            {"value": 3},
            kind="structured",
            descriptor="structured payload",
            metadata={"label": "example"},
        ),
        artifact_writer=source_writer(write_source, target_kind="file", source_kind="structured"),
    )

    assert target.read_text(encoding="utf-8") == "example"
    assert record.derived_metadata["source_kind"] == "structured"


def test_memory_writer_adapts_none_return_to_base_result(tmp_path: Path) -> None:
    """Convenience materializer helpers can wrap one-off functions that return None."""

    def write_text(data: object, target: Path) -> None:
        target.write_text(str(data), encoding="utf-8")

    target = tmp_path / "none-result.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source("hello"),
        artifact_writer=memory_writer(write_text, target_kind="file"),
    )

    assert target.read_text(encoding="utf-8") == "hello"
    assert [artifact.id for artifact in record.artifacts] == ["data"]
    assert record.artifacts[0].locator == ArtifactLocator.path(target)


def test_memory_writer_adapts_descriptor_return(tmp_path: Path) -> None:
    """Convenience materializer helpers can wrap functions that return descriptor facts."""

    def write_text(data: object, target: Path) -> ArtifactDescriptor:
        target.write_text(str(data), encoding="utf-8")
        return ArtifactDescriptor(
            id="data",
            role="data_artifact",
            claims=[InterfaceClaim("text")],
        )

    target = tmp_path / "descriptor-result.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source("hello"),
        artifact_writer=memory_writer(write_text, target_kind="file"),
    )

    assert record.artifacts[0].claims == [
        {
            "kind": "interface",
            "name": "text",
            "namespace": "ogcat.core",
            "version": "1",
            "evidence": "declared",
            "confidence": "declared",
            "metadata": {},
        }
    ]


def test_materializer_result_merges_claims_facets_diagnostics_and_provenance(tmp_path: Path) -> None:
    """Structured materializer results enrich the data descriptor and audit event."""

    class TextWriter:
        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(str(request.source.payload), encoding="utf-8")
            request.context.rollback(
                lambda path=target_path: path.unlink(missing_ok=True),
                description="remove text result",
            )
            return ArtifactWriteResult.for_data_artifact(
                relationship={"kind": "generated_from", "source": request.source.kind},
                claims=[
                    InterfaceClaim("text", evidence="inferred", metadata={"encoding": "unknown"}),
                    InterfaceClaim("text", evidence="validated", metadata={"encoding": "utf-8"}),
                ],
                facets=[
                    ArtifactFacet(kind="encoding", name="charset", metadata={"encoding": "ascii"}),
                    ArtifactFacet(kind="encoding", name="charset", metadata={"encoding": "utf-8"}),
                ],
                diagnostics={"target_path": target_path, "checks": {"written", "encoded"}},
                provenance={"source_descriptor": request.source.descriptor},
            )

    target = tmp_path / "structured.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source("hello", kind="memory_text", descriptor="inline text"),
        artifact_writer=TextWriter(),
    )

    data = record.artifacts[0]
    assert data.relationship == {"kind": "generated_from", "source": "memory_text"}
    assert data.claims == [
        {
            "kind": "interface",
            "name": "text",
            "namespace": "ogcat.core",
            "version": "1",
            "evidence": "validated",
            "confidence": "validated",
            "metadata": {"encoding": "utf-8"},
        }
    ]
    assert data.facets == [
        {
            "kind": "encoding",
            "name": "charset",
            "namespace": "ogcat.core",
            "version": "1",
            "evidence": "declared",
            "confidence": "declared",
            "metadata": {"encoding": "utf-8"},
        }
    ]
    write_event = [
        event
        for event in catalog.audit_events(event_type="write")
        if event.details.get("write_phase") == "artifact-write"
    ][0]
    assert write_event.details["materializer_diagnostics"] == {
        "target_path": str(target),
        "checks": ["encoded", "written"],
    }
    assert write_event.details["materializer_provenance"] == {"source_descriptor": "inline text"}


def test_materializer_result_with_only_auxiliary_artifact_preserves_data_descriptor(tmp_path: Path) -> None:
    """Auxiliary-only results keep the planned base data descriptor."""

    class PreviewWriter:
        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.write_text("data", encoding="utf-8")
            preview = target_path.with_suffix(".preview.txt")
            preview.write_text("preview", encoding="utf-8")

            def remove_outputs() -> None:
                target_path.unlink(missing_ok=True)
                preview.unlink(missing_ok=True)

            request.context.rollback(
                remove_outputs,
                description="remove data and preview",
            )
            return ArtifactWriteResult.from_artifact(
                ArtifactDescriptor(
                    id="preview",
                    role="preview",
                    locator=ArtifactLocator.path(preview),
                    relationship={"kind": "preview_of", "target_artifact_id": "data"},
                    claims=[InterfaceClaim("text")],
                )
            )

    target = tmp_path / "data.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="generated_text",
        locator=ArtifactLocator.path(target),
        source=memory_source("payload"),
        artifact_writer=PreviewWriter(),
    )

    assert [artifact.id for artifact in record.artifacts] == ["data", "preview"]
    assert record.artifacts[0].locator == ArtifactLocator.path(target)
    assert record.artifacts[1].relationship["target_artifact_id"] == "data"


def test_materializer_result_locator_conflict_rolls_back_written_artifact(tmp_path: Path) -> None:
    """A conflicting data locator is rejected after materializer cleanup is registered."""

    class ConflictingLocatorWriter:
        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.write_text("data", encoding="utf-8")
            request.context.rollback(
                lambda path=target_path: path.unlink(missing_ok=True),
                description="remove conflicting data",
            )
            return ArtifactWriteResult.for_data_artifact(
                locator=ArtifactLocator.path(target_path.with_name("other.txt")),
            )

    target = tmp_path / "data.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="locator must match the planned locator"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            source=memory_source("payload"),
            artifact_writer=ConflictingLocatorWriter(),
        )

    assert not target.exists()
    assert catalog.repository.all() == []


def test_materializer_result_duplicate_auxiliary_id_rolls_back_written_artifact(tmp_path: Path) -> None:
    """Duplicate returned artifact ids fail before commit and trigger rollback."""

    class DuplicateAuxiliaryWriter:
        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.write_text("data", encoding="utf-8")
            request.context.rollback(
                lambda path=target_path: path.unlink(missing_ok=True),
                description="remove duplicate auxiliary data",
            )
            return ArtifactWriteResult(
                artifacts=(
                    ArtifactDescriptor(id="preview", role="preview"),
                    ArtifactDescriptor(id="preview", role="preview"),
                )
            )

    target = tmp_path / "data.txt"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="duplicate artifact id"):
        catalog.add_artifact(
            record_type="generated_text",
            locator=ArtifactLocator.path(target),
            source=memory_source("payload"),
            artifact_writer=DuplicateAuxiliaryWriter(),
        )

    assert not target.exists()
    assert catalog.repository.all() == []


def test_materializer_result_can_describe_directory_collection_shape(tmp_path: Path) -> None:
    """Materializers can attach collection claims/facets without a collection API."""

    class CollectionDirectoryWriter:
        target_kind = "directory"

        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.mkdir(parents=True, exist_ok=False)
            (target_path / "co2_202401.nc").write_text("not netcdf", encoding="utf-8")
            request.context.rollback(
                lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
                description="remove collection directory",
            )
            return ArtifactWriteResult.for_data_artifact(
                claims=[
                    RepresentationClaim("directory"),
                    InterfaceClaim("collection"),
                ],
                facets=[
                    ArtifactFacet(
                        kind="collection",
                        name="members",
                        metadata={
                            "pattern": "*.nc",
                            "member_format": "netcdf",
                            "member_suffixes": [".nc"],
                            "reader_hint": "xarray.open_mfdataset",
                        },
                    )
                ],
            )

    target = tmp_path / "collection"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="netcdf_collection",
        locator=ArtifactLocator.path(target),
        source=memory_source("payload"),
        artifact_writer=CollectionDirectoryWriter(),
    )

    first_claim = record.artifacts[0].claims[0]
    second_claim = record.artifacts[0].claims[1]
    collection_facet = record.artifacts[0].facets[0]
    assert isinstance(first_claim, Mapping)
    assert isinstance(second_claim, Mapping)
    assert isinstance(collection_facet, Mapping)
    facet_metadata = collection_facet["metadata"]
    assert isinstance(facet_metadata, Mapping)
    assert first_claim["name"] == "directory"
    assert second_claim["name"] == "collection"
    assert facet_metadata["pattern"] == "*.nc"


def test_unzip_artifact_writer_extracts_metadata(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.txt", "alpha")
        archive.writestr("nested/b.txt", "bravo")

    target = tmp_path / "unzipped"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="zip_directory",
        locator=ArtifactLocator.path(target),
        source=path_source(archive_path, kind="zip_file"),
        artifact_writer=UnzipArtifactWriter(),
    )

    assert (target / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (target / "nested" / "b.txt").read_text(encoding="utf-8") == "bravo"
    assert record.derived_metadata["extracted_file_count"] == 2
    assert record.derived_metadata["extracted_names"] == ["a.txt", "nested/b.txt"]


def test_unzip_artifact_writer_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "nope")

    target = tmp_path / "unzipped"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="zip member escapes target directory"):
        catalog.add_artifact(
            record_type="zip_directory",
            locator=ArtifactLocator.path(target),
            source=path_source(archive_path, kind="zip_file"),
            artifact_writer=UnzipArtifactWriter(),
        )

    assert not target.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_unzip_single_file_artifact_writer_extracts_only_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data.nc", "netcdf")

    target = tmp_path / "stored.nc"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    record = catalog.add_artifact(
        record_type="netcdf_file",
        locator=ArtifactLocator.path(target),
        source=path_source(archive_path, kind="zip_file"),
        artifact_writer=UnzipSingleFileArtifactWriter(),
    )

    assert target.read_text(encoding="utf-8") == "netcdf"
    assert record.derived_metadata["extracted_file_count"] == 1
    assert record.derived_metadata["extracted_name"] == "data.nc"
    assert record.derived_metadata["extracted_size"] == len("netcdf")


def test_unzip_single_file_artifact_writer_storage_intent_is_not_configurable() -> None:
    with pytest.raises(TypeError, match="target_kind"):
        UnzipSingleFileArtifactWriter(target_kind="directory")  # type: ignore[call-arg]


def test_unzip_single_file_artifact_writer_can_select_named_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "skip")
        archive.writestr("nested/data.nc", "netcdf")

    target = tmp_path / "stored.nc"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    catalog.add_artifact(
        record_type="netcdf_file",
        locator=ArtifactLocator.path(target),
        source=path_source(archive_path, kind="zip_file"),
        artifact_writer=UnzipSingleFileArtifactWriter(member_name="nested/data.nc"),
    )

    assert target.read_text(encoding="utf-8") == "netcdf"


def test_unzip_single_file_artifact_writer_requires_one_member_by_default(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.nc", "alpha")
        archive.writestr("b.nc", "bravo")

    target = tmp_path / "stored.nc"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="expected exactly one file"):
        catalog.add_artifact(
            record_type="netcdf_file",
            locator=ArtifactLocator.path(target),
            source=path_source(archive_path, kind="zip_file"),
            artifact_writer=UnzipSingleFileArtifactWriter(),
        )

    assert not target.exists()
    assert catalog.repository.all() == []


def test_unzip_single_file_artifact_writer_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.nc", "nope")

    target = tmp_path / "stored.nc"
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    with pytest.raises(ValueError, match="zip member escapes target file"):
        catalog.add_artifact(
            record_type="netcdf_file",
            locator=ArtifactLocator.path(target),
            source=path_source(archive_path, kind="zip_file"),
            artifact_writer=UnzipSingleFileArtifactWriter(),
        )

    assert not target.exists()
    assert not (tmp_path / "escape.nc").exists()


def test_non_reference_storage_plan_requires_materializer(tmp_path: Path) -> None:
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))
    target = tmp_path / "catalog" / "files" / "generated.txt"
    plan = plan_storage(
        ArtifactLocator.from_path(target, relative_path="files/generated.txt"),
        write_mode="write",
        ogcat_owned=True,
    )

    with pytest.raises(ValueError, match="requires an artifact_writer"):
        catalog.add_artifact(record_type="generated_text", storage_plan=plan)

    assert not target.exists()
    assert catalog.repository.all() == []


def test_copy_artifact_writer_materialises_file_and_rolls_back(tmp_path: Path) -> None:
    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop after copy")

    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "catalog" / "files" / "copied.txt"
    plan = plan_storage(
        ArtifactLocator.from_path(target, relative_path="files/copied.txt"),
        write_mode="copy",
        ogcat_owned=True,
    )
    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.raises(RuntimeError, match="stop after copy"):
        catalog.add_artifact(
            record_type="copied_file",
            storage_plan=plan,
            source=path_source(source, kind="local_file"),
            artifact_writer=CopyArtifactWriter(),
        )

    assert source.exists()
    assert not target.exists()
    assert catalog.repository.all() == []


def test_move_artifact_writer_restores_source_on_rollback(tmp_path: Path) -> None:
    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop after move")

    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "catalog" / "files" / "moved.txt"
    plan = plan_storage(
        ArtifactLocator.from_path(target, relative_path="files/moved.txt"),
        write_mode="move",
        ogcat_owned=True,
    )
    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.raises(RuntimeError, match="stop after move"):
        catalog.add_artifact(
            record_type="moved_file",
            storage_plan=plan,
            source=path_source(source, kind="local_file"),
            artifact_writer=MoveArtifactWriter(),
        )

    assert source.read_text(encoding="utf-8") == "payload"
    assert not target.exists()
    assert catalog.repository.all() == []


def test_move_artifact_writer_rejects_urlpath_before_adapter_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"))

    def fail_adapter_lookup(target: ArtifactLocator):
        raise AssertionError(f"adapter lookup should not run for {target.kind!r}")

    monkeypatch.setattr(writers_module, "adapter_for_locator", fail_adapter_lookup)

    with pytest.raises(ValueError, match="path-backed target"):
        catalog.add_artifact(
            record_type="moved_file",
            locator=ArtifactLocator.from_urlpath("memory://catalog/files/moved.txt"),
            source=path_source(source, kind="local_file"),
            artifact_writer=MoveArtifactWriter(),
        )

    assert source.read_text(encoding="utf-8") == "payload"
    assert catalog.repository.all() == []


def test_materializer_receives_storage_plan_and_rolls_back_directory_target(tmp_path: Path) -> None:
    class FailingHook:
        def before_record_write(self, context: OperationContext) -> None:
            raise RuntimeError("stop after planned write")

    class PlanAwareDirectoryWriter:
        def write(self, request: ArtifactWriteRequest) -> ArtifactWriteResult:
            assert request.context.storage_plan is not None
            assert request.context.storage_plan.target_kind == "directory"
            assert request.context.storage_plan.locator == request.locator
            target_path = request.locator.as_path()
            assert target_path is not None
            target_path.mkdir(parents=True, exist_ok=False)
            (target_path / "payload.txt").write_text(str(request.source.payload), encoding="utf-8")
            request.context.rollback(
                lambda path=target_path: shutil.rmtree(path, ignore_errors=True),
                description="remove planned directory",
            )
            return ArtifactWriteResult.from_artifact(request.target)

    target = tmp_path / "catalog" / "files" / "stores" / "example.zarr"
    plan = plan_storage(
        ArtifactLocator.from_path(target, relative_path="files/stores/example.zarr"),
        target_kind="directory",
        write_mode="write",
        ogcat_owned=True,
        adapter="local",
    )
    registry = PluginRegistry([FailingHook()])
    catalog = Catalog.create(tmp_path / "catalog", CatalogSpec(catalog_name="artifacts"), plugins=registry)

    with pytest.raises(RuntimeError, match="stop after planned write"):
        catalog.add_artifact(
            record_type="zarr_store",
            storage_plan=plan,
            source=memory_source("zarr-ish", kind="memory"),
            artifact_writer=PlanAwareDirectoryWriter(),
        )

    assert not target.exists()


def test_plan_artifact_storage_can_allocate_zarr_directory_name(tmp_path: Path) -> None:
    """Storage planning can allocate directory-like Zarr store names."""
    catalog = Catalog.create(
        tmp_path / "catalog",
        CatalogSpec(
            catalog_name="artifacts",
            record_schemas={
                "zarr_store": RecordSchema(
                    directory_template="{species}/{domain}",
                    filename_template="{title}.zarr",
                )
            },
        ),
    )

    plan = catalog.plan_artifact_storage(
        record_type="zarr_store",
        metadata={"species": "CO2", "domain": "EUROPE", "title": "my_store"},
        target_kind="directory",
        write_mode="write",
        primary_location="template",
    )

    assert plan.target_kind == "directory"
    assert Path(plan.locator.value).name == "my_store.zarr"
    assert Path(plan.locator.value).parent.name == "EUROPE"
    assert not Path(plan.locator.value).exists()
