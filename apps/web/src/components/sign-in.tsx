'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import { API_BASE_URL, ApiError, listAuthProviders, signInWithEmail } from '@/lib/api';

/**
 * Deterministic sign-in for local development, CI and the public demo.
 *
 * The API refuses this provider in deployed environments, where GitHub OAuth is
 * used instead. The console does not decide that — it reflects what the API
 * offers.
 */
export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const inputId = useId();
  const [email, setEmail] = useState('ada@example.com');
  const providers = useQuery({
    queryKey: ['auth-providers'],
    queryFn: listAuthProviders,
    retry: false,
  });

  const submission = useMutation({
    mutationFn: (value: string) => signInWithEmail(value),
    onSuccess: onSignedIn,
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submission.mutate(email.trim());
  }

  const devProvider = providers.data?.find((provider) => provider.name === 'dev') ?? null;
  const githubProvider = providers.data?.find((provider) => provider.name === 'github') ?? null;
  const error = submission.error instanceof ApiError ? submission.error : null;
  const providerError = providers.error instanceof ApiError ? providers.error : null;

  return (
    <section className="panel panel--narrow" aria-labelledby="sign-in-heading">
      <h2 id="sign-in-heading">Sign in</h2>

      {providers.isPending ? (
        <p className="loading" role="status">
          Loading sign-in options…
        </p>
      ) : providerError ? (
        <ErrorNotice error={providerError} testId="sign-in-error" />
      ) : (
        <>
          {devProvider ? (
            <>
              <p className="form__hint">
                This deployment uses deterministic sign-in: any email address creates or resumes an
                account. No password, no provider, no credentials required.
              </p>

              <form className="form" onSubmit={onSubmit}>
                <label className="form__label" htmlFor={inputId}>
                  Email
                </label>
                <input
                  id={inputId}
                  className="form__input"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
                <button className="button" type="submit" disabled={submission.isPending}>
                  {submission.isPending ? 'Signing in…' : devProvider.label}
                </button>
              </form>
            </>
          ) : null}

          {githubProvider ? (
            <button
              className="button"
              type="button"
              onClick={() => window.location.assign(`${API_BASE_URL}/api/v1/auth/github/authorize`)}
            >
              {githubProvider.label}
            </button>
          ) : null}

          {!devProvider && !githubProvider ? (
            <p className="empty" role="status">
              No sign-in provider is configured.
            </p>
          ) : null}
        </>
      )}

      {error ? <ErrorNotice error={error} testId="sign-in-error" /> : null}
    </section>
  );
}

function ErrorNotice({ error, testId }: { error: ApiError; testId: string }) {
  return (
    <div className="notice notice--error" role="alert" data-testid={testId}>
      <p>{error.message}</p>
      {error.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{error.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
