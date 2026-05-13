"""Structured validation for catalog schemas and records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter, ValidationError

from ogcat.models import CatalogRecord, normalize_metadata
from ogcat.spec import CatalogSpec, RecordSchema

_TYPE_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "bool": TypeAdapter(StrictBool),
    "boolean": TypeAdapter(StrictBool),
    "date": TypeAdapter(date),
    "datetime": TypeAdapter(datetime),
    "dict": TypeAdapter(dict[str, Any]),
    "float": TypeAdapter(StrictFloat),
    "int": TypeAdapter(StrictInt),
    "integer": TypeAdapter(StrictInt),
    "list[str]": TypeAdapter(list[StrictStr]),
    "list[string]": TypeAdapter(list[StrictStr]),
    "number": TypeAdapter(StrictInt | StrictFloat),
    "object": TypeAdapter(dict[str, Any]),
    "str": TypeAdapter(StrictStr),
    "string": TypeAdapter(StrictStr),
}


@dataclass(slots=True)
class ValidationIssue:
    """A single validation finding.

    Args:
        path: Dot-separated path to the invalid value.
        message: Human-readable validation message.
        severity: Issue severity, usually ``"error"`` or ``"warning"``.
        code: Stable machine-readable issue code.
        hint: Optional user-facing remediation hint.
    """

    path: str
    message: str
    severity: str = "error"
    code: str = "validation.error"
    hint: str | None = None


@dataclass(slots=True)
class ValidationReport:
    """Structured validation result.

    Args:
        issues: Validation issues collected during a check.
    """

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether the report has no error issues."""
        return not self.errors

    @property
    def errors(self) -> list[ValidationIssue]:
        """Return validation errors."""
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Return validation warnings."""
        return [issue for issue in self.issues if issue.severity == "warning"]

    def add(
        self,
        *,
        path: str,
        message: str,
        severity: str = "error",
        code: str = "validation.error",
        hint: str | None = None,
    ) -> None:
        """Add one issue to the report."""
        self.issues.append(
            ValidationIssue(
                path=path,
                message=message,
                severity=severity,
                code=code,
                hint=hint,
            )
        )

    def extend(self, other: ValidationReport) -> None:
        """Add issues from another report."""
        self.issues.extend(other.issues)

    def raise_for_errors(self) -> None:
        """Raise a Python exception if the report contains errors.

        Raises:
            TypeError: If metadata itself is not a mapping.
            ValueError: If one or more metadata values fail validation.
        """
        errors = self.errors
        if not errors:
            return
        first = errors[0]
        if first.code in {"metadata.not_object", "metadata.not_json"}:
            raise TypeError(first.message)
        raise ValueError(_summarize_errors(errors))


def validate_schema(
    schema: RecordSchema,
    *,
    schema_name: str = "default",
    base_path: str | None = None,
) -> ValidationReport:
    """Validate a record schema's validation hints.

    Args:
        schema: Schema to validate.
        schema_name: Name used in issue paths and messages.
        base_path: Optional issue path prefix for the schema.

    Returns:
        Structured validation report.
    """
    report = ValidationReport()
    schema_path = base_path or f"record_schemas.{schema_name}"
    for field_index, field_description in enumerate(schema.metadata_fields):
        for type_index, type_label in enumerate(field_description.value_types):
            normalized = _normalize_type_label(type_label)
            if normalized in _TYPE_ADAPTERS:
                continue
            report.add(
                path=(f"{schema_path}.metadata_fields.{field_index}.type.{type_index}"),
                message=(f"Unsupported metadata type label for schema {schema_name}: {type_label}"),
                code="schema.unsupported_type",
                hint=(
                    "Use one of: bool/boolean, date, datetime, dict/object, float/number, "
                    "int/integer, list[str]/list[string], str/string."
                ),
            )
    return report


def validate_spec(spec: CatalogSpec) -> ValidationReport:
    """Validate schema validation hints across a catalog spec."""
    report = ValidationReport()
    for schema_name, schema in spec.record_schemas.items():
        report.extend(
            validate_schema(
                schema,
                schema_name=schema_name,
                base_path=f"record_schemas.{schema_name}",
            )
        )
    return report


def validate_metadata(
    metadata: object,
    schema: RecordSchema,
    *,
    strict: bool = False,
    schema_name: str = "default",
) -> ValidationReport:
    """Validate metadata against a record schema.

    Args:
        metadata: Metadata mapping to validate.
        schema: Schema describing required fields and value types.
        strict: Whether to apply strict schema options.
        schema_name: Human-readable schema name for messages.

    Returns:
        Structured validation report.
    """
    report = ValidationReport()
    if not isinstance(metadata, Mapping):
        report.add(
            path="metadata",
            message=(
                f"Metadata for schema {schema_name} must be a dictionary, got {type(metadata).__name__}"
            ),
            code="metadata.not_object",
        )
        return report
    try:
        metadata = normalize_metadata(metadata)
    except (TypeError, ValueError) as exc:
        report.add(
            path="metadata",
            message=str(exc),
            code="metadata.not_json",
            hint="Use JSON-compatible metadata values or values ogcat can normalize.",
        )
        return report

    fields_by_name = {
        field_description.name: field_description for field_description in schema.metadata_fields
    }
    missing = [field_name for field_name in schema.required_field_names() if field_name not in metadata]
    if missing:
        joined = ", ".join(missing)
        report.add(
            path="metadata",
            message=f"Missing required metadata for schema {schema_name}: {joined}",
            code="metadata.required",
            hint="Add the missing required metadata field(s).",
        )

    if strict and not schema.allow_unknown_metadata:
        unknown = sorted(str(field_name) for field_name in metadata if field_name not in fields_by_name)
        for field_name in unknown:
            report.add(
                path=f"metadata.{field_name}",
                message=f"Unknown metadata field for schema {schema_name}: {field_name}",
                code="metadata.unknown",
                hint="Remove the field or add it to the schema metadata_fields.",
            )

    for field_name, field_description in fields_by_name.items():
        if field_name not in metadata:
            continue
        report.extend(
            _validate_field_value(
                value=metadata[field_name],
                field_name=field_name,
                type_labels=field_description.value_types,
            )
        )
    return report


def validate_record(
    record: CatalogRecord,
    spec: CatalogSpec | None = None,
    *,
    strict: bool = False,
) -> ValidationReport:
    """Validate a catalog record dataclass.

    Args:
        record: Runtime catalog record to validate.
        spec: Optional catalog spec used for metadata schema validation.
        strict: Whether to apply strict schema options.

    Returns:
        Structured validation report.
    """
    report = ValidationReport()
    if not record.catalog:
        report.add(path="catalog", message="Catalog record is missing catalog name.", code="record.catalog")
    if not record.time_added:
        report.add(
            path="time_added",
            message="Catalog record is missing time_added.",
            code="record.time_added",
        )
    if spec is None:
        return report

    if record.record_type in spec.record_schemas:
        schema_name = record.record_type
        schema = spec.get_schema(record.record_type)
    else:
        schema_name = spec.default_record_schema
        schema = spec.get_schema()
    metadata_report = validate_metadata(
        record.user_metadata,
        schema,
        strict=strict,
        schema_name=schema_name,
    )
    report.extend(metadata_report)
    return report


def _validate_field_value(
    *,
    value: object,
    field_name: str,
    type_labels: list[str],
) -> ValidationReport:
    """Validate a value against any supported type label."""
    report = ValidationReport()
    validation_errors: list[ValidationError] = []
    supported_type_labels: list[str] = []
    for type_label in type_labels:
        adapter = _TYPE_ADAPTERS.get(_normalize_type_label(type_label))
        if adapter is None:
            continue
        supported_type_labels.append(type_label)
        try:
            adapter.validate_python(value)
        except ValidationError as exc:
            validation_errors.append(exc)
            continue
        return report
    if supported_type_labels and validation_errors:
        expected = " or ".join(supported_type_labels)
        report.add(
            path=f"metadata.{field_name}",
            message=_pydantic_message(
                field_name=field_name,
                type_label=expected,
                error=validation_errors[0],
            ),
            code="metadata.type",
            hint=f"Provide a value compatible with {expected}.",
        )
    return report


def _normalize_type_label(type_label: str) -> str:
    """Normalize type labels used in schema metadata descriptions."""
    return type_label.strip().lower().replace(" ", "")


def _pydantic_message(*, field_name: str, type_label: str, error: ValidationError) -> str:
    """Build a concise message from a Pydantic validation error."""
    detail = error.errors()[0]["msg"] if error.errors() else "Invalid value"
    return f"Invalid metadata value for {field_name}: expected {type_label}. {detail}"


def _summarize_errors(errors: list[ValidationIssue]) -> str:
    """Summarize validation errors as a single exception message."""
    if len(errors) == 1:
        return errors[0].message
    return "; ".join(error.message for error in errors)
