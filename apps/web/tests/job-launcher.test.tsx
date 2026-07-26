import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { JobLauncher } from '@/components/job-launcher';

const PROJECT_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAP';

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const PENDING_JOB = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
  kind: 'noop',
  state: 'PENDING',
  correlation_id: 'cid_test',
  trace_id: 'a'.repeat(32),
  attempts: 0,
  payload: { message: 'hello' },
  result: null,
  error_code: null,
  error_message: null,
  created_at: '2026-07-26T00:00:00Z',
  updated_at: '2026-07-26T00:00:00Z',
  started_at: null,
  completed_at: null,
};

const COMPLETED_JOB = {
  ...PENDING_JOB,
  state: 'COMPLETED',
  attempts: 1,
  result: { echo: 'hello', digest: '2cf24dba5fb0a30e', sandbox_version: '0.1.0' },
  started_at: '2026-07-26T00:00:01Z',
  completed_at: '2026-07-26T00:00:02Z',
};

describe('JobLauncher', () => {
  it('shows an empty state before anything is submitted', () => {
    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);

    expect(screen.getByRole('status')).toHaveTextContent('No job yet');
    expect(screen.queryByTestId('job-result')).not.toBeInTheDocument();
  });

  it('submits a job and then displays its completed result', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PENDING_JOB, 201))
      .mockResolvedValue(jsonResponse(COMPLETED_JOB));

    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    await userEvent.click(screen.getByRole('button', { name: /submit job/i }));

    await waitFor(() => {
      expect(screen.getByTestId('job-state')).toHaveTextContent('Completed');
    });
    expect(screen.getByTestId('job-result-payload')).toHaveTextContent('2cf24dba5fb0a30e');
  });

  it('renders the intermediate pending state while the worker is still running', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PENDING_JOB, 201))
      .mockResolvedValue(jsonResponse(PENDING_JOB));

    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    await userEvent.click(screen.getByRole('button', { name: /submit job/i }));

    await waitFor(() => {
      expect(screen.getByTestId('job-state')).toHaveTextContent('Pending');
    });
    expect(screen.queryByTestId('job-result-payload')).not.toBeInTheDocument();
  });

  it('surfaces a failed job with its error code', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PENDING_JOB, 201))
      .mockResolvedValue(
        jsonResponse({
          ...PENDING_JOB,
          state: 'FAILED',
          attempts: 1,
          error_code: 'dependency_unavailable',
          error_message: 'CloudOps sandbox call failed',
          completed_at: '2026-07-26T00:00:02Z',
        }),
      );

    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    await userEvent.click(screen.getByRole('button', { name: /submit job/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('CloudOps sandbox call failed');
    expect(alert).toHaveTextContent('dependency_unavailable');
  });

  it('shows the correlation id when job creation is rejected', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          code: 'validation_failed',
          message: 'The request failed validation.',
          correlation_id: 'cid_rejected',
          details: {},
        },
        422,
      ),
    );

    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    await userEvent.click(screen.getByRole('button', { name: /submit job/i }));

    const notice = await screen.findByTestId('error-notice');
    expect(notice).toHaveTextContent('The request failed validation.');
    expect(notice).toHaveTextContent('cid_rejected');
  });

  it('tells the user when the API cannot be reached', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));

    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    await userEvent.click(screen.getByRole('button', { name: /submit job/i }));

    expect(await screen.findByTestId('error-notice')).toHaveTextContent(
      'Could not reach the AgentRail API.',
    );
  });

  it('disables submission when the message is blank', async () => {
    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);
    const input = screen.getByLabelText(/message/i);

    await userEvent.clear(input);

    expect(screen.getByRole('button', { name: /submit job/i })).toBeDisabled();
  });

  it('labels the input so it is reachable by assistive technology', () => {
    renderWithQueryClient(<JobLauncher projectId={PROJECT_ID} />);

    expect(screen.getByLabelText(/message/i)).toBeInTheDocument();
  });
});
