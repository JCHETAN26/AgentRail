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

function mockTraceApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith(`/api/v1/evaluation-runs/${RUN_ID}/comparison`)) {
      return Promise.resolve(
        json({
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
        }),
      );
    }
    if (url.endsWith(`/api/v1/evaluation-runs/${RUN_ID}/evaluator-results`)) {
      return Promise.resolve(
        json({
          items: [
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

describe('TraceExplorer', () => {
  it('renders comparison metrics and trajectory inspectors for a run', async () => {
    mockTraceApi();

    renderWithQueryClient(<TraceExplorer />);
    await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
    await userEvent.click(screen.getByRole('button', { name: /load trace/i }));

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
    expect(screen.getByLabelText(/trajectory inspector/i)).toHaveTextContent('Checkpoints');
    expect(screen.getByLabelText(/graph state inspector/i)).toHaveTextContent('tool_call');
    expect(screen.getByLabelText(/tool call inspector/i)).toHaveTextContent('restart_service');
    expect(screen.getByLabelText(/evidence viewer/i)).toHaveTextContent('service stayed unhealthy');
  });
});
