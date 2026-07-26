'use client';

import { useMutation } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import { ApiError, signInWithEmail } from '@/lib/api';

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

  const submission = useMutation({
    mutationFn: (value: string) => signInWithEmail(value),
    onSuccess: onSignedIn,
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submission.mutate(email.trim());
  }

  const error = submission.error instanceof ApiError ? submission.error : null;

  return (
    <section className="panel panel--narrow" aria-labelledby="sign-in-heading">
      <h2 id="sign-in-heading">Sign in</h2>
      <p className="form__hint">
        This deployment uses deterministic sign-in: any email address creates or resumes an account.
        No password, no provider, no credentials required.
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
          {submission.isPending ? 'Signing in…' : 'Continue'}
        </button>
      </form>

      {error ? (
        <div className="notice notice--error" role="alert" data-testid="sign-in-error">
          <p>{error.message}</p>
          {error.correlationId ? (
            <p className="notice__meta">
              Correlation id: <code>{error.correlationId}</code>
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
