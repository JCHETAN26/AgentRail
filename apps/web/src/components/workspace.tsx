'use client';

import type { Me, Organisation, Project } from '@agentrail/contracts';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useState, type FormEvent } from 'react';

import { AgentVersionComparison } from '@/components/agent-version-comparison';
import { ApprovalQueue } from '@/components/approval-queue';
import { JobLauncher } from '@/components/job-launcher';
import { TribunalPanel } from '@/components/tribunal-panel';
import { ApiError, createOrganisation, getMe, listProjects, signOut } from '@/lib/api';

/**
 * The signed-in shell.
 *
 * Owns the tenant context — which organisation and project subsequent calls are
 * scoped to — and renders the signed-out, loading, empty and error states around
 * it. The API remains the authority on access; this component only decides what
 * to show.
 */
export function Workspace({ onSignedOut }: { onSignedOut: () => void }) {
  const queryClient = useQueryClient();
  const [organisationId, setOrganisationId] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  const me = useQuery<Me>({ queryKey: ['me'], queryFn: getMe, retry: false });

  const organisations = me.data?.organisations ?? [];
  const selected = organisationId ?? organisations[0]?.organisation.id ?? null;
  // The role in the selected organisation decides what the console offers. The
  // API stays the authority on what it permits.
  const selectedRole =
    organisations.find((membership) => membership.organisation.id === selected)?.role ?? 'viewer';

  const projects = useQuery({
    queryKey: ['projects', selected],
    queryFn: () => listProjects(selected as string),
    enabled: selected !== null,
  });

  const signOutMutation = useMutation({
    mutationFn: signOut,
    onSuccess: () => {
      queryClient.clear();
      onSignedOut();
    },
  });

  // A session that expired between renders must return the user to sign-in
  // rather than leaving a shell that silently fails every call.
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.isUnauthenticated) {
      onSignedOut();
    }
  }, [me.error, onSignedOut]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (me.isPending) {
    return (
      <p className="loading" role="status">
        Loading your workspace…
      </p>
    );
  }

  if (me.isError) {
    return <ErrorNotice error={me.error} />;
  }

  const availableProjects = projects.data?.items ?? [];
  // Defaulting to the first project is fine; being *stuck* on it is not. An
  // approval parked in any other project would otherwise be invisible, leaving
  // its run blocked with nothing in the console able to release it.
  const selectedProjectId =
    availableProjects.find((project) => project.id === projectId)?.id ??
    availableProjects[0]?.id ??
    null;

  return (
    <>
      <section className="demo-banner" aria-label="Recorded replay mode">
        <div>
          <span className="badge badge--mode">Recorded replay mode</span>
          <strong>Deterministic demo</strong>
          <span>No paid model keys required.</span>
        </div>
        <button
          className="button button--quiet"
          type="button"
          onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </section>

      <section className="demo-tour" aria-label="Guided demo tour">
        <ol>
          <li>
            <strong>Freeze</strong>
            <span>Suite evidence</span>
          </li>
          <li>
            <strong>Inspect</strong>
            <span>Trace and replay</span>
          </li>
          <li>
            <strong>Debate</strong>
            <span>Tribunal verdict</span>
          </li>
          <li>
            <strong>Gate</strong>
            <span>Canary or block</span>
          </li>
        </ol>
      </section>

      <section className="identity" data-testid="identity">
        <div>
          <span className="identity__label">Signed in as</span>{' '}
          <strong>{me.data.user?.email ?? 'service account'}</strong>
        </div>
        <button
          className="button button--quiet"
          type="button"
          onClick={() => signOutMutation.mutate()}
          disabled={signOutMutation.isPending}
        >
          Sign out
        </button>
      </section>

      {organisations.length === 0 ? (
        <CreateFirstOrganisation
          onCreated={async (organisation) => {
            setOrganisationId(organisation.id);
            await queryClient.invalidateQueries({ queryKey: ['me'] });
          }}
        />
      ) : (
        <>
          <OrganisationPicker
            organisations={organisations}
            selected={selected}
            onSelect={setOrganisationId}
          />

          {projects.isPending ? (
            <p className="loading" role="status">
              Loading projects…
            </p>
          ) : projects.isError ? (
            <ErrorNotice error={projects.error} />
          ) : selectedProjectId === null ? (
            <p className="empty" role="status">
              This organisation has no projects yet.
            </p>
          ) : (
            <>
              <ProjectPicker
                projects={availableProjects}
                selected={selectedProjectId}
                onSelect={setProjectId}
              />
              <AgentVersionComparison projectId={selectedProjectId} />
              <JobLauncher projectId={selectedProjectId} />
              <TribunalPanel />
              <ApprovalQueue projectId={selectedProjectId} role={selectedRole} />
            </>
          )}
        </>
      )}
    </>
  );
}

