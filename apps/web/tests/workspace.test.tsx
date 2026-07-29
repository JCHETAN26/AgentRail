import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { SignIn } from '@/components/sign-in';
import { Workspace } from '@/components/workspace';

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

function problem(code: string, status: number, correlationId = 'cid_test') {
  return json({ code, message: 'Denied.', correlation_id: correlationId, details: {} }, status);
}

const USER = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
  email: 'ada@example.com',
  display_name: 'Ada',
  created_at: '2026-07-26T00:00:00Z',
};

const ORGANISATION = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAW',
  name: 'Ada Labs',
  slug: 'ada-labs',
  created_at: '2026-07-26T00:00:00Z',
};

const PROJECT = {
  id: '01ARZ3NDEKTSV4RRFFQ69G5FAX',
  organisation_id: ORGANISATION.id,
  name: 'Default',
  slug: 'default',
  created_at: '2026-07-26T00:00:00Z',
};

const ME_WITH_ORG = {
  user: USER,
  principal_kind: 'user',
  organisations: [{ organisation: ORGANISATION, role: 'owner' }],
};

describe('SignIn', () => {
  it('signs in and notifies the shell', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        json({ providers: [{ name: 'dev', label: 'Continue with email', deterministic: true }] }),
      )
      .mockResolvedValue(json(ME_WITH_ORG));
    const onSignedIn = vi.fn();

    renderWithQueryClient(<SignIn onSignedIn={onSignedIn} />);
    await userEvent.click(await screen.findByRole('button', { name: /continue with email/i }));

    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
  });

  it('shows the correlation id when sign-in is rejected', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        json({ providers: [{ name: 'dev', label: 'Continue with email', deterministic: true }] }),
      )
      .mockResolvedValue(problem('validation_failed', 422, 'cid_rejected'));

    renderWithQueryClient(<SignIn onSignedIn={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: /continue with email/i }));

    expect(await screen.findByTestId('sign-in-error')).toHaveTextContent('cid_rejected');
  });

  it('offers GitHub when it is the configured provider', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { assign });
    vi.mocked(fetch).mockResolvedValue(
      json({
        providers: [{ name: 'github', label: 'Continue with GitHub', deterministic: false }],
      }),
    );

    renderWithQueryClient(<SignIn onSignedIn={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: /continue with github/i }));

    expect(assign).toHaveBeenCalledWith('http://localhost:8000/api/v1/auth/github/authorize');
  });
});

describe('Workspace', () => {
  it('shows a loading state before the session resolves', () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);

    expect(screen.getByRole('status')).toHaveTextContent('Loading your workspace');
  });

  it('renders the tenant context and the job launcher once loaded', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(ME_WITH_ORG))
      .mockResolvedValue(json({ items: [PROJECT] }));

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);

    expect(await screen.findByTestId('identity')).toHaveTextContent('ada@example.com');
    expect(screen.getByLabelText(/recorded replay mode/i)).toHaveTextContent('No paid model keys');
    expect(screen.getByLabelText(/guided demo tour/i)).toHaveTextContent('Tribunal verdict');
    expect(await screen.findByTestId('organisation-context')).toHaveTextContent('Ada Labs');
    expect(await screen.findByRole('heading', { name: /run a job/i })).toBeInTheDocument();
  });

  it('lets the user switch between dark and light mode', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(ME_WITH_ORG))
      .mockResolvedValue(json({ items: [PROJECT] }));

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);
    await screen.findByTestId('identity');

    await userEvent.click(screen.getByRole('button', { name: /switch to light mode/i }));

    expect(document.documentElement.dataset.theme).toBe('light');
    expect(screen.getByRole('button', { name: /switch to dark mode/i })).toBeInTheDocument();
  });

  it('prompts for a first organisation when the user has none', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({ user: USER, principal_kind: 'user', organisations: [] }),
    );

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);

    expect(
      await screen.findByRole('heading', { name: /create your organisation/i }),
    ).toBeInTheDocument();
  });

  it('returns to sign-in when the session has expired', async () => {
    vi.mocked(fetch).mockResolvedValue(problem('unauthenticated', 401));
    const onSignedOut = vi.fn();

    renderWithQueryClient(<Workspace onSignedOut={onSignedOut} />);

    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
  });

  it('explains a permission denial instead of showing a raw error', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(ME_WITH_ORG))
      .mockResolvedValue(problem('forbidden', 403, 'cid_denied'));

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);

    const notice = await screen.findByTestId('forbidden-notice');
    expect(notice).toHaveTextContent('do not have access');
    expect(notice).toHaveTextContent('cid_denied');
  });

  it('reports an empty organisation with no projects', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(ME_WITH_ORG))
      .mockResolvedValue(json({ items: [] }));

    renderWithQueryClient(<Workspace onSignedOut={vi.fn()} />);

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument();
  });

  it('signs out and notifies the shell', async () => {
    // A Response body can only be read once, so every call needs its own
    // instance. Sharing one across calls makes this test depend on exactly how
    // many requests the shell happens to make.
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(ME_WITH_ORG))
      .mockImplementation(() => Promise.resolve(json({ items: [PROJECT] })));
    const onSignedOut = vi.fn();

    renderWithQueryClient(<Workspace onSignedOut={onSignedOut} />);
    await screen.findByTestId('identity');

    vi.mocked(fetch).mockImplementation(() => Promise.resolve(json({ status: 'signed_out' })));
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));

    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
  });
});
