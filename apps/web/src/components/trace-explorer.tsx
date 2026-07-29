'use client';

import type {
  ComparisonReport,
  EvaluationResult,
  RunItemTrace,
  Trajectory,
  TrajectoryCheckpoint,
  TrajectoryStep,
} from '@agentrail/contracts';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useId, useMemo, useState, type FormEvent } from 'react';

import {
  ApiError,
  getComparisonReport,
  getTrajectory,
  listEvaluationResults,
  listRunItems,
  listTrajectoryCheckpoints,
  listTrajectorySteps,
} from '@/lib/api';

const RUN_ID_LENGTH = 26;

export function TraceExplorer() {
  const inputId = useId();
  const [draftRunId, setDraftRunId] = useState('');
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedTrajectoryId, setSelectedTrajectoryId] = useState<string | null>(null);
  const trimmed = draftRunId.trim();
  const canLoad = trimmed.length === RUN_ID_LENGTH;

  const comparison = useQuery({
    queryKey: ['comparison', runId],
    queryFn: () => getComparisonReport(runId as string),
    enabled: runId !== null,
  });
  const results = useQuery({
    queryKey: ['evaluation-results', runId],
    queryFn: () => listEvaluationResults(runId as string),
    enabled: runId !== null,
  });
  const runItems = useQuery({
    queryKey: ['run-items', runId],
    queryFn: () => listRunItems(runId as string),
    enabled: runId !== null,
  });

  const trajectoryIds = useMemo(
    () =>
      runItems.data?.items.flatMap((item) => (item.trajectory_id ? [item.trajectory_id] : [])) ??
      [],
    [runItems.data],
  );

  useEffect(() => {
    setSelectedTrajectoryId(null);
  }, [runId]);

  useEffect(() => {
    if (selectedTrajectoryId === null && trajectoryIds.length > 0) {
      setSelectedTrajectoryId(trajectoryIds[0]!);
    }
  }, [selectedTrajectoryId, trajectoryIds]);

  const trajectory = useQuery({
    queryKey: ['trajectory', selectedTrajectoryId],
    queryFn: () => getTrajectory(selectedTrajectoryId as string),
    enabled: selectedTrajectoryId !== null,
  });
  const steps = useQuery({
    queryKey: ['trajectory-steps', selectedTrajectoryId],
    queryFn: () => listTrajectorySteps(selectedTrajectoryId as string),
    enabled: selectedTrajectoryId !== null,
  });
  const checkpoints = useQuery({
    queryKey: ['trajectory-checkpoints', selectedTrajectoryId],
    queryFn: () => listTrajectoryCheckpoints(selectedTrajectoryId as string),
    enabled: selectedTrajectoryId !== null,
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canLoad) {
      setRunId(trimmed);
    }
  }

  return (
    <section className="panel trace" aria-labelledby="trace-heading">
      <header className="trace__header">
        <div>
          <h2 id="trace-heading">Trace explorer</h2>
          <p className="form__hint">Inspect comparison deltas and recorded execution evidence.</p>
        </div>
      </header>

      <form className="trace__lookup" onSubmit={onSubmit}>
        <label className="form__label" htmlFor={inputId}>
          Evaluation run id
        </label>
        <div className="trace__lookup-row">
          <input
            id={inputId}
            className="form__input"
            value={draftRunId}
            onChange={(event) => setDraftRunId(event.target.value)}
            placeholder="01..."
            aria-describedby={`${inputId}-hint`}
          />
          <button className="button" type="submit" disabled={!canLoad}>
            Load trace
          </button>
        </div>
        <p className="form__hint" id={`${inputId}-hint`}>
          Use a completed evaluation run to load comparison metrics, run items, and trajectory
          steps.
        </p>
      </form>

      {runId === null ? (
        <p className="empty" role="status">
          No trace loaded.
        </p>
      ) : (
        <div className="trace__body" data-testid="trace-result">
          <QueryError query={comparison} />
          <QueryError query={results} />
          <QueryError query={runItems} />
          <ComparisonSummary comparison={comparison.data} />
          <EvaluatorResults results={results.data?.items ?? []} />
          <RunItems
            items={runItems.data?.items ?? []}
            selectedTrajectoryId={selectedTrajectoryId}
            onSelect={setSelectedTrajectoryId}
          />
          <QueryError query={trajectory} />
          <QueryError query={steps} />
          <QueryError query={checkpoints} />
          <TrajectoryInspector
            trajectory={trajectory.data}
            steps={steps.data?.items ?? []}
            checkpoints={checkpoints.data?.items ?? []}
          />
        </div>
      )}
    </section>
  );
}

