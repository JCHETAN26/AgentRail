import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { TraceExplorer } from '@/components/trace-explorer';

const RUN_ID = '01ARZ3NDEKTSV4RRFFQ69G5RUN';
const RUN_ITEM_ID = '01ARZ3NDEKTSV4RRFFQ69G5RIT';
const TRAJECTORY_ID = '01ARZ3NDEKTSV4RRFFQ69G5TRA';
const STEP_TOOL_ID = '01ARZ3NDEKTSV4RRFFQ69G5ST1';
const STEP_EVIDENCE_ID = '01ARZ3NDEKTSV4RRFFQ69G5ST2';
const CHECKPOINT_ID = '01ARZ3NDEKTSV4RRFFQ69G5CHK';

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const BASELINE_RUN_ID = '01ARZ3NDEKTSV4RRFFQ69G5BSE';

/** The comparison payload used unless a test overrides individual fields. */
function comparisonPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: '01ARZ3NDEKTSV4RRFFQ69G5CMP',
    project_id: '01ARZ3NDEKTSV4RRFFQ69G5PRJ',
    run_id: RUN_ID,
    baseline_agent_version_id: null,
    candidate_agent_version_id: '01ARZ3NDEKTSV4RRFFQ69G5AGV',
    suite_digest: 'd'.repeat(64),
    summary: { pass_rate: 0.875, regression_count: 1, reproducible: true },
    evaluator_metrics: {
      task_success: { pass_rate: 0.875, mean_score: 0.91, total: 8 },
    },
    category_metrics: {
      diagnosis: { pass_rate: 0.75, mean_score: 0.82, total: 4 },
    },
    regressions: [
      {
        run_item_id: RUN_ITEM_ID,
        item_index: 3,
        evaluator_slug: 'task_success',
        category: 'diagnosis',
        score: 0.25,
      },
    ],
    exports: {},
    created_at: '2026-07-26T00:00:00Z',
    baseline: null,
    evaluator_deltas: [],
    category_deltas: [],
    ...overrides,
  };
}

function evaluatorResult(index: number, state: 'PASSED' | 'FAILED') {
  return {
    id: `01ARZ3NDEKTSV4RRFFQ69G5E${String(index).padStart(2, '0')}`,
    run_id: RUN_ID,
    run_item_id: RUN_ITEM_ID,
    evaluator_version_id: null,
    evaluator_slug: `evaluator_${index}`,
    evaluator_kind: 'outcome',
    item_index: index,
    partition: 'default',
    category: 'diagnosis',
    state,
    score: state === 'PASSED' ? 1 : 0.25,
    threshold: 0.8,
    details: { reason: 'missing remediation' },
    created_at: '2026-07-26T00:00:00Z',
  };
}

