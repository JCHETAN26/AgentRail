'use client';

import type { AgentVersion } from '@agentrail/contracts';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { ApiError, listAgentVersions, listProjectAgents } from '@/lib/api';

type VersionField = {
  key: keyof Pick<
    AgentVersion,
    'graph_spec' | 'prompt_bundle' | 'model_config' | 'tool_contracts' | 'policy_bundle'
  >;
  label: string;
};

const VERSION_FIELDS: VersionField[] = [
  { key: 'prompt_bundle', label: 'Prompts' },
  { key: 'model_config', label: 'Model config' },
  { key: 'tool_contracts', label: 'Tool contracts' },
  { key: 'policy_bundle', label: 'Policy' },
  { key: 'graph_spec', label: 'Graph' },
];

export function AgentVersionComparison({ projectId }: { projectId: string }) {
  const [agentId, setAgentId] = useState<string | null>(null);
  const [baselineId, setBaselineId] = useState<string | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);

  const agents = useQuery({
    queryKey: ['agents', projectId],
    queryFn: () => listProjectAgents(projectId),
  });

  const agentItems = useMemo(() => agents.data?.items ?? [], [agents.data?.items]);

  useEffect(() => {
    if (agentId === null && agentItems.length > 0) {
      setAgentId(agentItems[0]!.id);
    }
    if (agentId !== null && !agentItems.some((agent) => agent.id === agentId)) {
      setAgentId(agentItems[0]?.id ?? null);
    }
  }, [agentId, agentItems]);

  const versions = useQuery({
    queryKey: ['agent-versions', agentId],
    queryFn: () => listAgentVersions(agentId as string),
    enabled: agentId !== null,
  });

  const versionItems = useMemo(() => versions.data?.items ?? [], [versions.data?.items]);

  useEffect(() => {
    if (versionItems.length === 0) {
      setBaselineId(null);
      setCandidateId(null);
      return;
    }

    const newest = versionItems[0]!;
    const previous = versionItems[1] ?? newest;
    if (baselineId === null || !versionItems.some((version) => version.id === baselineId)) {
      setBaselineId(previous.id);
    }
    if (candidateId === null || !versionItems.some((version) => version.id === candidateId)) {
      setCandidateId(newest.id);
    }
  }, [baselineId, candidateId, versionItems]);

  const baseline = versionItems.find((version) => version.id === baselineId) ?? null;
  const candidate = versionItems.find((version) => version.id === candidateId) ?? null;
  const rows = useMemo(() => compareVersions(baseline, candidate), [baseline, candidate]);
  const changedCount = rows.filter((row) => row.changed).length;

  return (
    <section className="panel version-compare" aria-labelledby="version-compare-heading">
      <header className="version-compare__header">
        <div>
          <h2 id="version-compare-heading">Compare agent versions</h2>
          <p className="form__hint">
            Diff prompts, model settings, tools, policy, and graph snapshots before a rollout.
          </p>
        </div>
        {baseline !== null && candidate !== null ? (
          <span className={changedCount === 0 ? 'badge badge--completed' : 'badge badge--pending'}>
            {changedCount === 0 ? 'No changes' : `${changedCount} changed`}
          </span>
        ) : null}
      </header>

      {agents.isPending ? (
        <p className="loading" role="status">
          Loading agents...
        </p>
      ) : agents.isError ? (
        <ErrorNotice error={agents.error} />
      ) : agentItems.length === 0 ? (
        <p className="empty" role="status">
          No registered agents yet.
        </p>
      ) : (
        <>
          <div className="version-compare__controls">
            <label className="form__label">
              Agent
              <select
                className="form__input"
                value={agentId ?? ''}
                onChange={(event) => {
                  setAgentId(event.target.value);
                  setBaselineId(null);
                  setCandidateId(null);
                }}
              >
                {agentItems.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
            <VersionSelect
              label="Baseline"
              value={baselineId}
              versions={versionItems}
              onChange={setBaselineId}
            />
            <VersionSelect
              label="Candidate"
              value={candidateId}
              versions={versionItems}
              onChange={setCandidateId}
            />
          </div>

          {versions.isPending ? (
            <p className="loading" role="status">
              Loading versions...
            </p>
          ) : versions.isError ? (
            <ErrorNotice error={versions.error} />
          ) : versionItems.length < 2 ? (
            <p className="empty" role="status">
              Register at least two immutable versions to compare changes.
            </p>
          ) : baseline !== null && candidate !== null ? (
            <div className="version-compare__diff" data-testid="version-diff">
              <dl className="result__grid">
                <div>
                  <dt>Baseline digest</dt>
                  <dd>
                    <code>{shortDigest(baseline.content_digest)}</code>
                  </dd>
                </div>
                <div>
                  <dt>Candidate digest</dt>
                  <dd>
                    <code>{shortDigest(candidate.content_digest)}</code>
                  </dd>
                </div>
                <div>
                  <dt>Changed fields</dt>
                  <dd>{changedCount}</dd>
                </div>
              </dl>
              {rows.map((row) => (
                <article className="version-compare__field" key={row.label}>
                  <header>
                    <strong>{row.label}</strong>
                    <span
                      className={row.changed ? 'badge badge--pending' : 'badge badge--completed'}
                    >
                      {row.changed ? 'Changed' : 'Same'}
                    </span>
                  </header>
                  <div className="version-compare__payloads">
                    <VersionPayload title="Baseline" value={row.baseline} />
                    <VersionPayload title="Candidate" value={row.candidate} />
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function VersionSelect({
  label,
  value,
  versions,
  onChange,
}: {
  label: string;
  value: string | null;
  versions: AgentVersion[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="form__label">
      {label}
      <select
        className="form__input"
        value={value ?? ''}
        disabled={versions.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {versions.map((version) => (
          <option key={version.id} value={version.id}>
            v{version.version} - {shortDigest(version.content_digest)}
          </option>
        ))}
      </select>
    </label>
  );
}

function VersionPayload({ title, value }: { title: string; value: string }) {
  return (
    <div>
      <span>{title}</span>
      <pre className="result__payload">{value}</pre>
    </div>
  );
}

function compareVersions(baseline: AgentVersion | null, candidate: AgentVersion | null) {
  return VERSION_FIELDS.map((field) => {
    const baselineText = stableJson(baseline?.[field.key]);
    const candidateText = stableJson(candidate?.[field.key]);
    return {
      label: field.label,
      baseline: baselineText,
      candidate: candidateText,
      changed: baselineText !== candidateText,
    };
  });
}

function stableJson(value: unknown): string {
  return JSON.stringify(sortJson(value), null, 2);
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (typeof value !== 'object' || value === null) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sortJson(child)]),
  );
}

function shortDigest(digest: string | null | undefined): string {
  return digest?.slice(0, 12) ?? 'unknown';
}

function ErrorNotice({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;

  return (
    <div className="notice notice--error" role="alert" data-testid="version-compare-error">
      <p>{apiError?.message ?? 'Something went wrong.'}</p>
      {apiError?.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{apiError.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