function ComparisonSummary({ comparison }: { comparison: ComparisonReport | undefined }) {
  if (comparison === undefined) {
    return (
      <p className="loading" role="status">
        Loading comparison report…
      </p>
    );
  }

  const evaluatorEntries = Object.entries(comparison.evaluator_metrics).filter(([, value]) =>
    isRecord(value),
  );
  const categoryEntries = Object.entries(comparison.category_metrics).filter(([, value]) =>
    isRecord(value),
  );

  return (
    <section className="trace__section" aria-label="Comparison UI">
      <header className="trace__section-header">
        <h3>Comparison</h3>
        <span className="badge">{comparison.regressions.length} regressions</span>
      </header>
      <dl className="trace__metrics">
        <Metric
          label="Pass rate"
          value={formatPercent(numberMetric(comparison.summary.pass_rate))}
        />
        <Metric
          label="Regressions"
          value={String(numberMetric(comparison.summary.regression_count))}
        />
        <Metric label="Suite digest" value={digestPrefix(comparison.suite_digest)} />
      </dl>

      <MetricTable title="Evaluator deltas" entries={evaluatorEntries} />
      <MetricTable title="Category breakdown" entries={categoryEntries} />
      <RegressionList regressions={comparison.regressions} />
    </section>
  );
}

function MetricTable({
  title,
  entries,
}: {
  title: string;
  entries: [string, Record<string, unknown>][];
}) {
  if (entries.length === 0) {
    return null;
  }
  return (
    <div className="trace__table" aria-label={title}>
      <h4>{title}</h4>
      <table>
        <thead>
          <tr>
            <th scope="col">Subject</th>
            <th scope="col">Pass rate</th>
            <th scope="col">Mean score</th>
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, metric]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{formatPercent(numberMetric(metric.pass_rate))}</td>
              <td>{formatDecimal(numberMetric(metric.mean_score))}</td>
              <td>{numberMetric(metric.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RegressionList({ regressions }: { regressions: Record<string, unknown>[] }) {
  if (regressions.length === 0) {
    return (
      <p className="empty" role="status">
        No worsened metrics in this comparison.
      </p>
    );
  }
  return (
    <ol className="trace__regressions" aria-label="Comparison regressions">
      {regressions.map((regression, index) => (
        <li
          key={`${stringMetric(regression.run_item_id)}:${stringMetric(regression.evaluator_slug)}:${index}`}
        >
          <strong>
            {stringMetric(regression.evaluator_slug) || 'evaluator'} on item{' '}
            {numberMetric(regression.item_index)}
          </strong>
          <span className="trace__meta">
            {stringMetric(regression.category) || 'uncategorised'} · score{' '}
            {formatDecimal(numberMetric(regression.score))}
          </span>
        </li>
      ))}
    </ol>
  );
}

function EvaluatorResults({ results }: { results: EvaluationResult[] }) {
  if (results.length === 0) {
    return null;
  }
  const failed = results.filter((result) => result.state !== 'PASSED');
  return (
    <section className="trace__section" aria-label="Evaluator selection UI">
      <header className="trace__section-header">
        <h3>Evaluator results</h3>
        <span className="badge">{failed.length} findings</span>
      </header>
      <div className="trace__table">
        <table>
          <thead>
            <tr>
              <th scope="col">Evaluator</th>
              <th scope="col">Item</th>
              <th scope="col">State</th>
              <th scope="col">Score</th>
            </tr>
          </thead>
          <tbody>
            {results.slice(0, 12).map((result) => (
              <tr key={result.id}>
                <td>{result.evaluator_slug}</td>
                <td>{result.item_index}</td>
                <td>
                  <span className={`badge badge--${result.state.toLowerCase()}`}>
                    {result.state}
                  </span>
                </td>
                <td>{formatDecimal(result.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RunItems({
  items,
  selectedTrajectoryId,
  onSelect,
}: {
  items: RunItemTrace[];
  selectedTrajectoryId: string | null;
  onSelect: (trajectoryId: string) => void;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section className="trace__section" aria-label="Run item trace links">
      <header className="trace__section-header">
        <h3>Run items</h3>
        <span className="badge">{items.length} items</span>
      </header>
      <ul className="trace__items">
        {items.map((item) => (
          <li key={item.id}>
            <button
              className="button button--quiet"
              type="button"
              disabled={item.trajectory_id === null}
              aria-pressed={item.trajectory_id === selectedTrajectoryId}
              onClick={() => item.trajectory_id && onSelect(item.trajectory_id)}
            >
              Item {item.item_index}
            </button>
            <span className={`badge badge--${item.state.toLowerCase()}`}>{item.state}</span>
            <span className="trace__meta">
              {item.failing_step_id
                ? `failing step ${item.failing_step_id.slice(0, 10)}`
                : item.partition}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TrajectoryInspector({
  trajectory,
  steps,
  checkpoints,
}: {
  trajectory: Trajectory | undefined;
  steps: TrajectoryStep[];
  checkpoints: TrajectoryCheckpoint[];
}) {
  if (trajectory === undefined) {
    return null;
  }

  const toolSteps = steps.filter((step) => step.step_type === 'tool_call');
  const evidenceSteps = steps.filter((step) => Object.keys(step.evidence).length > 0);

  return (
    <section className="trace__section" aria-label="Trajectory inspector">
      <header className="trace__section-header">
        <h3>Trajectory {trajectory.item_index}</h3>
        <span className={`badge badge--${trajectory.state.toLowerCase()}`}>{trajectory.state}</span>
      </header>
      <dl className="trace__metrics">
        <Metric label="Trajectory" value={digestPrefix(trajectory.id)} />
        <Metric label="Steps" value={String(steps.length)} />
        <Metric label="Checkpoints" value={String(checkpoints.length)} />
      </dl>

      <div className="trace__split">
        <JsonPanel title="Graph state" value={trajectory.graph_state} />
        <JsonPanel title="Final checkpoint" value={trajectory.final_checkpoint} />
      </div>

      <Timeline steps={steps} />
      <StepInspector
        title="Tool call inspector"
        empty="No tool calls recorded."
        steps={toolSteps}
      />
      <StepInspector
        title="Evidence viewer"
        empty="No evidence payloads recorded."
        steps={evidenceSteps}
      />
      <CheckpointList checkpoints={checkpoints} />
    </section>
  );
}

function Timeline({ steps }: { steps: TrajectoryStep[] }) {
  if (steps.length === 0) {
    return (
      <p className="empty" role="status">
        No trajectory steps recorded yet.
      </p>
    );
  }
  return (
    <ol className="trace__timeline" aria-label="Timeline visualization">
      {steps.map((step) => (
        <li key={step.id}>
          <span className="trace__sequence">{step.step_index}</span>
          <div>
            <strong>{step.title}</strong>
            <span className="trace__meta">
              {step.step_type}
              {step.latency_ms === null ? '' : ` · ${step.latency_ms}ms`}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function StepInspector({
  title,
  empty,
  steps,
}: {
  title: string;
  empty: string;
  steps: TrajectoryStep[];
}) {
  return (
    <section className="trace__subsection" aria-label={title}>
      <h4>{title}</h4>
      {steps.length === 0 ? (
        <p className="empty" role="status">
          {empty}
        </p>
      ) : (
        <ul className="trace__step-details">
          {steps.map((step) => (
            <li key={step.id}>
              <strong>{step.title}</strong>
              <div className="trace__split">
                <JsonPanel title="Input" value={step.redacted_input} />
                <JsonPanel
                  title="Output"
                  value={
                    Object.keys(step.evidence).length > 0 ? step.evidence : step.redacted_output
                  }
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CheckpointList({ checkpoints }: { checkpoints: TrajectoryCheckpoint[] }) {
  if (checkpoints.length === 0) {
    return null;
  }
  return (
    <section className="trace__subsection" aria-label="Graph state inspector">
      <h4>Checkpoints</h4>
      <ul className="trace__step-details">
        {checkpoints.map((checkpoint) => (
          <li key={checkpoint.id}>
            <strong>{checkpoint.label}</strong>
            <JsonPanel
              title={`checkpoint ${checkpoint.checkpoint_index}`}
              value={checkpoint.state}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function JsonPanel({ title, value }: { title: string; value: Record<string, unknown> }) {
  return (
    <div className="trace__json">
      <span>{title}</span>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function QueryError({ query }: { query: { isError: boolean; error: unknown } }) {
  if (!query.isError) {
    return null;
  }
  const apiError = query.error instanceof ApiError ? query.error : null;
  return (
    <div className="notice notice--error" role="alert">
      <p>{apiError?.message ?? 'Could not load trace evidence.'}</p>
      {apiError?.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{apiError.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function numberMetric(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function stringMetric(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatDecimal(value: number): string {
  return value.toFixed(2);
}

function digestPrefix(value: string | null | undefined): string {
  return typeof value === 'string' && value.length > 0 ? value.slice(0, 10) : 'pending';
}