function mockTraceApi(
  options: {
    comparison?: Record<string, unknown>;
    comparisonStatus?: number;
    results?: unknown[];
  } = {},
) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith(`/api/v1/evaluation-runs/${RUN_ID}/comparison`)) {
      if (options.comparisonStatus !== undefined) {
        return Promise.resolve(
          json(
            { code: 'forbidden', message: 'Not yours, or not permitted.', details: {} },
            options.comparisonStatus,
          ),
        );
      }
      return Promise.resolve(json(comparisonPayload(options.comparison)));
    }
    if (url.endsWith(`/api/v1/evaluation-runs/${RUN_ID}/evaluator-results`)) {
      return Promise.resolve(
        json({
          items: options.results ?? [
            {
              id: '01ARZ3NDEKTSV4RRFFQ69G5EVR',
              run_id: RUN_ID,
              run_item_id: RUN_ITEM_ID,
              evaluator_version_id: null,
              evaluator_slug: 'task_success',
              evaluator_kind: 'outcome',
              item_index: 3,
              partition: 'default',
              category: 'diagnosis',
              state: 'FAILED',
              score: 0.25,
              threshold: 0.8,
              details: { reason: 'missing remediation' },
              created_at: '2026-07-26T00:00:00Z',
            },
          ],
        }),
      );
    }
    if (url.endsWith(`/api/v1/evaluation-runs/${RUN_ID}/items`)) {
      return Promise.resolve(
        json({
          items: [
            {
              id: RUN_ITEM_ID,
              run_id: RUN_ID,
              item_index: 3,
              partition: 'default',
              state: 'FAILED_TERMINAL',
              trajectory_id: TRAJECTORY_ID,
              failing_step_id: STEP_TOOL_ID,
              error_code: 'tool_failed',
              error_message: 'Tool call failed.',
            },
          ],
        }),
      );
    }
    if (url.endsWith(`/api/v1/trajectories/${TRAJECTORY_ID}`)) {
      return Promise.resolve(
        json({
          id: TRAJECTORY_ID,
          project_id: '01ARZ3NDEKTSV4RRFFQ69G5PRJ',
          run_id: RUN_ID,
          run_item_id: RUN_ITEM_ID,
          item_index: 3,
          state: 'FAILED',
          summary: { error_code: 'tool_failed' },
          graph_state: { node: 'recorded_executor', stage: 'tool_call' },
          final_checkpoint: { stage: 'failed', retryable: false },
          created_at: '2026-07-26T00:00:00Z',
          updated_at: '2026-07-26T00:00:01Z',
          completed_at: '2026-07-26T00:00:01Z',
        }),
      );
    }
    if (url.endsWith(`/api/v1/trajectories/${TRAJECTORY_ID}/steps`)) {
      return Promise.resolve(
        json({
          items: [
            {
              id: STEP_TOOL_ID,
              trajectory_id: TRAJECTORY_ID,
              step_index: 2,
              step_type: 'tool_call',
              title: 'Recorded tool call',
              redacted_input: { tool: 'restart_service' },
              redacted_output: { status: 'error' },
              evidence: {},
              checkpoint: { stage: 'tool_call' },
              redaction_summary: {},
              latency_ms: 42,
              created_at: '2026-07-26T00:00:00Z',
            },
            {
              id: STEP_EVIDENCE_ID,
              trajectory_id: TRAJECTORY_ID,
              step_index: 3,
              step_type: 'evidence',
              title: 'Collect evidence',
              redacted_input: { source: 'recorded_fixture' },
              redacted_output: {},
              evidence: { rationale: 'service stayed unhealthy' },
              checkpoint: { stage: 'evidence' },
              redaction_summary: {},
              latency_ms: null,
              created_at: '2026-07-26T00:00:01Z',
            },
          ],
        }),
      );
    }
    if (url.endsWith(`/api/v1/trajectories/${TRAJECTORY_ID}/checkpoints`)) {
      return Promise.resolve(
        json({
          items: [
            {
              id: CHECKPOINT_ID,
              trajectory_id: TRAJECTORY_ID,
              step_id: STEP_TOOL_ID,
              checkpoint_index: 1,
              label: 'tool-call',
              state: { stage: 'tool_call' },
              created_at: '2026-07-26T00:00:00Z',
            },
          ],
        }),
      );
    }
    return Promise.resolve(json({ code: 'not_found', message: url, details: {} }, 404));
  });
}

async function loadTrace() {
  renderWithQueryClient(<TraceExplorer />);
  await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
  await userEvent.click(screen.getByRole('button', { name: /load trace/i }));
}

