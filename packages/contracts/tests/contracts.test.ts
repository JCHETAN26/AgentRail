/**
 * Guards on the published contract itself.
 *
 * These fail when the API changes shape without the change being intended —
 * complementing `pnpm check`, which only proves the generated types match the
 * snapshot, not that the snapshot is still correct.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { TERMINAL_JOB_STATES, isTerminalJobState, type JobState } from '../src/index.js';

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));

interface OpenApiDocument {
  openapi: string;
  info: { title: string; version: string };
  paths: Record<
    string,
    Record<string, { operationId?: string; responses: Record<string, unknown> }>
  >;
  components: { schemas: Record<string, { enum?: string[]; required?: string[] }> };
}

const document = JSON.parse(
  readFileSync(join(packageRoot, 'openapi.json'), 'utf8'),
) as OpenApiDocument;

describe('the OpenAPI document', () => {
  it('describes the AgentRail API', () => {
    expect(document.openapi.startsWith('3.')).toBe(true);
    expect(document.info.title).toBe('AgentRail API');
  });

  it('exposes the expected surface and nothing more', () => {
    expect(Object.keys(document.paths).sort()).toEqual([
      '/api/v1/auth/dev/session',
      '/api/v1/auth/github/authorize',
      '/api/v1/auth/github/callback',
      '/api/v1/auth/me',
      '/api/v1/auth/providers',
      '/api/v1/auth/signout',
      '/api/v1/jobs/{job_id}',
      '/api/v1/organisations',
      '/api/v1/organisations/{organisation_id}',
      '/api/v1/organisations/{organisation_id}/api-keys',
      '/api/v1/organisations/{organisation_id}/api-keys/{key_id}',
      '/api/v1/organisations/{organisation_id}/audit-events',
      '/api/v1/organisations/{organisation_id}/members',
      '/api/v1/organisations/{organisation_id}/projects',
      '/api/v1/projects/{project_id}/jobs',
      '/healthz',
      '/readyz',
    ]);
  });

  it('keeps every tenant-owned path scoped to an organisation or project', () => {
    const tenantPaths = Object.keys(document.paths).filter(
      (path) => path.startsWith('/api/v1/') && !path.startsWith('/api/v1/auth/'),
    );

    // `/api/v1/jobs/{job_id}` is the one exception: the job's own project
    // supplies the scope, and the handler authorises against it.
    const unscoped = tenantPaths.filter(
      (path) =>
        !path.includes('{organisation_id}') &&
        !path.includes('{project_id}') &&
        path !== '/api/v1/organisations' &&
        path !== '/api/v1/jobs/{job_id}',
    );

    expect(unscoped).toEqual([]);
  });

  it('gives every operation a stable operationId for client generation', () => {
    const operationIds = Object.values(document.paths)
      .flatMap((methods) => Object.values(methods))
      .map((operation) => operation.operationId);

    expect(operationIds.every((id) => typeof id === 'string' && id.length > 0)).toBe(true);
    expect(new Set(operationIds).size).toBe(operationIds.length);
  });

  it('documents the problem detail contract on every job error response', () => {
    const getJob = document.paths['/api/v1/jobs/{job_id}']?.get;

    expect(getJob).toBeDefined();
    // 401 for anonymous, 403 for another tenant's job — and deliberately no 404,
    // which would confirm that an identifier exists.
    for (const status of ['401', '403', '409', '422', '503']) {
      expect(Object.keys(getJob!.responses)).toContain(status);
    }
    expect(Object.keys(getJob!.responses)).not.toContain('404');
  });

  it('requires a correlation id on every error body', () => {
    expect(document.components.schemas.ProblemDetail?.required).toEqual(
      expect.arrayContaining(['code', 'message', 'correlation_id']),
    );
  });
});

describe('job state helpers', () => {
  const declaredStates = document.components.schemas.JobState?.enum ?? [];

  it('matches the states the API declares', () => {
    expect(declaredStates.sort()).toEqual(['COMPLETED', 'FAILED', 'PENDING', 'RUNNING']);
  });

  it('classifies every declared state without throwing', () => {
    for (const state of declaredStates) {
      expect(typeof isTerminalJobState(state as JobState)).toBe('boolean');
    }
  });

  it('treats exactly the finished states as terminal', () => {
    const terminal = declaredStates.filter((state) => isTerminalJobState(state as JobState));

    expect(terminal.sort()).toEqual([...TERMINAL_JOB_STATES].sort());
  });

  it('does not treat in-flight states as terminal', () => {
    expect(isTerminalJobState('PENDING')).toBe(false);
    expect(isTerminalJobState('RUNNING')).toBe(false);
  });
});
