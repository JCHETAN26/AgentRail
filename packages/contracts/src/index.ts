/**
 * Typed contracts shared between the AgentRail API and its TypeScript clients.
 *
 * `src/generated/api.ts` is produced from the committed `openapi.json`, which is
 * itself produced from the FastAPI application. Do not hand-edit either file —
 * run `make contracts`.
 */

import type { components, paths } from './generated/api.js';

export type { components, paths };

export type Schemas = components['schemas'];

/** A job as returned by the API. */
export type Job = Schemas['JobResponse'];
/** Request body for `POST /api/v1/projects/{project_id}/jobs`. */
export type CreateJobRequest = Schemas['CreateJobRequest'];
export type JobListResponse = Schemas['JobListResponse'];
export type JobState = Schemas['JobState'];
export type JobKind = Schemas['JobKind'];
/** The body returned for every non-2xx response. */
export type ProblemDetail = Schemas['ProblemDetail'];
export type ErrorCode = Schemas['ErrorCode'];
export type HealthResponse = Schemas['HealthResponse'];
export type ReadinessResponse = Schemas['ReadinessResponse'];

/** Identity and tenancy. */
export type User = Schemas['UserResponse'];
export type Organisation = Schemas['OrganisationResponse'];
export type OrganisationMembership = Schemas['OrganisationMembershipResponse'];
export type Me = Schemas['MeResponse'];
export type Project = Schemas['ProjectResponse'];
export type Member = Schemas['MemberResponse'];
export type ApiKey = Schemas['ApiKeyResponse'];
export type CreatedApiKey = Schemas['CreatedApiKeyResponse'];
export type AuditEvent = Schemas['AuditEventResponse'];
export type Role = Schemas['Role'];
export type Permission = Schemas['Permission'];
export type AuthProviderInfo = Schemas['AuthProviderInfo'];

/** Agent registry. */
export type AgentDefinition = Schemas['AgentDefinitionResponse'];
export type AgentDefinitionListResponse = Schemas['AgentDefinitionListResponse'];
export type CreateAgentDefinitionRequest = Schemas['CreateAgentDefinitionRequest'];
export type AgentVersion = Schemas['AgentVersionResponse'];
export type AgentVersionListResponse = Schemas['AgentVersionListResponse'];
export type CreateAgentVersionRequest = Schemas['CreateAgentVersionRequest'];

/** Dataset ingestion and evaluation suites. */
export type Dataset = Schemas['DatasetResponse'];
export type DatasetListResponse = Schemas['DatasetListResponse'];
export type CreateDatasetRequest = Schemas['CreateDatasetRequest'];
export type DatasetVersion = Schemas['DatasetVersionResponse'];
export type CreateDatasetVersionRequest = Schemas['CreateDatasetVersionRequest'];
export type DatasetValidation = Schemas['DatasetValidationResponse'];
export type EvaluationSuite = Schemas['EvaluationSuiteResponse'];
export type CreateEvaluationSuiteRequest = Schemas['CreateEvaluationSuiteRequest'];

/** Durable evaluation execution. */
export type CreateEvaluationRunRequest = Schemas['CreateEvaluationRunRequest'];
export type EvaluationRun = Schemas['EvaluationRunResponse'];
export type EvaluationRunState = Schemas['EvaluationRunState'];
export type ComparisonReport = Schemas['ComparisonReportResponse'];
export type EvaluationResult = Schemas['EvaluationResultResponse'];
export type EvaluationResultListResponse = Schemas['EvaluationResultListResponse'];
export type EvaluatorKind = Schemas['EvaluatorKind'];
export type EvaluatorResultState = Schemas['EvaluatorResultState'];

/** Trajectory trace explorer. */
export type RunItemTrace = Schemas['RunItemTraceResponse'];
export type RunItemTraceListResponse = Schemas['RunItemTraceListResponse'];
export type RunItemState = Schemas['RunItemState'];
export type Trajectory = Schemas['TrajectoryResponse'];
export type TrajectoryState = Schemas['TrajectoryState'];
export type TrajectoryStep = Schemas['TrajectoryStepResponse'];
export type TrajectoryStepType = Schemas['TrajectoryStepType'];
export type TrajectoryStepListResponse = Schemas['TrajectoryStepListResponse'];
export type TrajectoryCheckpoint = Schemas['TrajectoryCheckpointResponse'];
export type TrajectoryCheckpointListResponse = Schemas['TrajectoryCheckpointListResponse'];

/** Terminal job states. A job in one of these will never change again. */
export const TERMINAL_JOB_STATES = ['COMPLETED', 'FAILED'] as const satisfies readonly JobState[];

export function isTerminalJobState(state: JobState): boolean {
  return (TERMINAL_JOB_STATES as readonly JobState[]).includes(state);
}

/**
 * Roles in increasing order of capability. Useful for rendering a role picker
 * and for deciding what to show, though the API is always the authority — the
 * console never makes an access decision the server has not already made.
 */
export const ROLES_BY_CAPABILITY = [
  'viewer',
  'reviewer',
  'developer',
  'admin',
  'owner',
] as const satisfies readonly Role[];

/** Header name carrying the correlation id on every request and response. */
export const CORRELATION_HEADER = 'x-correlation-id';
/** Header name for the optional idempotency key on job creation. */
export const IDEMPOTENCY_HEADER = 'Idempotency-Key';