describe('TraceExplorer', () => {
  it('renders comparison metrics and trajectory inspectors for a run', async () => {
    mockTraceApi();

    await loadTrace();

    expect(await screen.findByLabelText(/comparison ui/i)).toHaveTextContent('87.5%');
    expect(screen.getByLabelText(/comparison regressions/i)).toHaveTextContent(
      'task_success on item 3',
    );
    expect(screen.getByLabelText(/evaluator selection ui/i)).toHaveTextContent('task_success');

    const runItems = screen.getByLabelText(/run item trace links/i);
    expect(within(runItems).getByRole('button', { name: /item 3/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    expect(await screen.findByLabelText(/timeline visualization/i)).toHaveTextContent(
      'Recorded tool call',
    );
    expect(screen.getByLabelText(/persisted checkpoints/i)).toHaveTextContent('tool-call');
    expect(screen.getByLabelText(/tool call inspector/i)).toHaveTextContent('restart_service');
    expect(screen.getByLabelText(/evidence viewer/i)).toHaveTextContent('service stayed unhealthy');
  });

  it('shows candidate-only metrics when the report has no baseline', async () => {
    mockTraceApi();

    await loadTrace();

    const comparison = await screen.findByLabelText(/comparison ui/i);
    expect(comparison).toHaveTextContent('no baseline');
    expect(screen.getByTestId('no-baseline-note')).toHaveTextContent(/declared no baseline/i);
    expect(screen.getByLabelText(/evaluator metrics \(candidate\)/i)).toHaveTextContent(
      'task_success',
    );
    expect(screen.queryByLabelText(/evaluator deltas/i)).toBeNull();
  });

  it('shows baseline, candidate and delta columns when a baseline exists', async () => {
    mockTraceApi({
      comparison: {
        baseline_agent_version_id: '01ARZ3NDEKTSV4RRFFQ69G5BAG',
        summary: { pass_rate: 0.5, regression_count: 1, reproducible: true },
        baseline: {
          id: '01ARZ3NDEKTSV4RRFFQ69G5BCP',
          run_id: BASELINE_RUN_ID,
          candidate_agent_version_id: '01ARZ3NDEKTSV4RRFFQ69G5BAG',
          suite_digest: 'd'.repeat(64),
          summary: { pass_rate: 0.875 },
          created_at: '2026-07-25T00:00:00Z',
        },
        evaluator_deltas: [
          {
            subject: 'task_success',
            status: 'regressed',
            baseline: { pass_rate: 0.875, mean_score: 0.91 },
            candidate: { pass_rate: 0.5, mean_score: 0.6 },
            delta: { pass_rate: -0.375, mean_score: -0.31 },
          },
        ],
        category_deltas: [
          {
            subject: 'diagnosis',
            status: 'improved',
            baseline: { pass_rate: 0.5 },
            candidate: { pass_rate: 0.75 },
            delta: { pass_rate: 0.25 },
          },
        ],
      },
    });

    await loadTrace();

    const deltas = await screen.findByLabelText(/evaluator deltas/i);
    const row = within(deltas).getByRole('row', { name: /task_success/i });
    expect(row).toHaveTextContent('87.5%');
    expect(row).toHaveTextContent('50.0%');
    expect(row).toHaveTextContent('-37.5pp');
    expect(row).toHaveTextContent('-0.31');
    expect(row).toHaveTextContent('regressed');

    const comparison = screen.getByLabelText(/comparison ui/i);
    expect(comparison).toHaveTextContent(BASELINE_RUN_ID);
    // Run-level pass rate delta, not just the per-evaluator rows.
    expect(comparison).toHaveTextContent('-37.5pp');
    expect(
      within(screen.getByLabelText(/category deltas/i)).getByRole('row', { name: /diagnosis/i }),
    ).toHaveTextContent('+25.0pp');
    expect(screen.queryByTestId('no-baseline-note')).toBeNull();
  });

  it('stops reporting loading once the comparison request fails', async () => {
    mockTraceApi({ comparisonStatus: 403 });

    await loadTrace();

    expect(await screen.findByRole('alert')).toHaveTextContent(/not yours, or not permitted/i);
    expect(screen.queryByText(/loading comparison report/i)).toBeNull();
  });

  it('keeps every evaluator result reachable past the first page', async () => {
    const results = Array.from({ length: 20 }, (_, index) =>
      evaluatorResult(index, index === 19 ? 'FAILED' : 'PASSED'),
    );
    mockTraceApi({ results });

    await loadTrace();

    const section = await screen.findByLabelText(/evaluator selection ui/i);
    expect(within(section).queryByText('evaluator_19')).toBeNull();
    expect(section).toHaveTextContent('Showing 12 of 20 results');

    await userEvent.click(within(section).getByRole('button', { name: /show all 20/i }));
    expect(within(section).getByText('evaluator_19')).toBeVisible();

    await userEvent.click(within(section).getByRole('button', { name: /findings only/i }));
    expect(within(section).getByText('evaluator_19')).toBeVisible();
    expect(within(section).queryByText('evaluator_0')).toBeNull();
  });

  it('shows the graph state recorded for the selected timeline step', async () => {
    mockTraceApi();

    await loadTrace();

    const inspector = await screen.findByLabelText(/graph state inspector/i);
    // The first step is selected by default.
    expect(inspector).toHaveTextContent('"stage": "tool_call"');

    const timeline = screen.getByLabelText(/timeline visualization/i);
    await userEvent.click(within(timeline).getByRole('button', { name: /collect evidence/i }));

    expect(screen.getByLabelText(/graph state inspector/i)).toHaveTextContent(
      '"stage": "evidence"',
    );
  });
});
