import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { AgentVersionComparison } from '@/components/agent-version-comparison';

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

const AGENT = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAG',
  project_id: '01ARZ3NDEKTSV4RRFFQ69G5FAX',
  name: 'CloudOps responder',
  slug: 'cloudops-responder',
  description: null,
  created_at: '2026-07-26T00:00:00Z',
};

const BASE_VERSION = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FV1',
  agent_id: AGENT.id,
  version: 1,
  content_digest: '111111111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  graph_spec: { entrypoint: 'triage' },
  prompt_bundle: { system: 'Diagnose the incident.' },
  model_config: { model: 'gpt-4.1-mini' },
  tool_contracts: [{ name: 'get_service_health', risk_level: 'READ_ONLY' }],
  policy_bundle: { max_tool_calls: 3 },
  source_commit: 'abc1234',
  created_at: '2026-07-26T00:00:00Z',
};

const CANDIDATE_VERSION = {
  ...BASE_VERSION,
  id: '01ARZ3NDEKTSV4RRFFQ69G5FV2',
  version: 2,
  content_digest: '222222222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  prompt_bundle: { system: 'Diagnose the incident and cite evidence.' },
};

describe('AgentVersionComparison', () => {
  it('loads agent versions and renders changed prompt fields', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ items: [AGENT] }))
      .mockResolvedValueOnce(json({ items: [CANDIDATE_VERSION, BASE_VERSION] }));

    renderWithQueryClient(<AgentVersionComparison projectId="01ARZ3NDEKTSV4RRFFQ69G5FAX" />);

    expect(await screen.findByRole('heading', { name: /compare agent versions/i })).toBeVisible();
    expect(await screen.findByTestId('version-diff')).toHaveTextContent('1');

    const prompts = screen.getByText('Prompts').closest('article');
    expect(prompts).not.toBeNull();
    expect(within(prompts as HTMLElement).getByText('Changed')).toBeVisible();
    expect(prompts).toHaveTextContent('cite evidence');
  });

  it('shows an empty state when no agent versions can be compared', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ items: [AGENT] }))
      .mockResolvedValueOnce(json({ items: [BASE_VERSION] }));

    renderWithQueryClient(<AgentVersionComparison projectId="01ARZ3NDEKTSV4RRFFQ69G5FAX" />);

    expect(await screen.findByText(/at least two immutable versions/i)).toBeVisible();
  });
});
