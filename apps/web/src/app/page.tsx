'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';

import { SignIn } from '@/components/sign-in';
import { Workspace } from '@/components/workspace';
import { ApiError, getMe } from '@/lib/api';

type SessionState = 'checking' | 'signed-in' | 'signed-out';

export default function HomePage() {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<SessionState>('checking');

  const check = useCallback(async () => {
    try {
      await getMe();
      setSession('signed-in');
    } catch (error) {
      // Anything other than "not signed in" still lands on the sign-in screen —
      // there is nothing useful to show without an identity.
      if (!(error instanceof ApiError) || error.isUnauthenticated) {
        setSession('signed-out');
        return;
      }
      setSession('signed-out');
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const onSignedIn = useCallback(async () => {
    await queryClient.invalidateQueries();
    setSession('signed-in');
  }, [queryClient]);

  const onSignedOut = useCallback(() => setSession('signed-out'), []);

  return (
    <>
      <section className="intro">
        <h1>Deterministic request path</h1>
        <p>
          Submitting a job writes a row to PostgreSQL, publishes its identifier to Redis, and a
          worker executes it against the CloudOps sandbox. No model provider is involved, so the
          result is byte-identical on every run.
        </p>
      </section>

      {session === 'checking' ? (
        <p className="loading" role="status">
          Checking your session…
        </p>
      ) : session === 'signed-out' ? (
        <SignIn onSignedIn={onSignedIn} />
      ) : (
        <Workspace onSignedOut={onSignedOut} />
      )}
    </>
  );
}
