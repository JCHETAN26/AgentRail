"""Dataset and evaluation-suite use cases."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.datasets.schemas import CreateDatasetVersionRequest, DatasetInputFormat
from agentrail_api.identity.service import record_audit, slugify
from agentrail_core.errors import ConflictError, ForbiddenError, ValidationFailedError
from agentrail_core.faults import FaultProfileError, parse_fault_profiles
from agentrail_core.identity import (
    Dataset,
    DatasetVersion,
    EvaluationSuite,
    Permission,
    Principal,
    Project,
    authorize,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.tribunal import TribunalConfigError, validate_tribunal_config

REQUIRED_FIELDS = ("id", "input", "expected")
MAX_REJECTIONS = 50
MAX_DATASET_UPLOAD_BYTES = 1024 * 1024
_ALLOWED_EXTENSIONS: dict[DatasetInputFormat, tuple[str, ...]] = {
    "jsonl": (".jsonl", ".ndjson"),
    "csv": (".csv",),
}
_ALLOWED_CONTROL_CHARACTERS = {"\n", "\r", "\t"}
_ACTIVE_CONTENT_MARKERS = ("<script", "</script", "javascript:", "data:text/html")


@dataclass(frozen=True, slots=True)
class DatasetValidation:
    item_count: int
    rejected_count: int
    partition_counts: dict[str, int]
    record_schema: dict[str, Any]
    validation_report: dict[str, Any]
    #: The records that passed validation, in file order. Kept so a run item can
    #: carry the record it evaluates; previously these were validated and
    #: dropped, leaving nothing to show an agent.
    records: list[dict[str, Any]] = dataclasses.field(default_factory=list)


def dataset_content_digest(request: CreateDatasetVersionRequest) -> str:
    return hashlib.sha256(request.content.encode("utf-8")).hexdigest()


def validate_dataset_upload_envelope(request: CreateDatasetVersionRequest) -> dict[str, Any]:
    """Validate upload metadata before parsing records.

    The API currently accepts dataset bytes as a JSON string. These checks are
    the security boundary around that upload: size, declared type/filename, and
    a lightweight active-content scan. They deliberately do not reject ordinary
    prompt-injection text inside synthetic incidents; the Tribunal evidence
    sandbox owns that threat.
    """
    content_bytes = request.content.encode("utf-8")
    if len(content_bytes) > MAX_DATASET_UPLOAD_BYTES:
        raise ValidationFailedError(
            "Dataset upload validation failed.",
            details={
                "reason": "content_too_large",
                "max_bytes": MAX_DATASET_UPLOAD_BYTES,
                "actual_bytes": len(content_bytes),
            },
        )

    filename = (request.source_filename or "").strip()
    if filename:
        _validate_source_filename(filename, request.input_format)

    _scan_dataset_content(request.content)
    return {
        "max_bytes": MAX_DATASET_UPLOAD_BYTES,
        "actual_bytes": len(content_bytes),
        "input_format": request.input_format,
        "source_filename": filename or None,
        "content_scan": "passed",
    }


def _validate_source_filename(filename: str, input_format: DatasetInputFormat) -> None:
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValidationFailedError(
            "Dataset upload validation failed.",
            details={"reason": "invalid_source_filename"},
        )
    if any(ord(character) < 32 for character in filename):
        raise ValidationFailedError(
            "Dataset upload validation failed.",
            details={"reason": "invalid_source_filename"},
        )
    lowered = filename.lower()
    allowed = _ALLOWED_EXTENSIONS[input_format]
    if not lowered.endswith(allowed):
        raise ValidationFailedError(
            "Dataset upload validation failed.",
            details={
                "reason": "source_filename_type_mismatch",
                "input_format": input_format,
                "allowed_extensions": list(allowed),
            },
        )


def _scan_dataset_content(content: str) -> None:
    for character in content:
        if character in _ALLOWED_CONTROL_CHARACTERS:
            continue
        if ord(character) < 32 or character == "\ufffd":
            raise ValidationFailedError(
                "Dataset upload validation failed.",
                details={"reason": "content_scan_failed", "finding": "binary_or_control_data"},
            )

    lowered = content.lower()
    for marker in _ACTIVE_CONTENT_MARKERS:
        if marker in lowered:
            raise ValidationFailedError(
                "Dataset upload validation failed.",
                details={"reason": "content_scan_failed", "finding": "active_content_marker"},
            )


def _normalise_storage_uri(
    dataset_id: str, digest: str, request: CreateDatasetVersionRequest
) -> str:
    if request.storage_uri:
        return request.storage_uri
    suffix = "jsonl" if request.input_format == "jsonl" else "csv"
    return f"s3://agentrail-datasets/{dataset_id}/{digest}.{suffix}"


def _parse_jsonl(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            rejected.append({"line": index, "message": exc.msg})
            continue
        if not isinstance(parsed, dict):
            rejected.append({"line": index, "message": "Record must be a JSON object."})
            continue
        accepted.append(parsed)
    return accepted, rejected


def _coerce_csv_cell(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _parse_csv(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        return [], [{"line": 1, "message": "CSV header row is required."}]
    missing = sorted(set(REQUIRED_FIELDS) - set(reader.fieldnames))
    if missing:
        return [], [{"line": 1, "message": "CSV is missing required columns.", "fields": missing}]

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in reader:
        accepted.append({key: _coerce_csv_cell(value or "") for key, value in row.items()})
    return accepted, rejected


def _validate_records(
    records: list[dict[str, Any]], initial_rejections: list[dict[str, Any]]
) -> DatasetValidation:
    rejected = list(initial_rejections)
    valid: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    partitions: Counter[str] = Counter()

    for index, record in enumerate(records, start=1):
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            rejected.append(
                {
                    "record": index,
                    "message": "Record is missing required fields.",
                    "fields": missing,
                }
            )
            continue
        record_id = record["id"]
        if not isinstance(record_id, str) or not record_id.strip():
            rejected.append({"record": index, "message": "Record id must be a non-empty string."})
            continue
        if record_id in identifiers:
            rejected.append(
                {"record": index, "message": "Record id is duplicated.", "id": record_id}
            )
            continue
        identifiers.add(record_id)
        partition = record.get("partition", "default")
        if not isinstance(partition, str) or not partition.strip():
            rejected.append({"record": index, "message": "Partition must be a non-empty string."})
            continue
        record["partition"] = partition
        partitions[partition] += 1
        valid.append(record)

    report = {
        "accepted_count": len(valid),
        "rejected_count": len(rejected),
        "rejections": rejected[:MAX_REJECTIONS],
    }
    if len(rejected) > MAX_REJECTIONS:
        report["truncated"] = True

    return DatasetValidation(
        records=valid,
        item_count=len(valid),
        rejected_count=len(rejected),
        partition_counts=dict(sorted(partitions.items())),
        record_schema={
            "required": list(REQUIRED_FIELDS),
            "optional": ["partition", "metadata"],
            "partition_field": "partition",
        },
        validation_report=report,
    )


def validate_dataset_content(input_format: DatasetInputFormat, content: str) -> DatasetValidation:
    if input_format == "jsonl":
        records, rejections = _parse_jsonl(content)
    else:
        records, rejections = _parse_csv(content)
    validation = _validate_records(records, rejections)
    if validation.item_count == 0:
        validation.validation_report["message"] = "At least one valid dataset record is required."
    return validation


async def create_dataset(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    project_id: str,
    name: str,
    description: str | None,
) -> Dataset:
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=principal.organisation_id)

    slug = slugify(name)
    duplicate = await session.scalar(
        select(Dataset.id).where(Dataset.project_id == project_id, Dataset.slug == slug)
    )
    if duplicate is not None:
        raise ConflictError("A dataset with that name already exists.", details={"slug": slug})

    dataset = Dataset(
        id=new_sortable_id(),
        project_id=project_id,
        name=name.strip(),
        slug=slug,
        description=description.strip() if description else None,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(dataset)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "A dataset with that name already exists.", details={"slug": slug}
        ) from exc

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="dataset.created",
        target_type="dataset",
        target_id=dataset.id,
        context={"project_id": project_id, "slug": slug},
    )
    return dataset


async def list_datasets(
    session: AsyncSession, principal: Principal, *, project_id: str
) -> list[Dataset]:
    authorize(principal, Permission.DATASET_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.id)
    )
    return list(rows.all())


async def principal_for_dataset(session: AsyncSession, actor: Actor, dataset_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(Dataset, Dataset.project_id == Project.id)
        .where(Dataset.id == dataset_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_dataset_version(
    session: AsyncSession, actor: Actor, version_id: str
) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(Dataset, Dataset.project_id == Project.id)
        .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
        .where(DatasetVersion.id == version_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_suite(session: AsyncSession, actor: Actor, suite_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(EvaluationSuite, EvaluationSuite.project_id == Project.id)
        .where(EvaluationSuite.id == suite_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def get_dataset(
    session: AsyncSession,
    principal: Principal,
    *,
    dataset_id: str,
    project_id: str | None = None,
    lock_for_update: bool = False,
) -> Dataset:
    authorize(principal, Permission.DATASET_READ, organisation_id=principal.organisation_id)
    clauses: list[Any] = [
        Dataset.id == dataset_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if project_id is not None:
        clauses.append(Dataset.project_id == project_id)
    statement = select(Dataset).join(Project, Project.id == Dataset.project_id).where(*clauses)
    if lock_for_update:
        statement = statement.with_for_update(of=Dataset)
    dataset = await session.scalar(statement)
    if dataset is None:
        raise ForbiddenError()
    return dataset


async def create_dataset_version(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    dataset_id: str,
    request: CreateDatasetVersionRequest,
) -> DatasetVersion:
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=principal.organisation_id)
    dataset = await get_dataset(session, principal, dataset_id=dataset_id, lock_for_update=True)
    upload_validation = validate_dataset_upload_envelope(request)
    validation = validate_dataset_content(request.input_format, request.content)
    if validation.rejected_count > 0 or validation.item_count == 0:
        raise ValidationFailedError(
            "Dataset validation failed.", details=validation.validation_report
        )
    validation.validation_report["upload_validation"] = upload_validation

    digest = dataset_content_digest(request)
    duplicate_digest = await session.scalar(
        select(DatasetVersion.id).where(
            DatasetVersion.dataset_id == dataset.id, DatasetVersion.content_digest == digest
        )
    )
    if duplicate_digest is not None:
        raise ConflictError(
            "That dataset version content already exists.", details={"content_digest": digest}
        )

    current_version = await session.scalar(
        select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset.id)
    )
    version_number = int(current_version or 0) + 1
    version = DatasetVersion(
        id=new_sortable_id(),
        dataset_id=dataset.id,
        version=version_number,
        content_digest=digest,
        storage_uri=_normalise_storage_uri(dataset.id, digest, request),
        input_format=request.input_format,
        source_filename=request.source_filename,
        records=validation.records,
        record_schema=validation.record_schema,
        validation_report=validation.validation_report,
        item_count=validation.item_count,
        rejected_count=validation.rejected_count,
        partition_counts=validation.partition_counts,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "That dataset version already exists.", details={"dataset_id": dataset_id}
        ) from exc

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="dataset_version.created",
        target_type="dataset_version",
        target_id=version.id,
        context={"dataset_id": dataset.id, "version": version_number, "content_digest": digest},
    )
    return version


async def get_dataset_version(
    session: AsyncSession, principal: Principal, *, version_id: str
) -> DatasetVersion:
    authorize(principal, Permission.DATASET_READ, organisation_id=principal.organisation_id)
    row = await session.execute(
        select(DatasetVersion, Project)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .join(Project, Project.id == Dataset.project_id)
        .where(DatasetVersion.id == version_id)
    )
    result = row.one_or_none()
    if result is None:
        raise ForbiddenError()
    version = cast(DatasetVersion, result[0])
    project = cast(Project, result[1])
    authorize(principal, Permission.DATASET_READ, organisation_id=project.organisation_id)
    return version


async def create_evaluation_suite(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    project_id: str,
    name: str,
    dataset_version_id: str,
    description: str | None,
    evaluators: list[dict[str, Any]],
    thresholds: dict[str, Any],
    fault_profiles: list[dict[str, Any]],
) -> EvaluationSuite:
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=principal.organisation_id)
    dataset_version = await get_dataset_version(session, principal, version_id=dataset_version_id)

    # Reject an unexecutable fault profile here, at the boundary, rather than
    # when the worker parses it. A profile that only fails at run time strands
    # a leased item and takes the consuming worker down with it.
    try:
        parse_fault_profiles(fault_profiles)
    except FaultProfileError as invalid:
        raise ValidationFailedError(
            "A fault profile cannot be executed.",
            details={"index": invalid.index, "reason": invalid.reason},
        ) from invalid
    try:
        tribunal_config = validate_tribunal_config(thresholds)
    except TribunalConfigError as invalid:
        raise ValidationFailedError(
            "Tribunal configuration is invalid.", details={"reason": str(invalid)}
        ) from invalid

    row = await session.execute(
        select(Dataset.project_id, DatasetVersion.partition_counts, DatasetVersion.item_count)
        .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
        .where(DatasetVersion.id == dataset_version.id, Dataset.project_id == project_id)
    )
    dataset_scope = row.one_or_none()
    if dataset_scope is None:
        raise ForbiddenError()

    slug = slugify(name)
    duplicate = await session.scalar(
        select(EvaluationSuite.id).where(
            EvaluationSuite.project_id == project_id, EvaluationSuite.slug == slug
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "An evaluation suite with that name already exists.", details={"slug": slug}
        )

    preview = {
        "dataset_version_id": dataset_version_id,
        "item_count": int(dataset_scope.item_count),
        "partition_counts": dataset_scope.partition_counts,
        "evaluator_count": len(evaluators),
        "thresholds": thresholds,
        "fault_profile_count": len(fault_profiles),
        "tribunal_enabled": tribunal_config["enabled"],
        "tribunal_mode": tribunal_config["mode"],
        "tribunal_prompt_version": tribunal_config["prompt_version"],
        "tribunal_model_provider": tribunal_config["model_provider"],
        "tribunal_model": tribunal_config["model"],
    }
    suite = EvaluationSuite(
        id=new_sortable_id(),
        project_id=project_id,
        dataset_version_id=dataset_version_id,
        name=name.strip(),
        slug=slug,
        description=description.strip() if description else None,
        evaluators=evaluators,
        thresholds=thresholds,
        fault_profiles=fault_profiles,
        preview=preview,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(suite)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "An evaluation suite with that name already exists.", details={"slug": slug}
        ) from exc

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="evaluation_suite.created",
        target_type="evaluation_suite",
        target_id=suite.id,
        context={"project_id": project_id, "dataset_version_id": dataset_version_id, "slug": slug},
    )
    return suite


async def freeze_evaluation_suite(
    session: AsyncSession, actor: Actor, principal: Principal, *, suite_id: str
) -> EvaluationSuite:
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=principal.organisation_id)
    row = await session.execute(
        select(EvaluationSuite, Project)
        .join(Project, Project.id == EvaluationSuite.project_id)
        .where(EvaluationSuite.id == suite_id)
        .with_for_update(of=EvaluationSuite)
    )
    result = row.one_or_none()
    if result is None:
        raise ForbiddenError()
    suite = cast(EvaluationSuite, result[0])
    project = cast(Project, result[1])
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=project.organisation_id)
    if suite.frozen_at is not None:
        return suite

    suite.frozen_at = datetime.now(UTC)
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="evaluation_suite.frozen",
        target_type="evaluation_suite",
        target_id=suite.id,
        context={"dataset_version_id": suite.dataset_version_id},
    )
    await session.flush()
    return suite
