'use client';

import type {
  TribunalAgentRole,
  TribunalArgument,
  TribunalFinding,
  TribunalReplay,
  TribunalSession,
} from '@agentrail/contracts';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import {
  ApiError,
  createTribunalReplay,
  createTribunalSession,
  getTribunalSession,
  listTribunalReplays,
} from '@/lib/api';

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

      <ol className="tribunal__rounds" aria-label="Tribunal state machine">
        {tribunal.rounds.map((round) => (
          <li key={round.id}>
            <strong>{round.round}</strong>
            <span className="tribunal__meta">{round.state}</span>
          </li>
        ))}
      </ol>

      <div className="tribunal__roles">
        {ROLES.map((role) => (
          <RoleFindings key={role} role={role} findings={findingsByRole.get(role) ?? []} />
        ))}
      </div>

      <Arguments arguments={tribunal.arguments} />

      <TribunalReplays tribunal={tribunal} />

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

function TribunalReplays({ tribunal }: { tribunal: TribunalSession }) {
  const queryClient = useQueryClient();
  const [defenderPrompt, setDefenderPrompt] = useState(
    'Argue only from independently reproduced evidence.',
  );
  const replays = useQuery({
    queryKey: ['tribunal-replays', tribunal.id],
    queryFn: () => listTribunalReplays(tribunal.id),
  });
  const createReplay = useMutation({
    mutationFn: (mode: 'recorded' | 'forked') =>
      createTribunalReplay(
        tribunal.id,
        mode === 'recorded'
          ? { mode }
          : {
              mode,
              prompt_overrides: { defender: defenderPrompt },
            },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tribunal-replays', tribunal.id] });
    },
  });
  const replayItems = Array.isArray(replays.data?.items) ? replays.data.items : [];
  const latest = createReplay.data ?? replayItems.at(-1) ?? null;
  const error = createReplay.error ?? replays.error;

  return (
    <section className="tribunal__replays" aria-label="Tribunal replays">
      <div className="tribunal__replay-header">
        <div>
          <h3>Replays</h3>
          <p className="form__hint">
            Recorded replay and Defender prompt forks are stored separately from the source verdict.
          </p>
        </div>
        <button
          className="button button--quiet"
          type="button"
          onClick={() => createReplay.mutate('recorded')}
          disabled={createReplay.isPending}
        >
          {createReplay.isPending ? 'Replaying...' : 'Replay'}
        </button>
      </div>

      <label className="form__label" htmlFor={`defender-prompt-${tribunal.id}`}>
        Defender fork prompt
      </label>
      <div className="tribunal__fork-row">
        <textarea
          id={`defender-prompt-${tribunal.id}`}
          className="form__input"
          rows={3}
          value={defenderPrompt}
          onChange={(event) => setDefenderPrompt(event.target.value)}
        />
        <button
          className="button"
          type="button"
          onClick={() => createReplay.mutate('forked')}
          disabled={createReplay.isPending || defenderPrompt.trim().length === 0}
        >
          Fork Defender
        </button>
      </div>

      {error ? <TribunalError error={error} /> : null}

      {latest ? <ReplaySummary replay={latest} /> : null}

      {replayItems.length ? (
        <ul className="tribunal__replay-list" aria-label="Tribunal replay history">
          {replayItems.map((replay) => (
            <li key={replay.id}>
              <span className={`badge badge--tribunal-${replay.outcome}`}>{replay.outcome}</span>
              <span className="tribunal__meta">
                {replay.mode} / {digestPrefix(replay.replay_digest)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function ReplaySummary({ replay }: { replay: TribunalReplay }) {
  const result = objectFromJson(replay.result);
  const divergence = objectFromJson(replay.divergence);
  const reproduced = result.reproduced === true;
  const outcomeChanged = divergence.outcome_changed === true;

  return (
    <div className="tribunal__replay-summary" data-testid="tribunal-replay-result">
      <span className={`badge badge--tribunal-${replay.outcome}`}>{replay.outcome}</span>
      <dl className="result__grid">
        <div>
          <dt>Mode</dt>
          <dd>{replay.mode}</dd>
        </div>
        <div>
          <dt>Digest</dt>
          <dd>{reproduced ? 'matched' : 'changed'}</dd>
        </div>
        <div>
          <dt>Verdict</dt>
          <dd>{outcomeChanged ? 'changed' : 'same'}</dd>
        </div>
        <div>
          <dt>Replay</dt>
          <dd>
            <code>{digestPrefix(replay.replay_digest)}</code>
          </dd>
        </div>
      </dl>
      <p>{replay.primary_reason}</p>
    </div>
  );
}

function objectFromJson(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function digestPrefix(value: string | null | undefined): string {
  return typeof value === 'string' && value.length > 0 ? value.slice(0, 10) : 'pending';
}

function messageFromPayload(payload: Record<string, unknown>): string | null {
  return typeof payload.message === 'string' ? payload.message : null;
}

type EvidenceStepLink = {
  trajectoryId: string;
  stepId: string;
  stepIndex: number | null;
  stepType: string | null;
  title: string | null;
  itemIndex: number | null;
  evaluatorSlug: string | null;
};

function evidenceStepLinks(evidence: Record<string, unknown> | undefined): EvidenceStepLink[] {
  const rawLinks = evidence?.trajectory_steps;
  if (!Array.isArray(rawLinks)) {
    return [];
  }
  return rawLinks.flatMap((rawLink) => {
    if (!isRecord(rawLink)) {
      return [];
    }
    const trajectoryId = rawLink.trajectory_id;
    const stepId = rawLink.step_id;
    if (typeof trajectoryId !== 'string' || typeof stepId !== 'string') {
      return [];
    }
    return [
      {
        trajectoryId,
        stepId,
        stepIndex: typeof rawLink.step_index === 'number' ? rawLink.step_index : null,
        stepType: typeof rawLink.step_type === 'string' ? rawLink.step_type : null,
        title: typeof rawLink.title === 'string' ? rawLink.title : null,
        itemIndex: typeof rawLink.item_index === 'number' ? rawLink.item_index : null,
        evaluatorSlug: typeof rawLink.evaluator_slug === 'string' ? rawLink.evaluator_slug : null,
      },
    ];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function trajectoryStepHref(link: EvidenceStepLink): string {
  const params = link.stepType ? `?step_type=${encodeURIComponent(link.stepType)}` : '';
  return `/api/v1/trajectories/${encodeURIComponent(link.trajectoryId)}/steps${params}#${encodeURIComponent(link.stepId)}`;
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
              <EvidenceLinks links={evidenceStepLinks(finding.evidence)} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function EvidenceLinks({ links }: { links: EvidenceStepLink[] }) {
  if (links.length === 0) {
    return null;
  }
  return (
    <ul className="tribunal__evidence-links" aria-label="Finding evidence links">
      {links.map((link) => (
        <li key={`${link.trajectoryId}:${link.stepId}`}>
          <a href={trajectoryStepHref(link)}>
            {link.itemIndex === null ? 'item' : `item ${link.itemIndex}`} -{' '}
            {link.stepIndex === null ? 'step' : `step ${link.stepIndex}`}
          </a>
          <span className="tribunal__meta">
            {link.evaluatorSlug ?? 'trajectory'} - {link.title ?? link.stepType ?? 'evidence'}
          </span>
        </li>
      ))}
    </ul>
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
