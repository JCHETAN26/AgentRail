# Deploy and rollback

AgentRail deploys from immutable images. The release workflow builds each Python service from the
shared pinned Dockerfile, pushes GHCR images tagged by the source commit SHA, and renders an
environment release manifest.

## Release flow

1. Merge to `main`.
2. `.github/workflows/release.yml` builds and pushes:
   - `ghcr.io/<owner>/<repo>/agentrail-api:<git-sha>`
   - `ghcr.io/<owner>/<repo>/agentrail-worker:<git-sha>`
   - `ghcr.io/<owner>/<repo>/agentrail-cloudops-sandbox:<git-sha>`
3. The `staging` environment renders a release manifest automatically on merge.
4. Production promotion is manual through `workflow_dispatch` with `environment=production`.

The workflow has `id-token: write` so it can assume the AWS OIDC role defined in
`infra/terraform/aws` once the repository variables and cloud account are wired.

## Kubernetes deployment

Render the chart with the immutable image tag from the release workflow:

```bash
helm upgrade --install agentrail infra/helm/agentrail \
  --namespace agentrail --create-namespace \
  --set global.imageNamespace=<owner>/<repo> \
  --set global.imageTag=<git-sha> \
  --set secrets.databaseUrl="$AGENTRAIL_DATABASE_URL" \
  --set secrets.redisUrl="$AGENTRAIL_REDIS_URL"
```

The chart includes:

- API, worker, and CloudOps sandbox deployments.
- A one-shot migration job using the API image.
- Non-root security contexts and dropped Linux capabilities.
- Liveness and readiness probes for every runtime container.
- Horizontal Pod Autoscalers for API and worker.
- Recorded-model fallback and quota settings for a low-cost demo environment.

## Smoke checks

After deploy:

```bash
kubectl -n agentrail rollout status deploy/agentrail-api
kubectl -n agentrail rollout status deploy/agentrail-worker
kubectl -n agentrail get job -l app.kubernetes.io/component=migration
kubectl -n agentrail port-forward svc/agentrail-api 8000:80
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

Run `make benchmark-report` before publishing resume numbers. Raw benchmark artifacts live under
`docs/benchmarks/artifacts/` and can be mirrored to the S3 bucket from `infra/terraform/aws`.

## Rollback

Rollback is always to a previous immutable SHA tag:

```bash
helm history agentrail --namespace agentrail
helm rollback agentrail <revision> --namespace agentrail
kubectl -n agentrail rollout status deploy/agentrail-api
kubectl -n agentrail rollout status deploy/agentrail-worker
```

If a migration caused the rollback, stop traffic first and run a hand-authored corrective migration.
Do not run Alembic downgrade in production unless the migration was explicitly written and rehearsed
as reversible.

Record the rollback reason in the deployment history UI or, until that UI is connected to production,
in the incident runbook timeline.

## Codespaces cost control

When the final PR is merged and verification is complete:

```bash
gh codespace list
gh codespace stop --codespace <name>
```

Leave a Codespace running only while a CI failure is being actively debugged.
