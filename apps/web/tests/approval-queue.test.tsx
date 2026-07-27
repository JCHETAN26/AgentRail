import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApprovalQueue } from '@/components/approval-queue';

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

function problem(code: string, status: number) {
  return json({ code, message: 'Denied.', correlation_id: 'cid_test', details: {} }, status);
}

const PROJECT_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAX';

const APPROVAL = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FB1',
  project_id: PROJECT_ID,
  run_id: '01ARZ3NDEKTSV4RRFFQ69G5FB2',
  run_item_id: '01ARZ3NDEKTSV4RRFFQ69G5FB3',
  trajectory_id: null,
  tool: 'restart_service',
  risk_level: 'HIGH_RISK_WRITE',
  state: 'PENDING',
  requested_arguments: { service: 'checkout', api_key: '[REDACTED]' },
  edited_arguments: null,
  reason: null,
  decided_by: null,
  decided_at: null,
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
};

describe('ApprovalQueue', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a pending high-risk call with its risk level', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);

    expect(await screen.findByTestId('approval-card')).toBeTruthy();
    expect(screen.getByTestId('approval-risk').textContent).toBe('HIGH_RISK_WRITE');
    expect(screen.getByText('restart_service')).toBeTruthy();
  });

  it('never renders a secret, because the API redacted it before storage', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    const { container } = renderWithQueryClient(
      <ApprovalQueue projectId={PROJECT_ID} role="reviewer" />,
    );

    await screen.findByTestId('approval-card');
    expect(container.textContent).toContain('[REDACTED]');
    expect(container.textContent).not.toContain('secret');
  });

  it('tells a reviewer when nothing is waiting', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);

    expect(await screen.findByTestId('approvals-empty')).toBeTruthy();
  });

  it('sends an approval and refreshes the queue', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ items: [APPROVAL] }))
      .mockResolvedValueOnce(json({ ...APPROVAL, state: 'APPROVED' }))
      .mockResolvedValue(json({ items: [] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.click(screen.getByTestId('approve-button'));

    await waitFor(() => expect(screen.queryByTestId('approvals-empty')).toBeTruthy());
    const decision = fetchMock.mock.calls[1]!;
    expect(String(decision[0])).toContain(`/api/v1/approvals/${APPROVAL.id}/decision`);
    expect(JSON.parse(String(decision[1]?.body))).toMatchObject({ approve: true });
  });

  it('refuses to submit malformed edited arguments before calling the API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.type(screen.getByLabelText(/Edited arguments/), '{{not json');
    await userEvent.click(screen.getByTestId('approve-button'));

    expect(await screen.findByText(/must be valid JSON/)).toBeTruthy();
    // One call: the list. The decision never left the browser.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('sends the reviewer edit as parsed JSON when approving', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ items: [APPROVAL] }))
      .mockResolvedValueOnce(json({ ...APPROVAL, state: 'APPROVED' }))
      .mockResolvedValue(json({ items: [] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.type(screen.getByLabelText(/Edited arguments/), '{{"service": "payments"}');
    await userEvent.click(screen.getByTestId('approve-button'));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))).toMatchObject({
      approve: true,
      edited_arguments: { service: 'payments' },
    });
  });

  it('blocks rejecting while an edit is present', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.type(screen.getByLabelText(/Edited arguments/), '{{"a": 1}');

    expect(screen.getByTestId('reject-button').hasAttribute('disabled')).toBe(true);
  });

  it('picks up an approval that parks after the first fetch', async () => {
    // The normal case: a reviewer is already looking at the screen when a run
    // stops. Focus refetching is disabled app-wide and an empty queue is never
    // invalidated, so only the poll can surface this.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(json({ items: [] }))
        .mockImplementation(() => Promise.resolve(json({ items: [APPROVAL] })));

      renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
      await screen.findByTestId('approvals-empty');

      await vi.advanceTimersByTimeAsync(6_000);

      await waitFor(() => expect(screen.queryByTestId('approval-card')).toBeTruthy());
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('refuses edited JSON that is not an object', async () => {
    // JSON.parse accepts arrays and bare values happily; casting them through
    // would send the API something its contract does not describe.
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    // `[` opens a key descriptor in userEvent's syntax, so it is escaped.
    await userEvent.type(screen.getByLabelText(/Edited arguments/), '[[1, 2]');
    await userEvent.click(screen.getByTestId('approve-button'));

    expect(await screen.findByText(/must be a JSON object/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('shows a viewer the request without the controls to decide it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ items: [APPROVAL] }));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="viewer" />);

    await screen.findByTestId('approval-card');
    expect(screen.getByTestId('approval-readonly')).toBeTruthy();
    expect(screen.queryByTestId('approve-button')).toBeNull();
  });

  it('explains a conflict rather than looking like a failed click', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ items: [APPROVAL] }))
      .mockResolvedValueOnce(problem('conflict', 409));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.click(screen.getByTestId('approve-button'));

    expect(await screen.findByTestId('approval-conflict')).toBeTruthy();
  });

  it('explains a server-side refusal even when the role looked sufficient', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ items: [APPROVAL] }))
      .mockResolvedValueOnce(problem('forbidden', 403));

    renderWithQueryClient(<ApprovalQueue projectId={PROJECT_ID} role="reviewer" />);
    await screen.findByTestId('approval-card');
    await userEvent.click(screen.getByTestId('approve-button'));

    expect(await screen.findByTestId('approval-forbidden')).toBeTruthy();
  });
});
