import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { TribunalPanel } from '@/components/tribunal-panel';

const RUN_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAR';

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

const TRIBUNAL = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAT',
  project_id: '01ARZ3NDEKTSV4RRFFQ69G5FAX',
  run_id: RUN_ID,
  state: 'PUBLISHED',
  outcome: 'blocked',
  summary: { agent_count: 6, finding_count: 2, blocker_count: 1 },
  created_by: null,
  created_at: '2026-07-28T00:00:00Z',
  completed_at: '2026-07-28T00:00:00Z',
  verdict: {
    id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    outcome: 'blocked',
    primary_reason: 'Comparison evidence is missing.',
    dissent: { auditor_blockers: 1 },
    evidence: {},
    created_at: '2026-07-28T00:00:00Z',
  },
  rounds: [
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5RB1',
      sequence: 1,
      round: 'evidence',
      state: 'TRIBUNAL_EVIDENCE',
      summary: { finding_count: 2, argument_count: 0 },
      started_at: '2026-07-28T00:00:00Z',
      completed_at: '2026-07-28T00:00:00Z',
    },
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5RB2',
      sequence: 2,
      round: 'debate',
      state: 'TRIBUNAL_DEBATE',
      summary: { finding_count: 0, argument_count: 0 },
      started_at: '2026-07-28T00:00:00Z',
      completed_at: '2026-07-28T00:00:00Z',
    },
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5RB3',
      sequence: 3,
      round: 'verdict',
      state: 'TRIBUNAL_VERDICT',
      summary: { finding_count: 0, argument_count: 1, outcome: 'blocked' },
      started_at: '2026-07-28T00:00:00Z',
      completed_at: '2026-07-28T00:00:00Z',
    },
  ],
  findings: [
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FA1',
      agent_role: 'auditor',
      severity: 'blocker',
      subject: 'evidence',
      message: 'Comparison evidence is missing.',
      evidence: {
        trajectory_steps: [
          {
            trajectory_id: '01ARZ3NDEKTSV4RRFFQ69G5TR',
            step_id: '01ARZ3NDEKTSV4RRFFQ69G5ST',
            step_index: 5,
            step_type: 'final_result',
            title: 'Recorded final result',
            item_index: 3,
            evaluator_slug: 'task_success',
          },
        ],
      },
      created_at: '2026-07-28T00:00:00Z',
    },
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FA2',
      agent_role: 'defender',
      severity: 'info',
      subject: 'defense',
      message: 'The defense found clean quality evidence.',
      evidence: {},
      created_at: '2026-07-28T00:00:00Z',
    },
  ],
  arguments: [
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FA3',
      round: 'verdict',
      agent_role: 'judge',
      stance: 'supports_block',
      message: 'Comparison evidence is missing.',
      evidence: {},
      created_at: '2026-07-28T00:00:00Z',
    },
  ],
  blackboard: [
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FA4',
      sequence: 1,
      round: 'evidence',
      agent_role: 'auditor',
      entry_type: 'finding',
      title: 'evidence',
      payload: { message: 'Comparison evidence is missing.' },
      created_at: '2026-07-28T00:00:00Z',
    },
  ],
};

const REPLAY = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5R1',
  project_id: '01ARZ3NDEKTSV4RRFFQ69G5FAX',
  session_id: TRIBUNAL.id,
  source_run_id: RUN_ID,
  mode: 'recorded',
  state: 'COMPLETED',
  outcome: 'blocked',
  primary_reason: 'Comparison evidence is missing.',
  source_digest: 'a'.repeat(64),
  replay_digest: 'b'.repeat(64),
  request: {},
  result: { reproduced: false, source_outcome: 'blocked', replay_outcome: 'blocked' },
  divergence: { outcome_changed: false },
  safety_summary: { live_model_calls: 0 },
  created_by: null,
  created_at: '2026-07-28T00:00:00Z',
  completed_at: '2026-07-28T00:00:00Z',
};

function replayPostBodies() {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([url, init]) => String(url).includes('/replays') && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)));
}

describe('TribunalPanel', () => {
  it('renders a created Tribunal blackboard', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(TRIBUNAL, 201));
    vi.mocked(fetch).mockResolvedValue(json({ items: [] }));

    renderWithQueryClient(<TribunalPanel />);
    await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
    await userEvent.click(screen.getByRole('button', { name: /run tribunal/i }));

    expect(await screen.findByTestId('tribunal-result')).toHaveTextContent('blocked');
    expect(screen.getByTestId('tribunal-result')).toHaveTextContent(
      'Comparison evidence is missing.',
    );
    expect(screen.getByLabelText(/tribunal state machine/i)).toHaveTextContent('TRIBUNAL_VERDICT');
    expect(screen.getByLabelText(/finding evidence links/i)).toHaveTextContent('item 3 - step 5');
    expect(screen.getByRole('link', { name: /item 3 - step 5/i })).toHaveAttribute(
      'href',
      'http://localhost:8000/api/v1/trajectories/01ARZ3NDEKTSV4RRFFQ69G5TR/steps?step_type=final_result#01ARZ3NDEKTSV4RRFFQ69G5ST',
    );
    expect(vi.mocked(fetch).mock.calls[0]![1]?.method).toBe('POST');
  });

  it('creates recorded and forked Tribunal replays', async () => {
    const forkedReplay = { ...REPLAY, id: '01ARZ3NDEKTSV4RRFFQ69G5R2', mode: 'forked' };
    vi.mocked(fetch).mockResolvedValueOnce(json(TRIBUNAL, 201));
    vi.mocked(fetch).mockResolvedValueOnce(json({ items: [] }));
    vi.mocked(fetch).mockResolvedValueOnce(json(REPLAY));
    vi.mocked(fetch).mockResolvedValueOnce(json({ items: [REPLAY] }));
    vi.mocked(fetch).mockResolvedValueOnce(json(forkedReplay));
    vi.mocked(fetch).mockResolvedValue(json({ items: [REPLAY, forkedReplay] }));

    renderWithQueryClient(<TribunalPanel />);
    await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
    await userEvent.click(screen.getByRole('button', { name: /run tribunal/i }));
    await screen.findByTestId('tribunal-result');
    await waitFor(() =>
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([url, init]) => String(url).includes('/replays') && !init?.method),
      ).toBe(true),
    );

    await userEvent.click(screen.getByRole('button', { name: /^replay$/i }));
    expect(await screen.findByTestId('tribunal-replay-result')).toHaveTextContent('changed');
    expect(replayPostBodies()).toContainEqual({ mode: 'recorded' });

    await userEvent.click(screen.getByRole('button', { name: /fork defender/i }));
    expect(replayPostBodies()).toContainEqual({
      mode: 'forked',
      prompt_overrides: { defender: 'Argue only from independently reproduced evidence.' },
    });
  });

  it('shows an empty Tribunal state when a run has no persisted session', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({ code: 'not_found', message: 'No Tribunal session.', correlation_id: 'cid_t' }, 404),
    );

    renderWithQueryClient(<TribunalPanel />);
    await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
    await userEvent.click(screen.getByRole('button', { name: /inspect/i }));

    expect(await screen.findByTestId('tribunal-missing')).toHaveTextContent(
      'No Tribunal exists for this run yet.',
    );
  });
});
