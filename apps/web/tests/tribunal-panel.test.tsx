import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
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
  state: 'completed',
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
  findings: [
    {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FA1',
      agent_role: 'auditor',
      severity: 'blocker',
      subject: 'evidence',
      message: 'Comparison evidence is missing.',
      evidence: {},
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

describe('TribunalPanel', () => {
  it('renders a created Tribunal blackboard', async () => {
    vi.mocked(fetch).mockResolvedValue(json(TRIBUNAL, 201));

    renderWithQueryClient(<TribunalPanel />);
    await userEvent.type(screen.getByLabelText(/evaluation run id/i), RUN_ID);
    await userEvent.click(screen.getByRole('button', { name: /run tribunal/i }));

    expect(await screen.findByTestId('tribunal-result')).toHaveTextContent('blocked');
    expect(screen.getByTestId('tribunal-result')).toHaveTextContent(
      'Comparison evidence is missing.',
    );
    expect(vi.mocked(fetch).mock.calls[0]![1]?.method).toBe('POST');
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
