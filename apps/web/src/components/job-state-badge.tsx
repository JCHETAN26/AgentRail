import type { JobState } from '@agentrail/contracts';

const LABELS: Record<JobState, string> = {
  PENDING: 'Pending',
  RUNNING: 'Running',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
};

export function JobStateBadge({ state }: { state: JobState }) {
  return (
    <span className={`badge badge--${state.toLowerCase()}`} data-testid="job-state">
      {LABELS[state]}
    </span>
  );
}