function OrganisationPicker({
  organisations,
  selected,
  onSelect,
}: {
  organisations: Me['organisations'];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const selectId = useId();

  if (organisations.length === 1) {
    const only = organisations[0]!;
    return (
      <p className="context" data-testid="organisation-context">
        Organisation <strong>{only.organisation.name}</strong>{' '}
        <span className="badge">{only.role}</span>
      </p>
    );
  }

  return (
    <div className="context">
      <label className="form__label" htmlFor={selectId}>
        Organisation
      </label>
      <select
        id={selectId}
        className="form__input"
        value={selected ?? ''}
        onChange={(event) => onSelect(event.target.value)}
        data-testid="organisation-picker"
      >
        {organisations.map((membership) => (
          <option key={membership.organisation.id} value={membership.organisation.id}>
            {membership.organisation.name} — {membership.role}
          </option>
        ))}
      </select>
    </div>
  );
}

function ProjectPicker({
  projects,
  selected,
  onSelect,
}: {
  projects: Project[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  const selectId = useId();

  if (projects.length === 1) {
    return (
      <p className="context" data-testid="project-context">
        Project <strong>{projects[0]!.name}</strong>
      </p>
    );
  }

  return (
    <div className="context">
      <label className="form__label" htmlFor={selectId}>
        Project
      </label>
      <select
        id={selectId}
        className="form__input"
        value={selected}
        onChange={(event) => onSelect(event.target.value)}
        data-testid="project-picker"
      >
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function CreateFirstOrganisation({
  onCreated,
}: {
  onCreated: (organisation: Organisation) => void;
}) {
  const inputId = useId();
  const [name, setName] = useState('');

  const submission = useMutation({
    mutationFn: (value: string) => createOrganisation(value),
    onSuccess: onCreated,
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submission.mutate(name.trim());
  }

  return (
    <section className="panel" aria-labelledby="first-org-heading">
      <h2 id="first-org-heading">Create your organisation</h2>
      <p className="form__hint">
        Everything in AgentRail belongs to an organisation. Creating one also creates a default
        project and makes you its owner.
      </p>
      <form className="form" onSubmit={onSubmit}>
        <label className="form__label" htmlFor={inputId}>
          Organisation name
        </label>
        <input
          id={inputId}
          className="form__input"
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <button
          className="button"
          type="submit"
          disabled={name.trim().length === 0 || submission.isPending}
        >
          {submission.isPending ? 'Creating…' : 'Create organisation'}
        </button>
      </form>
      {submission.isError ? <ErrorNotice error={submission.error} /> : null}
    </section>
  );
}

function ErrorNotice({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;

  if (apiError?.isForbidden) {
    return (
      <div className="notice notice--error" role="alert" data-testid="forbidden-notice">
        <p>You do not have access to this. Ask an administrator for a role that allows it.</p>
        {apiError.correlationId ? (
          <p className="notice__meta">
            Correlation id: <code>{apiError.correlationId}</code>
          </p>
        ) : null}
      </div>
    );
  }

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
