"""TinyDB storage for catalog records with explicit resource cleanup.

Writable repositories hold a writer lock until ``close`` is called. Closing is
idempotent and rejects subsequent reads and writes, including cached searches.
It does not commit or roll back catalog units of work.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, cast

from tinydb import Query, TinyDB
from tinydb.storages import Storage

from ogcat.models import CatalogRecord, JsonValue
from ogcat.persistence import write_json
from ogcat.search import SearchOp, SearchQuery, SearchTerm, matches_record


class TinyDbCatalogRepository:
    """TinyDB-backed catalog repository."""

    def __init__(
        self, db_path: Path, *, read_only: bool = False, create: bool = True, exclusive: bool = False
    ) -> None:
        """Open a TinyDB database, optionally without write access.

        Writable repositories hold a POSIX advisory lock until ``close``.
        Read-only repositories acquire no lock and read fresh snapshots for
        each query, including replacements published by an active writer.

        Args:
            db_path: Database file path.
            read_only: Open an existing database without creating files or
                allowing record mutations.
            create: Allow writable repositories to initialize a missing database.
            exclusive: Reject an existing database after acquiring the writer lock.
        """
        self._db_path = db_path.expanduser().resolve()
        self._read_only = read_only
        self._closed = False
        self._lock_handle: BinaryIO | None = None
        created_database = False
        try:
            if not read_only:
                if os.name != "posix":
                    raise RuntimeError("Writable catalogs require POSIX advisory file locks.")
                import fcntl

                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._lock_handle = self._db_path.with_name(self._db_path.name + ".lock").open("a+b")
                try:
                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError(
                        f"Catalog database already has a writer: {self._db_path}. "
                        "Close the existing writable catalog or use read_only=True for inspection."
                    ) from exc
                if exclusive and self._db_path.exists():
                    raise FileExistsError(self._db_path)
                if create and not self._db_path.exists():
                    write_json(self._db_path, {}, exclusive=True)
                    created_database = True
            self._db = TinyDB(self._db_path, storage=_AtomicJSONStorage, read_only=read_only)
            self._db.table(self._db.default_table_name, cache_size=0)
        except BaseException:
            if created_database:
                with suppress(OSError):
                    self._db_path.unlink(missing_ok=True)
            if self._lock_handle is not None:
                self._lock_handle.close()
            raise

    def close(self) -> None:
        """Release the writer lock; repeated calls have no effect.

        Reads and mutations after closing raise ``RuntimeError``. This method
        does not commit or roll back catalog units of work.
        """
        if not self._closed:
            try:
                self._db.close()
            finally:
                if self._lock_handle is not None:
                    self._lock_handle.close()
                self._closed = True

    def insert(self, record: CatalogRecord) -> CatalogRecord:
        """Insert a new record and return it with its TinyDB doc_id."""
        self._require_writable()
        payload = record.to_dict()
        payload.pop("id", None)
        doc_id = self._db.insert(payload)
        return replace(record, id=str(doc_id))

    def insert_many(self, records: list[CatalogRecord]) -> list[CatalogRecord]:
        """Insert multiple records and return them with their TinyDB doc_ids."""
        self._require_writable()
        if not records:
            return []
        payloads = []
        for record in records:
            payload = record.to_dict()
            payload.pop("id", None)
            payloads.append(payload)
        doc_ids = self._db.insert_multiple(payloads)
        return [replace(record, id=str(doc_id)) for record, doc_id in zip(records, doc_ids, strict=True)]

    def get(self, record_id: str) -> CatalogRecord | None:
        """Get a record by id."""
        self._require_open()
        if record_id.isdigit():
            result = self._db.get(doc_id=int(record_id))
        else:
            query = Query()
            result = self._db.get(query.id == record_id)
        if result is None:
            return None
        return self._record_from_document(result)

    def update(self, record: CatalogRecord) -> None:
        """Update an existing record."""
        self._require_writable()
        if record.id is None:
            raise ValueError("Cannot update a record without an id.")
        if record.id.isdigit():
            updated_doc_ids = self._db.update(record.to_dict(), doc_ids=[int(record.id)])
        else:
            query = Query()
            updated_doc_ids = self._db.update(record.to_dict(), query.id == record.id)
        if not updated_doc_ids:
            raise KeyError(f"Record not found: {record.id}")

    def delete(self, record_id: str) -> None:
        """Delete an existing record."""
        self._require_writable()
        if record_id.isdigit():
            removed_doc_ids = self._db.remove(doc_ids=[int(record_id)])
        else:
            query = Query()
            removed_doc_ids = self._db.remove(query.id == record_id)
        if not removed_doc_ids:
            raise KeyError(f"Record not found: {record_id}")

    def search(
        self,
        *,
        query: SearchQuery | None = None,
        where: Mapping[str, object] | None = None,
        contains: Mapping[str, object] | None = None,
        regex: Mapping[str, str] | None = None,
        match: Mapping[str, str] | None = None,
        exists: Sequence[str] | None = None,
        missing: Sequence[str] | None = None,
        ignore_case: bool = False,
        resolution_order: Sequence[str] | None = None,
    ) -> list[CatalogRecord]:
        """Search records."""
        self._require_open()
        active_query = (query or SearchQuery.all()).and_(
            SearchQuery.from_filters(
                where=where,
                contains=contains,
                regex=regex,
                match=match,
                exists=exists,
                missing=missing,
            )
        )
        records = self._native_search(active_query, ignore_case=ignore_case)
        if records is None:
            records = self.all()
        return [
            record
            for record in records
            if matches_record(
                record,
                query=active_query,
                where=None,
                contains=None,
                regex=None,
                ignore_case=ignore_case,
                resolution_order=resolution_order,
            )
        ]

    def all(self) -> list[CatalogRecord]:
        """Return all records."""
        self._require_open()
        return [self._record_from_document(item) for item in self._db.all()]

    def _require_open(self) -> None:
        """Reject access after the database handle has been released."""
        if self._closed:
            raise RuntimeError(f"Catalog database is closed: {self._db_path}")

    def _require_writable(self) -> None:
        """Reject mutations on a database opened for reading."""
        self._require_open()
        if self._read_only:
            raise PermissionError(f"Catalog database is read-only: {self._db_path}")

    def _record_from_document(self, document: Any) -> CatalogRecord:
        """Build a record, recovering the id from TinyDB doc_id when needed."""
        data = dict(cast(dict[str, JsonValue], document))
        if data.get("id") is None:
            data["id"] = str(document.doc_id)
        return CatalogRecord.from_dict(data)

    def _native_search(self, query: SearchQuery, *, ignore_case: bool) -> list[CatalogRecord] | None:
        """Run a TinyDB query for safely compilable search terms."""
        native_query = self._compile_query(query, ignore_case=ignore_case)
        if native_query is None:
            return None
        return [self._record_from_document(item) for item in self._db.search(native_query)]

    def _compile_query(self, query: SearchQuery, *, ignore_case: bool) -> Any | None:
        """Compile simple explicit search terms to TinyDB, or return None."""
        if ignore_case:
            return None

        compiled_terms: list[Any] = []
        for term in query.terms:
            compiled = self._compile_term(term)
            if compiled is None:
                return None
            compiled_terms.append(compiled)

        if not compiled_terms:
            return None

        native_query = compiled_terms[0]
        for compiled in compiled_terms[1:]:
            native_query = native_query & compiled
        return native_query

    def _compile_term(self, term: SearchTerm) -> Any | None:
        """Compile one simple explicit search term to TinyDB."""
        field = term.field.stored
        if not _is_explicit_stored_path(field):
            return None

        tinydb_field = _tinydb_field(field)
        if term.op == SearchOp.EQ:
            return tinydb_field == term.value
        if term.op == SearchOp.EXISTS:
            return tinydb_field.exists()
        if term.op == SearchOp.MISSING:
            return ~tinydb_field.exists()
        return None


def _is_explicit_stored_path(field: str) -> bool:
    """Return whether a field path maps directly to stored TinyDB document data."""
    return "." in field and field not in {"path", "locator.uri"}


def _tinydb_field(field: str) -> Any:
    """Build a dynamic TinyDB field query from a dotted path."""
    current: Any = Query()
    for part in field.split("."):
        current = current[part]
    return current


class _AtomicJSONStorage(Storage):
    """Read each snapshot from its pathname and atomically publish replacements."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        """Validate existing storage without retaining a stale file descriptor."""
        self._path = path
        self._read_only = read_only
        self.read()

    def read(self) -> dict[str, dict[str, Any]] | None:
        """Read complete JSON; accept legacy zero-byte databases as empty."""
        payload = self._path.read_text(encoding="utf-8")
        if not payload:
            return None
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(f"Catalog database must contain a JSON object: {self._path}")
        self._validate_tables(data)
        return data

    def write(self, data: dict[str, dict[str, Any]]) -> None:
        """Reject missing/read-only storage and replace it with complete JSON."""
        if self._read_only:
            raise PermissionError(f"Catalog database is read-only: {self._path}")
        if not self._path.exists():
            raise FileNotFoundError(self._path)
        self._validate_tables(data)
        write_json(self._path, data)

    def _validate_tables(self, data: dict[str, dict[str, Any]]) -> None:
        """Reject malformed documents and IDs that TinyDB would normalize or merge."""
        for table in data.values():
            if not isinstance(table, dict):
                raise ValueError(f"Catalog database tables must be JSON objects: {self._path}")
            for document_id, document in table.items():
                try:
                    canonical_id = isinstance(document_id, str) and str(int(document_id)) == document_id
                except ValueError:
                    canonical_id = False
                if not canonical_id:
                    raise ValueError(f"Catalog document IDs must be canonical integer strings: {self._path}")
                if not isinstance(document, dict):
                    raise ValueError(f"Catalog documents must be JSON objects: {self._path}")
