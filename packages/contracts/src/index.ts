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
/** Request body for `POST /api/v1/jobs`. */
export type CreateJobRequest = Schemas['CreateJobRequest'];
export type JobListResponse = Schemas['JobListResponse'];
export type JobState = Schemas['JobState'];
export type JobKind = Schemas['JobKind'];
/** The body returned for every non-2xx response. */
export type ProblemDetail = Schemas['ProblemDetail'];
export type ErrorCode = Schemas['ErrorCode'];
export type HealthResponse = Schemas['HealthResponse'];
export type ReadinessResponse = Schemas['ReadinessResponse'];

/** Terminal job states. A job in one of these will never change again. */
export const TERMINAL_JOB_STATES = ['COMPLETED', 'FAILED'] as const satisfies readonly JobState[];

export function isTerminalJobState(state: JobState): boolean {
  return (TERMINAL_JOB_STATES as readonly JobState[]).includes(state);
}

/** Header name carrying the correlation id on every request and response. */
export const CORRELATION_HEADER = 'x-correlation-id';
/** Header name for the optional idempotency key on job creation. */
export const IDEMPOTENCY_HEADER = 'Idempotency-Key';
