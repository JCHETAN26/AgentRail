'use client';

import type {
  TribunalAgentRole,
  TribunalArgument,
  TribunalFinding,
  TribunalSession,
} from '@agentrail/contracts';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import { ApiError, createTribunalSession, getTribunalSession } from '@/lib/api';

const RUN_ID_LENGTH = 26;
const ROLES: readonly TribunalAgentRole[] = [
  'prosecutor',
  'defender',
  'auditor',
  'economist',
  'historian',
  'judge',
];

export function TribunalPanel() {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [draftRunId, setDraftRunId] = useState('');
  const [runId, setRunId] = useState<string | null>(null);
  const trimmed = draftRunId.trim();
  const canSubmit = trimmed.length === RUN_ID_LENGTH;

  const tribunal = useQuery({
    queryKey: ['tribunal', runId],
    queryFn: () => getTribunalSession(runId as string),
    enabled: runId !== null,
    retry: false,
  });

  const create = useMutation({
    mutationFn: (targetRunId: string) => createTribunalSession(targetRunId),
    onSuccess: async (created) => {
      setRunId(created.run_id);
      setDraftRunId(created.run_id);
      await queryClient.setQueryData(['tribunal', created.run_id], created);
    },
  });

  function inspect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) {
      create.reset();
      setRunId(trimmed);
    }
  }

  function runTribunal() {
    if (canSubmit) {
      create.mutate(trimmed);
    }
  }

  const result = create.data ?? tribunal.data ?? null;
  const error = create.error ?? tribunal.error;
  const missingTribunal =
    error instanceof ApiError && error.status === 404 && runId !== null && !create.isPending;

  return (
    <section className="panel tribunal" aria-labelledby="tribunal-heading">
      <div className="tribunal__header">
        <div>
          <h2 id="tribunal-heading">Safety Tribunal</h2>
          <p className="form__hint">
            Inspect the six-agent blackboard for any evaluation run, or create the deterministic
            verdict from its stored evidence.
          </p>
        </div>
      </div>

      <form className="tribunal__lookup" onSubmit={inspect}>
        <label className="form__label" htmlFor={inputId}>
          Evaluation run id
        </label>
        <div className="tribunal__lookup-row">
          <input
            id={inputId}
            className="form__input"
            value={draftRunId}
            maxLength={RUN_ID_LENGTH}
            onChange={(event) => setDraftRunId(event.target.value)}
            placeholder="01..."
            aria-describedby={`${inputId}-hint`}
          />
          <button className="button button--quiet" type="submit" disabled={!canSubmit}>
            Inspect
          </button>
          <button className="button" type="button" disabled={!canSubmit} onClick={runTribunal}>
            {create.isPending ? 'Running...' : 'Run tribunal'}
          </button>
        </div>
        <p id={`${inputId}-hint`} className="form__hint">
          Run ids are 26-character sortable identifiers.
        </p>
      </form>

      {tribunal.isPending && runId !== null ? (
        <p className="loading" role="status">
          Loading Tribunal...
        </p>
      ) : null}

      {missingTribunal ? (
        <div className="notice" role="status" data-testid="tribunal-missing">
          <p>No Tribunal exists for this run yet.</p>
        </div>
      ) : error && !missingTribunal ? (
        <TribunalError error={error} />
      ) : null}

      {result ? <TribunalResult tribunal={result} /> : null}
    </section>
  );
}

function TribunalResult({ tribunal }: { tribunal: TribunalSession }) {
  const summary = tribunal.summary ?? {};
  const findingsByRole = new Map<TribunalAgentRole, TribunalFinding[]>();
  for (const role of ROLES) {
    findingsByRole.set(role, []);
  }
  for (const finding of tribunal.findings) {
    findingsByRole.get(finding.agent_role)?.push(finding);
  }

  return (
    <div className="tribunal__result" data-testid="tribunal-result">
      <div className="tribunal__verdict">
        <span className={`badge badge--tribunal-${tribunal.outcome}`}>{tribunal.outcome}</span>
        <p>{tribunal.verdict.primary_reason}</p>
      </div>

      <dl className="result__grid">
        <div>
          <dt>Agents</dt>
          <dd>{summary.agent_count ?? ROLES.length}</dd>
        </div>
        <div>
          <dt>Findings</dt>
          <dd>{summary.finding_count ?? tribunal.findings.length}</dd>
        </div>
        <div>
          <dt>Blockers</dt>
          <dd>{summary.blocker_count ?? 0}</dd>
        </div>
        <div>
          <dt>Run</dt>
          <dd>
            <code>{tribunal.run_id}</code>
          </dd>
        </div>
      </dl>

      <div className="tribunal__roles">
        {ROLES.map((role) => (
          <RoleFindings key={role} role={role} findings={findingsByRole.get(role) ?? []} />
        ))}
      </div>

      <Arguments arguments={tribunal.arguments} />

      <ol className="tribunal__timeline" aria-label="Tribunal blackboard timeline">
        {tribunal.blackboard.map((entry) => (
          <li key={entry.id}>
            <span className="tribunal__sequence">{entry.sequence}</span>
            <div>
              <strong>{entry.agent_role}</strong>
              <span className="tribunal__meta">
                {entry.round} / {entry.entry_type}
              </span>
              <p>{messageFromPayload(entry.payload ?? {}) ?? entry.title}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function messageFromPayload(payload: Record<string, unknown>): string | null {
  return typeof payload.message === 'string' ? payload.message : null;
}

function RoleFindings({
  role,
  findings,
}: {
  role: TribunalAgentRole;
  findings: TribunalFinding[];
}) {
  return (
    <section className="tribunal__role" aria-label={`${role} findings`}>
      <header>
        <strong>{role}</strong>
        <span>{findings.length}</span>
      </header>
      {findings.length === 0 ? (
        <p className="form__hint">No findings recorded.</p>
      ) : (
        <ul>
          {findings.map((finding) => (
            <li key={finding.id}>
              <span className={`badge badge--tribunal-${finding.severity}`}>
                {finding.severity}
              </span>
              <p>{finding.message}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Arguments({ arguments: tribunalArguments }: { arguments: TribunalArgument[] }) {
  if (tribunalArguments.length === 0) {
    return null;
  }

  return (
    <div className="tribunal__arguments">
      <h3>Arguments</h3>
      <ul>
        {tribunalArguments.map((argument) => (
          <li key={argument.id}>
            <strong>{argument.agent_role}</strong>
            <span className="tribunal__meta">
              {argument.round} / {argument.stance}
            </span>
            <p>{argument.message}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TribunalError({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;

  return (
    <div className="notice notice--error" role="alert" data-testid="tribunal-error">
      <p>{apiError?.message ?? 'Something went wrong.'}</p>
      {apiError?.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{apiError.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
