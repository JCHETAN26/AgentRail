/**
 * Typed client for the AgentRail API.
 *
 * Every request and response shape comes from `@agentrail/contracts`, which is
 * generated from the API's OpenAPI document. A contract change that this file
 * does not account for is a type error, not a runtime surprise.
 */

import type { CreateJobRequest, Job, JobListResponse, ProblemDetail } from '@agentrail/contracts';
import { CORRELATION_HEADER, IDEMPOTENCY_HEADER } from '@agentrail/contracts';

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, '') ?? 'http://localhost:8000';

/**
 * A failed API call, carrying the correlation id so a user can quote it and an
 * engineer can find the exact request in the logs.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      status: number;
      code: string;
      correlationId: string | null;
      details?: Record<string, unknown>;
    },
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.correlationId = options.correlationId;
    this.details = options.details ?? {};
  }
}

function isProblemDetail(value: unknown): value is ProblemDetail {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { code?: unknown }).code === 'string' &&
    typeof (value as { message?: unknown }).message === 'string'
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
    });
  } catch (cause) {
    // A network-level failure never produced a correlation id.
    throw new ApiError('Could not reach the AgentRail API.', {
      status: 0,
      code: 'network_error',
      correlationId: null,
      details: { cause: String(cause) },
    });
  }

  const correlationId = response.headers.get(CORRELATION_HEADER);

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isProblemDetail(body)) {
      throw new ApiError(body.message, {
        status: response.status,
        code: body.code,
        correlationId: body.correlation_id ?? correlationId,
        details: body.details ?? {},
      });
    }
    throw new ApiError(`Request failed with status ${response.status}.`, {
      status: response.status,
      code: 'unexpected_response',
      correlationId,
    });
  }

  return (await response.json()) as T;
}

export async function createJob(
  body: CreateJobRequest,
  options: { idempotencyKey?: string } = {},
): Promise<Job> {
  const headers: Record<string, string> = {};
  if (options.idempotencyKey !== undefined) {
    headers[IDEMPOTENCY_HEADER] = options.idempotencyKey;
  }
  return request<Job>('/api/v1/jobs', {
    method: 'POST',
    body: JSON.stringify(body),
    headers,
  });
}

export async function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

export async function listJobs(limit = 10): Promise<JobListResponse> {
  return request<JobListResponse>(`/api/v1/jobs?limit=${limit}`);
}
