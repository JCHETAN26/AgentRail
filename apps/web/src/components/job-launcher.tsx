'use client';

import { isTerminalJobState, type Job } from '@agentrail/contracts';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useId, useState, type FormEvent } from 'react';

import { ApiError, createJob, getJob } from '@/lib/api';
import { JobStateBadge } from '@/components/job-state-badge';

const POLL_INTERVAL_MS = 500;
const MAX_MESSAGE_LENGTH = 500;

export function JobLauncher({ projectId }: { projectId: string }) {
  const inputId = useId();
  const [message, setMessage] = useState('hello from the console');
  const [jobId, setJobId] = useState<string | null>(null);

  const submission = useMutation({
    mutationFn: (value: string) => createJob(projectId, { kind: 'noop', message: value }),
    onSuccess: (job: Job) => setJobId(job.id),
  });

  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId as string),
    enabled: jobId !== null,
    // Stop polling as soon as the job can no longer change.
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state !== undefined && isTerminalJobState(state) ? false : POLL_INTERVAL_MS;
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setJobId(null);
    submission.mutate(message);
  }

  const trimmed = message.trim();
  const canSubmit = trimmed.length > 0 && trimmed.length <= MAX_MESSAGE_LENGTH;

  return (
    <section className="panel" aria-labelledby="launcher-heading">
      <h2 id="launcher-heading">Run a job</h2>

      <form className="form" onSubmit={onSubmit}>
        <label className="form__label" htmlFor={inputId}>
          Message
        </label>
        <input
          id={inputId}
          className="form__input"
          value={message}
          maxLength={MAX_MESSAGE_LENGTH}
          onChange={(event) => setMessage(event.target.value)}
          aria-describedby={`${inputId}-hint`}
        />
        <p className="form__hint" id={`${inputId}-hint`}>
          The sandbox echoes this back with a SHA-256 digest, proving the payload survived every hop
          unmodified.
        </p>
        <button className="button" type="submit" disabled={!canSubmit || submission.isPending}>
          {submission.isPending ? 'Submitting…' : 'Submit job'}
        </button>
      </form>

      {submission.isError ? <ErrorNotice error={submission.error} /> : null}

      {jobId === null && !submission.isPending && !submission.isError ? (
        <p className="empty" role="status">
          No job yet. Submit one to see the full path execute.
        </p>
      ) : null}

      {jobId !== null ? <JobResult jobId={jobId} query={job} /> : null}
    </section>
  );
}

function JobResult({ jobId, query }: { jobId: string; query: ReturnType<typeof useQuery<Job>> }) {
  if (query.isError) {
    return <ErrorNotice error={query.error} />;
  }

  const job = query.data;
  if (job === undefined) {
    return (
      <p className="loading" role="status">
        Loading job {jobId}…
      </p>
    );
  }

  return (
    <div className="result" data-testid="job-result">
      <dl className="result__grid">
        <div>
          <dt>Job</dt>
          <dd>
            <code>{job.id}</code>
          </dd>
        </div>
        <div>
          <dt>State</dt>
          <dd>
            <JobStateBadge state={job.state} />
          </dd>
        </div>
        <div>
          <dt>Attempts</dt>
          <dd>{job.attempts}</dd>
        </div>
        <div>
          <dt>Correlation id</dt>
          <dd>
            <code>{job.correlation_id}</code>
          </dd>
        </div>
      </dl>

      {job.state === 'COMPLETED' && job.result !== null ? (
        <pre className="result__payload" data-testid="job-result-payload">
          {JSON.stringify(job.result, null, 2)}
        </pre>
      ) : null}

      {job.state === 'FAILED' ? (
        <p className="notice notice--error" role="alert">
          {job.error_message ?? 'The job failed.'} ({job.error_code})
        </p>
      ) : null}

      {!isTerminalJobState(job.state) ? (
        <p className="loading" role="status">
          Waiting for a worker to finish this job…
        </p>
      ) : null}
    </div>
  );
}

function ErrorNotice({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;

  return (
    <div className="notice notice--error" role="alert" data-testid="error-notice">
      <p>{apiError?.message ?? 'Something went wrong.'}</p>
      {apiError?.correlationId ? (
        <p className="notice__meta">
          Correlation id: <code>{apiError.correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
