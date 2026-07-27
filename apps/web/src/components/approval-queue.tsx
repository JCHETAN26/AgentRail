'use client';

import type { Approval, Role } from '@agentrail/contracts';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';

import { ApiError, decideApproval, listProjectApprovals } from '@/lib/api';

/**
 * The reviewer's queue.
 *
 * A run that reaches a high-risk tool call stops and waits here. Nothing on
 * this screen executes anything — approving puts the item back in the queue for
 * a worker, and the worker re-checks the decision before acting.
 *
 * The role decides what is *shown*; the API decides what is *allowed*. A viewer
 * who reaches the decision endpoint anyway gets a 403, and the notice below
 * says so rather than pretending the click did nothing.
 */

/** Roles that may decide, mirroring `approval:decide` in the permission matrix. */
const DECIDING_ROLES: readonly Role[] = ['reviewer', 'developer', 'admin', 'owner'];

/** How often to look for newly parked approvals. A blocked run is waiting. */
const APPROVAL_POLL_MS = 5_000;

/**
 * True for a plain JSON object — not an array, not `null`, not a primitive.
 *
 * `JSON.parse` accepts all of those happily, and casting the result would send
 * the API something its contract does not describe, producing exactly the `422`
 * this parsing exists to prevent.
 */
function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function ApprovalQueue({ projectId, role }: { projectId: string; role: Role }) {
  const approvals = useQuery({
    queryKey: ['approvals', projectId, 'PENDING'],
    queryFn: () => listProjectApprovals(projectId, 'PENDING'),
    // A run parks *while* a reviewer is looking at this screen — that is the
    // normal case, not the exception. The app-wide client disables
    // refetch-on-focus, and an empty queue is never invalidated by anything, so
    // without a poll the panel would keep saying nothing is waiting while a run
    // sits blocked behind it.
    refetchInterval: APPROVAL_POLL_MS,
  });

  const canDecide = DECIDING_ROLES.includes(role);

  if (approvals.isPending) {
    return (
      <section className="panel" aria-labelledby="approvals-heading">
        <h2 id="approvals-heading">Approvals</h2>
        <p className="loading" role="status">
          Loading approvals…
        </p>
      </section>
    );
  }

  if (approvals.isError) {
    return (
      <section className="panel" aria-labelledby="approvals-heading">
        <h2 id="approvals-heading">Approvals</h2>
        <ApprovalError error={approvals.error} />
      </section>
    );
  }

  // Defended rather than assumed: this panel renders inside the signed-in
  // shell, so a response without `items` would otherwise throw during render
  // and take the whole workspace — sign-out included — down with it.
  const pending = approvals.data.items ?? [];

  return (
    <section className="panel" aria-labelledby="approvals-heading" data-testid="approval-queue">
      <h2 id="approvals-heading">Approvals</h2>
      {pending.length === 0 ? (
        <p className="empty" role="status" data-testid="approvals-empty">
          Nothing is waiting on you. High-risk tool calls appear here before they run.
        </p>
      ) : (
        <ul className="approvals">
          {pending.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              projectId={projectId}
              canDecide={canDecide}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ApprovalCard({
  approval,
  projectId,
  canDecide,
}: {
  approval: Approval;
  projectId: string;
  canDecide: boolean;
}) {
  const queryClient = useQueryClient();
  const reasonId = useId();
  const editId = useId();
  const [reason, setReason] = useState('');
  const [edited, setEdited] = useState('');
  const [editError, setEditError] = useState<string | null>(null);

  const decision = useMutation({
    mutationFn: (body: Parameters<typeof decideApproval>[1]) => decideApproval(approval.id, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['approvals', projectId, 'PENDING'] });
    },
  });

  function submit(approve: boolean) {
    setEditError(null);
    let editedArguments: Record<string, unknown> | undefined;
    if (approve && edited.trim().length > 0) {
      let parsed: unknown;
      try {
        // Parsed here so a typo is a message next to the field rather than a
        // 422 from the server after the reviewer has already committed.
        parsed = JSON.parse(edited);
      } catch {
        setEditError('Edited arguments must be valid JSON.');
        return;
      }
      if (!isJsonObject(parsed)) {
        setEditError('Edited arguments must be a JSON object, not an array or a bare value.');
        return;
      }
      editedArguments = parsed;
    }
    decision.mutate({
      approve,
      reason: reason.trim().length > 0 ? reason.trim() : null,
      edited_arguments: editedArguments ?? null,
    });
  }

  return (
    <li className="approval" data-testid="approval-card">
      <header className="approval__header">
        <code className="approval__tool">{approval.tool}</code>
        <span className="badge badge--risk" data-testid="approval-risk">
          {approval.risk_level}
        </span>
      </header>

      <p className="approval__meta">
        Run <code>{approval.run_id}</code>
      </p>

      <details className="approval__arguments">
        <summary>Arguments</summary>
        {/* Already redacted server-side before storage — sensitive values never
            reach this component to begin with. */}
        <pre>{JSON.stringify(approval.requested_arguments, null, 2)}</pre>
      </details>

      {canDecide ? (
        <div className="approval__decision">
          <label className="form__label" htmlFor={reasonId}>
            Reason
          </label>
          <input
            id={reasonId}
            className="form__input"
            value={reason}
            maxLength={1024}
            placeholder="Why you are approving or rejecting"
            onChange={(event) => setReason(event.target.value)}
          />

          <label className="form__label" htmlFor={editId}>
            Edited arguments (optional JSON)
          </label>
          <textarea
            id={editId}
            className="form__input"
            rows={3}
            value={edited}
            placeholder="Leave empty to run exactly what was requested"
            onChange={(event) => setEdited(event.target.value)}
          />
          {editError ? (
            <p className="notice notice--error" role="alert">
              {editError}
            </p>
          ) : null}

          <div className="approval__actions">
            <button
              className="button"
              type="button"
              disabled={decision.isPending}
              onClick={() => submit(true)}
              data-testid="approve-button"
            >
              {decision.isPending ? 'Submitting…' : 'Approve'}
            </button>
            <button
              className="button button--quiet"
              type="button"
              disabled={decision.isPending || edited.trim().length > 0}
              onClick={() => submit(false)}
              data-testid="reject-button"
            >
              Reject
            </button>
          </div>
          {edited.trim().length > 0 ? (
            <p className="form__hint">
              Clear the edited arguments to reject — there is nothing to edit about an action that
              will not run.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="form__hint" data-testid="approval-readonly">
          Your role can see this request but not decide it. Ask a reviewer.
        </p>
      )}

      {decision.isError ? <ApprovalError error={decision.error} /> : null}
    </li>
  );
}

function ApprovalError({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;

  if (apiError?.status === 409) {
    return (
      <div className="notice notice--error" role="alert" data-testid="approval-conflict">
        <p>
          Somebody already decided this one. Refresh to see the answer that was recorded — a
          decision cannot be overturned.
        </p>
      </div>
    );
  }

  if (apiError?.isForbidden) {
    return (
      <div className="notice notice--error" role="alert" data-testid="approval-forbidden">
        <p>Your role may not decide approvals. Ask an administrator for the reviewer role.</p>
      </div>
    );
  }

  return (
    <div className="notice notice--error" role="alert" data-testid="approval-error">
      <p>{apiError?.message ?? 'Something went wrong.'}</p>
      {apiError?.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{apiError.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
