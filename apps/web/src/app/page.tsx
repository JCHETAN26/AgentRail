import { JobLauncher } from '@/components/job-launcher';

export default function HomePage() {
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
      <JobLauncher />
    </>
  );
}
