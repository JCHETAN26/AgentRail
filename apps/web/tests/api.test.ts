import { describe, expect, it, vi } from 'vitest';

import { ApiError, createJob, getJob } from '@/lib/api';

const PROJECT_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAP';

function jsonResponse(body: unknown, init: { status?: number; headers?: HeadersInit } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
}

describe('createJob', () => {
  it('posts the request body and returns the created job', async () => {
    const job = { id: '01ARZ3NDEKTSV4RRFFQ69G5FAV', state: 'PENDING' };
    vi.mocked(fetch).mockResolvedValue(jsonResponse(job, { status: 201 }));

    const result = await createJob(PROJECT_ID, { kind: 'noop', message: 'hello' });

    expect(result).toEqual(job);
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toBe(`http://localhost:8000/api/v1/projects/${PROJECT_ID}/jobs`);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ kind: 'noop', message: 'hello' });
  });

  it('sends an idempotency key when one is supplied', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}, { status: 201 }));

    await createJob(PROJECT_ID, { kind: 'noop', message: 'hello' }, { idempotencyKey: 'key-1' });

    const headers = vi.mocked(fetch).mock.calls[0]![1]?.headers as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe('key-1');
  });

  it('omits the idempotency header when none is supplied', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}, { status: 201 }));

    await createJob(PROJECT_ID, { kind: 'noop', message: 'hello' });

    const headers = vi.mocked(fetch).mock.calls[0]![1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty('Idempotency-Key');
  });
});

describe('error handling', () => {
  it('turns a problem detail into an ApiError carrying the correlation id', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          code: 'not_found',
          message: 'Job not found',
          correlation_id: 'cid_abc123',
          details: { job_id: 'missing' },
        },
        { status: 404 },
      ),
    );

    const error = await getJob('missing').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.code).toBe('not_found');
    expect(apiError.message).toBe('Job not found');
    expect(apiError.correlationId).toBe('cid_abc123');
    expect(apiError.details).toEqual({ job_id: 'missing' });
  });

  it('falls back to the response header when the body is not a problem detail', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('<html>gateway error</html>', {
        status: 502,
        headers: { 'x-correlation-id': 'cid_from_header' },
      }),
    );

    const error = (await getJob('any').catch((caught: unknown) => caught)) as ApiError;

    expect(error.code).toBe('unexpected_response');
    expect(error.correlationId).toBe('cid_from_header');
  });

  it('reports a network failure without inventing a correlation id', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));

    const error = (await getJob('any').catch((caught: unknown) => caught)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('network_error');
    expect(error.correlationId).toBeNull();
    expect(error.status).toBe(0);
  });
});
