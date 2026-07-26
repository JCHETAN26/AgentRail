"""Dataset ingestion and evaluation-suite endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status
from pydantic import BaseModel

from agentrail_api.datasets import service
from agentrail_api.datasets.schemas import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    CreateEvaluationSuiteRequest,
    DatasetResponse,
    DatasetValidationResponse,
    DatasetVersionResponse,
    EvaluationSuiteResponse,
)
from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.identity import service as identity_service
from agentrail_core.errors import ProblemDetail
from agentrail_core.identity import Permission, authorize

router = APIRouter(prefix="/api/v1", tags=["datasets"])

ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
DatasetId = Annotated[str, Path(min_length=26, max_length=26)]
VersionId = Annotated[str, Path(min_length=26, max_length=26)]
SuiteId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Already exists."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


class DatasetListResponse(BaseModel):
    items: list[DatasetResponse]


@router.post(
    "/projects/{project_id}/datasets",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dataset",
    responses=_ERRORS,
)
async def create_dataset(
    project_id: ProjectId,
    body: CreateDatasetRequest,
    actor: ActorDep,
    session: SessionDep,
) -> DatasetResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=project.organisation_id)
    dataset = await service.create_dataset(
        session,
        actor,
        principal,
        project_id=project.id,
        name=body.name,
        description=body.description,
    )
    await session.commit()
    return DatasetResponse.model_validate(dataset)


@router.get(
    "/projects/{project_id}/datasets",
    response_model=DatasetListResponse,
    summary="List project datasets",
    responses=_ERRORS,
)
async def list_datasets(
    project_id: ProjectId, actor: ActorDep, session: SessionDep
) -> DatasetListResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    datasets = await service.list_datasets(session, principal, project_id=project.id)
    return DatasetListResponse(
        items=[DatasetResponse.model_validate(dataset) for dataset in datasets]
    )


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=DatasetVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable dataset version",
    responses=_ERRORS,
)
async def create_dataset_version(
    dataset_id: DatasetId,
    body: CreateDatasetVersionRequest,
    actor: ActorDep,
    session: SessionDep,
) -> DatasetVersionResponse:
    principal = await service.principal_for_dataset(session, actor, dataset_id)
    version = await service.create_dataset_version(
        session, actor, principal, dataset_id=dataset_id, request=body
    )
    await session.commit()
    return DatasetVersionResponse.model_validate(version)


@router.get(
    "/dataset-versions/{version_id}/validation",
    response_model=DatasetValidationResponse,
    summary="Fetch dataset-version validation details",
    responses=_ERRORS,
)
async def get_dataset_version_validation(
    version_id: VersionId, actor: ActorDep, session: SessionDep
) -> DatasetValidationResponse:
    principal = await service.principal_for_dataset_version(session, actor, version_id)
    version = await service.get_dataset_version(session, principal, version_id=version_id)
    return DatasetValidationResponse(
        version_id=version.id,
        dataset_id=version.dataset_id,
        validation_report=version.validation_report,
    )


@router.post(
    "/projects/{project_id}/evaluation-suites",
    response_model=EvaluationSuiteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an evaluation suite",
    responses=_ERRORS,
)
async def create_evaluation_suite(
    project_id: ProjectId,
    body: CreateEvaluationSuiteRequest,
    actor: ActorDep,
    session: SessionDep,
) -> EvaluationSuiteResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    authorize(principal, Permission.DATASET_MANAGE, organisation_id=project.organisation_id)
    suite = await service.create_evaluation_suite(
        session,
        actor,
        principal,
        project_id=project.id,
        name=body.name,
        dataset_version_id=body.dataset_version_id,
        description=body.description,
        evaluators=body.evaluators,
        thresholds=body.thresholds,
        fault_profiles=body.fault_profiles,
    )
    await session.commit()
    return EvaluationSuiteResponse.model_validate(suite)


@router.post(
    "/evaluation-suites/{suite_id}/freeze",
    response_model=EvaluationSuiteResponse,
    summary="Freeze an evaluation suite",
    responses=_ERRORS,
)
async def freeze_evaluation_suite(
    suite_id: SuiteId, actor: ActorDep, session: SessionDep
) -> EvaluationSuiteResponse:
    principal = await service.principal_for_suite(session, actor, suite_id)
    suite = await service.freeze_evaluation_suite(session, actor, principal, suite_id=suite_id)
    await session.commit()
    return EvaluationSuiteResponse.model_validate(suite)
