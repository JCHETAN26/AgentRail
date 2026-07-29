You are the senior staff engineer responsible for shipping AgentRail: a full-stack AI developer platform for evaluating, debugging, governing, and safely releasing tool-using AI agents.

AgentRail's flagship feature is the Multi-Agent Safety Tribunal — a panel of 6 specialized evaluator agents (Prosecutor, Defender, Auditor, Economist, Historian, Judge) that debate candidate agent safety via a shared blackboard before rendering a binding verdict.

The platform must feel like something an Apple Cloud AI Platform team would use internally: polished React workflows, Python backend services, LangGraph multi-agent orchestration, model evaluation, inference integration, telemetry, CI/CD, and production-minded reliability.

ENGINEERING STANDARDS:

- Every feature ships with tests (unit, integration, or e2e).
- No direct pushes to main. All work via PR.
- TypeScript strict mode everywhere. Python uses Pydantic + mypy.
- No secrets in code. No TODOs in merged code.
- Every feature must be demoable and documented.

Use this checklist to track progress. Check an item only when it is implemented, tested, and merged to main via PR.

---

# PHASE 0 — FOUNDATION & REPO SETUP

## Repository & Tooling

- [ ] Monorepo scaffolded with pnpm workspaces (apps/web, services/api, services/worker, services/tribunal, services/cloudops-sandbox, packages/\*)
- [x] Root Makefile with targets: verify, lint, type-check, test, test-integration, benchmark-\*
- [ ] Strict linting configured (ESLint + Prettier for TS, ruff + black for Python)
- [x] Type checking configured (tsc --noEmit for TS, mypy for Python)
- [ ] GitHub Actions CI skeleton with path-filtered jobs
- [x] Branch protection configured (require PR, 1 approval, required checks, no force push)
- [ ] Conventional commits enforced (husky + lint-staged)
- [x] OpenAPI seed spec generated
- [ ] Docker Compose local infra (Postgres 16, Redis, MinIO, Redpanda/Kafka)
- [x] Health and readiness endpoints for all services
- [x] OpenTelemetry correlation ID propagation wired
- [ ] ADR template and first 3 ADRs written (repo structure, auth strategy, why LangGraph)
- [x] docs/CHECKPOINT.md and docs/BRANCH_PROTECTION.md created

---

# PHASE 1 — AUTHENTICATION & TENANCY

## Identity

- [x] OAuth 2.0 browser sign-in (GitHub or Google)
- [x] User model with email, name, avatar
- [x] Organisation model with slug and display name
- [x] Membership model with roles: owner, admin, developer, reviewer, viewer
- [x] Role-based access control (RBAC) central policy functions
- [x] API key generation with scope bounding (read, write, admin)
- [x] API key stored as bcrypt hash only (never plaintext)
- [x] API key validation with constant-time comparison
- [x] Secure dev mode (deterministic identity for local development)
- [x] Audit log foundation (who did what, when)

## Tests

- [x] Unit tests for RBAC matrix (every role × every action)
- [x] Integration tests: cross-tenant access is blocked
- [x] Integration tests: revoked API key returns 401

---

# PHASE 2 — CLOUDOPS SANDBOX & REFERENCE AGENT

## Synthetic Environment

- [x] 10 synthetic tools implemented with JSON schemas:
  - get_service_health, query_metrics, search_logs, get_dependency_graph
  - get_runbook, restart_service, scale_service, create_incident
  - notify_oncall, escalate_to_human
- [x] Tool risk classification: READ_ONLY, LOW_RISK_WRITE, HIGH_RISK_WRITE, PROHIBITED
- [x] Idempotent side-effect tracking (idempotency_key deduplication)
- [x] Synthetic incident families (16 scenarios minimum):
  - DB pool exhaustion, Kafka lag, rate limiting, expired credential
  - Memory leak, CPU saturation, stale cache, DNS failure
  - Dependency timeout, misconfigured autoscaling, healthy service + misleading logs
  - Conflicting metrics/logs, prompt injection in logs, remediation requiring approval
  - Duplicate job delivery, worker failure during side effect
- [x] Ground truth definitions per scenario (expected diagnosis, allowed tools, forbidden tools, expected args, approval required)
- [x] Sandbox reset/seed command
- [x] Deterministic rule-based CloudOps adapter for CI (no LLM required)

## Tests

- [x] Unit tests for every tool contract
- [x] Integration tests: duplicate idempotency_key returns original result
- [x] Integration tests: forbidden tools are rejected

---

# PHASE 3 — AGENT REGISTRY & VERSIONING

## Agent Management

- [x] AgentDefinition model (stable logical identity)
- [x] AgentVersion model (immutable: graph, prompts, model config, tools, policy, source commit, content digest)
- [x] ToolContract model (JSON schema, risk level, side-effect class, timeout, retry, approval policy)
- [x] PolicyBundle model (immutable rules)
- [x] Framework adapter interface (LangGraph adapter, deterministic adapter, recorded adapter)
- [x] Version digest computation (SHA-256 of serialized config)
- [x] Version comparison UI (diff viewer for prompt changes)

## Tests

- [x] Unit tests: versions are immutable after creation
- [x] Unit tests: digest changes when any field changes
- [ ] Integration tests: adapter interface compliance

---

# PHASE 4 — DATASETS & EVALUATION SUITES

## Data Management

- [x] Dataset upload (JSONL, CSV)
- [x] Schema validation with rejection report
- [x] DatasetVersion model (immutable storage URI, digest, schema, source, count)
- [x] EvaluationSuite model (dataset, evaluators, thresholds, fault profiles, frozen timestamp)
- [x] Suite freeze operation (immutable after freeze)
- [x] **Tribunal config in suite** (enabled: bool, required: bool, model: str)
- [ ] Evaluator selection UI (programmatic + tribunal)
- [x] Threshold and release policy configuration
- [ ] Preview UI for suite contents

## Tests

- [x] Unit tests: malformed JSONL rejected with actionable errors
- [x] Integration tests: frozen suite cannot be modified
- [x] Integration tests: tribunal config persists with suite

---

# PHASE 5 — DISTRIBUTED EXECUTION ENGINE

## Job Processing

- [x] EvaluationRun state machine (CREATED → VALIDATING → QUEUING → RUNNING → AGGREGATING → PASSED/FAILED/CANCELLED/ERROR)
- [x] RunItem state machine (PENDING → LEASED → EXECUTING → EVALUATING → COMPLETED/FAILED_RETRYABLE/FAILED_TERMINAL/CANCELLED)
- [x] Transactional run creation with idempotency key
- [x] PostgreSQL transactional outbox pattern
- [x] Redis-backed task delivery (at-least-once)
- [x] Worker lease semantics (timeout, renewal, expiry)
- [x] Cancellation support (cancel propagation to workers)
- [x] Retry budgets (max attempts, backoff strategy)
- [ ] PostgreSQL checkpointer for LangGraph (durable checkpoints)
- [x] SSE progress streaming to dashboard
- [x] Graceful worker shutdown (finish in-flight, reject new)

## Tests

- [ ] Integration tests: 100-item suite completes end-to-end
- [ ] Integration tests: killed worker recovers and resumes
- [x] Integration tests: duplicate task delivery is harmless (idempotent)
- [x] Integration tests: cancellation stops run within 5 seconds

---

# PHASE 6 — TRAJECTORY CAPTURE & TRACE EXPLORER

## Observability

- [x] Trajectory schema (run_id, steps, timestamps, state snapshots)
- [x] TrajectoryStep schema (node name, input, output, tool calls, latency, tokens)
- [ ] Event capture hook in LangGraph runtime
- [x] Automatic redaction of secrets in traces
- [ ] Timeline visualization (step-by-step execution flow)
- [ ] Graph state inspector (state at each step)
- [ ] Tool call inspector (arguments, response, latency)
- [ ] Evidence viewer (RAG contexts, rationales)
- [x] Checkpoint listing per trajectory
- [x] Tenant isolation in trace storage

## Tests

- [x] Unit tests: redaction removes API keys and secrets
- [x] Integration tests: tenant A cannot read tenant B trajectories
- [ ] E2E tests: every failed item links to exact failing step

---

# PHASE 7 — PROGRAMMATIC EVALUATORS

## Evaluation Logic

- [x] Outcome evaluator (did agent reach expected final state?)
- [x] Diagnosis evaluator (did agent identify correct root cause?)
- [x] Tool selection evaluator (did agent call correct tools?)
- [x] Tool arguments evaluator (were arguments correct and complete?)
- [x] Evidence support evaluator (is rationale supported by retrieved evidence?)
- [x] Policy compliance evaluator (did agent violate any policy rules?)
- [x] Side-effect correctness evaluator (were side effects correct?)
- [x] Loop/latency/token/cost budget evaluator (did agent stay within budgets?)
- [x] Evaluator versioning (code + config digest stored per run)
- [x] Aggregation logic (roll up per-case scores to run-level metrics)
- [ ] Comparison UI (baseline vs. candidate delta)
- [x] Regression filtering (show only worsened metrics)
- [x] Category breakdown (per incident family metrics)

## Tests

- [x] Unit tests for every evaluator on known cases
- [x] Integration tests: evaluator results are reproducible
- [x] Property tests: errors remain in denominators (no hidden failures)

---

# PHASE 8 — MULTI-AGENT SAFETY TRIBUNAL ★ FLAGSHIP FEATURE

## Tribunal Architecture

- [x] TribunalSession model (linked to EvaluationRun, config, state)
- [x] TribunalRound model (EVIDENCE | DEBATE | VERDICT)
- [x] Blackboard schema:
  - findings[]: agent, type (safety|accuracy|cost|drift|policy), severity (info|warning|critical|blocker), evidence, confidence
  - arguments[]: agent, position (for|against|neutral), target_finding_id, evidence
  - verdict: status (approved|blocked|conditional), primary_reason, dissent, required_actions[], confidence
- [x] PostgreSQL schema for tribunal_sessions, tribunal_findings, tribunal_arguments, tribunal_verdicts
- [x] Tribunal state machine: TRIBUNAL_QUEUED → TRIBUNAL_EVIDENCE → TRIBUNAL_DEBATE → TRIBUNAL_VERDICT → PUBLISHED

## Specialist Agents (LangGraph Nodes)

- [x] **Prosecutor agent**: Hunts for failure modes, adversarial bias, outputs list[Finding] with severity warning/blocker
- [x] **Defender agent**: Contextualizes failures, optimistic bias, outputs rebuttal Findings
- [x] **Auditor agent**: Checks policy compliance, literal bias, outputs policy Findings
- [x] **Economist agent**: Analyzes cost and latency, efficiency bias, outputs cost Findings
- [x] **Historian agent**: Compares against baseline, conservative bias, outputs drift Findings
- [x] **Judge agent**: Synthesizes verdict, neutral bias, outputs Verdict struct

## Tribunal Execution Flow

- [x] Round 1 — Evidence: Prosecutor, Auditor, Economist, Historian run in parallel, write findings to blackboard
- [x] Round 2 — Debate: Defender reads all findings, writes rebuttals; Prosecutor may write counter-rebuttals (one iteration)
- [x] Round 3 — Verdict: Judge reads full blackboard, renders structured Verdict
- [ ] Round 4 — Gate integration:
  - blocked → run status FAILED, release gate blocks regardless of other metrics
  - conditional → run status PASSED with warnings, release gate requires human approval
  - approved → run status follows programmatic evaluator logic

## Tribunal Safety Invariants

- [x] Auditor blocker-level findings always override Defender approval in final verdict
- [x] Tribunal agents cannot share state across evaluation runs (blackboard scoped to run_id)
- [x] Evidence text is sandboxed; no user text enters system prompts
- [x] Tribunal prompts are versioned and content-addressed

## Tribunal Dashboard UI

- [x] Tribunal tab in run detail view
- [x] Blackboard timeline (who said what, when)
- [ ] Finding severity heatmap (by agent and type)
- [x] Verdict card with primary reason and dissent visible
- [ ] Evidence links from findings to specific trajectory steps
- [x] Argument thread view (rebuttals and counter-rebuttals)

## Tribunal Failure Injection

- [ ] Prosecutor over-flagging bias injection test
- [ ] Defender under-flagging bias injection test
- [x] Judge ignoring Auditor blockers (override logic test)
- [x] Tribunal model timeout during debate

## Tests

- [ ] Integration tests: 16-scenario suite runs through Tribunal in under 3 minutes
- [x] Integration tests: Prosecutor and Defender produce genuinely conflicting findings on at least one scenario
- [x] Integration tests: Auditor blocker always overrides Defender approval
- [x] Integration tests: verdict is reproducible (same trajectories + same prompts → same verdict within LLM variance)
- [x] Property tests: blocked verdict always blocks release gate
- [ ] E2E tests: full tribunal visible in dashboard with evidence links

---

# PHASE 9 — REPLAY & TIME-TRAVEL DEBUGGING

## Replay Modes

- [x] Recorded replay (deterministic, safe, CI and public demo)
- [x] Live replay (repeats model calls, not original side effects)
- [x] Forked replay (begins at checkpoint, changes prompt/model/tool response)
- [x] **Tribunal forked replay** (begins at Round 2 with different Defender prompt/model, observes Judge verdict change)
- [x] Checkpoint restoration from PostgreSQL
- [x] Divergence detection (replay differs from original)
- [x] Replay safety (no duplicate side effects)

## Tests

- [x] Integration tests: deterministic replay reproduces original exactly
- [x] Integration tests: fork shows divergence in trajectory
- [ ] Integration tests: tribunal fork shows verdict change
- [x] Integration tests: no side effect repeats during replay

---

# PHASE 10 — FAILURE INJECTION & RELIABILITY

## Fault Profiles

- [x] Model faults: timeout, rate limit, malformed output, refusal, wrong tool, invalid args, tool loop, partial stream failure
- [x] Tool faults: latency, timeout, 500, malformed response, stale data, rate limit, unavailable dependency
- [ ] Platform faults: duplicate delivery, delayed event, worker termination, lease expiry, Redis restart, Postgres transient error, object-store failure, analytics outage
- [ ] **Tribunal faults**: Prosecutor over-flagging, Judge ignoring Auditor, tribunal model timeout

## Resilience

- [x] Circuit breaker for model provider calls
- [x] Retry budgets with exponential backoff
- [ ] Recovery view in dashboard (what failed, how it recovered)
- [x] Chaos commands for manual fault injection

## Tests

- [x] Integration tests: zero duplicate side effects under forced failure
- [x] Integration tests: correct state recovery after worker termination
- [ ] Integration tests: tribunal remains consistent under bias injection

---

# PHASE 11 — POLICY ENGINE & HUMAN APPROVAL

## Policy System

- [x] PolicyBundle with declarative YAML/JSON rules
- [x] Tool risk levels: READ_ONLY, LOW_RISK_WRITE, HIGH_RISK_WRITE, PROHIBITED
- [x] Pre-execution policy interception (tool call blocked before execution)
- [x] Rule pattern matching (tool name, target pattern, argument conditions)
- [ ] Escalation chains (block after N attempts)
- [x] **Tribunal verdicts as policy inputs**:
  - blocked = policy violation
  - conditional = requires human review
- [ ] LangGraph interrupt for high-risk tool calls
- [x] Human approval workflow: approve, edit, reject
- [x] Persistent checkpoint resume after approval decision
- [x] Audit log for all policy, approval, tribunal, and release decisions

## Tests

- [x] Integration tests: high-risk tool cannot execute without approval
- [x] Integration tests: delayed event cannot bypass rejection
- [ ] Integration tests: tribunal blocked always blocks execution
- [ ] E2E tests: full approve → resume → complete flow

---

# PHASE 12 — RELEASE GATES & GITHUB INTEGRATION

## Release System

- [x] ReleasePolicy model (thresholds, required evaluators, tribunal requirement)
- [x] Offline gate (checks before any external call)
- [ ] GitHub App installation flow
- [x] Webhook verification (HMAC signature)
- [x] GitHub Check Run creation from evaluation
- [x] PR annotations with deep links to failed trajectories
- [x] **GitHub Check annotation includes tribunal verdict summary**:
  - Verdict status, primary reason, dissent, required actions
- [x] Superseded-run cancellation (new commit cancels old run)
- [x] Sample GitHub Actions workflow in docs

## Release Gate Logic

- [x] Gate can reference: task success, tool correctness, policy violations, latency, cost, tribunal verdict status, tribunal confidence
- [x] Configurable thresholds per gate
- [ ] Gate passes → merge allowed
- [x] Gate fails → merge blocked with actionable feedback

## Tests

- [x] Integration tests: regressed PR is blocked
- [x] Integration tests: passing PR succeeds
- [x] Integration tests: tribunal summary visible in GitHub Check
- [ ] E2E tests: full PR → eval → Check → merge/merge-blocked flow

---

# PHASE 13 — CANARY DEPLOYMENT & ROLLBACK

## Deployment Model

- [x] Deployment model (DRAFT → OFFLINE_GATE → APPROVAL_PENDING → CANARY → PROMOTED/ROLLED_BACK/FAILED)
- [x] Traffic simulator (configurable percentage to candidate)
- [x] Replay workload on canary
- [x] Canary metrics collection (success rate, latency, error rate, cost)
- [x] Promotion workflow (manual approval)
- [x] Automatic rollback on degradation
- [x] Rollback reason preservation
- [ ] Release history UI

## Tests

- [x] Integration tests: healthy candidate promotes
- [x] Integration tests: degraded candidate rolls back automatically
- [x] Integration tests: rollback reason preserved in history

---

# PHASE 14 — OBSERVABILITY, SLOs & OPERATIONS

## Telemetry

- [ ] OpenTelemetry SDK in web, API, workers, tribunal
- [x] OpenTelemetry Collector in Docker Compose
- [x] Prometheus-compatible metrics endpoint
- [x] Grafana dashboards:
  - Request rate/errors/latency
  - Active/queued runs
  - Worker utilization and lease expiry
  - Queue wait and outbox lag
  - Task success and diagnosis accuracy
  - Tool-selection F1 and argument accuracy
  - Policy violations and loop rate
  - Token usage and cost per successful task
  - **Tribunal round duration, finding count, verdict status, override rate**
  - Canary deltas and rollback reason
- [ ] Tempo-compatible traces
- [x] Structured JSON logging with correlation IDs
- [x] Correlation ID visible in UI error states

## SLOs

- [x] Documented SLOs for: API availability, eval completion time, tribunal duration, worker recovery time
- [x] Alerting rules for SLO breaches

## Tests

- [x] Integration tests: incident traced end-to-end via correlation ID
- [ ] Load tests: dashboard queries remain responsive under load

---

# PHASE 15 — SECURITY & SUPPLY CHAIN

## Security Hardening

- [x] Threat model document completed
- [x] PostgreSQL RLS as defence-in-depth
- [x] Request quotas and rate limiting
- [x] Data retention policies enforced
- [x] Redaction of sensitive data in logs and traces
- [x] Content Security Policy (CSP) headers
- [x] GitHub webhook replay defence
- [x] Uploaded file validation (size, type, content scan)
- [x] **Tribunal prompt-injection defence** (evidence sandboxing, no user text in system prompts)
- [x] Cross-tenant access testing across all surfaces

## Supply Chain

- [x] Dependabot enabled
- [x] Dependency review in CI
- [x] CodeQL analysis
- [x] Secret scanning
- [x] Container scanning (Trivy or similar)
- [x] SBOM generation
- [x] Image provenance (GitHub attestations)
- [x] Immutable action pins in workflows
- [x] Non-root container images

## Tests

- [x] Security suite passes in CI
- [x] Penetration tests: cross-tenant isolation holds
- [x] Penetration tests: tribunal evidence cannot manipulate system prompts

---

# PHASE 16 — PERFORMANCE & ANALYTICAL SCALE

## Optimization

- [ ] Load generators for run creation, concurrent items, SSE progress
- [ ] Profiling and bottleneck identification
- [ ] Database index tuning
- [ ] Connection pooling optimization
- [ ] Optional ClickHouse introduction (derived analytics store)
- [ ] PostgreSQL → ClickHouse projector/reconciliation
- [x] Autoscaling configuration (KEDA or HPA)
- [ ] Backpressure handling
- [x] Hardware metadata capture in benchmark reports

## Tribunal Benchmark

- [x] `make benchmark-tribunal` command
- [x] Measures: duration overhead, verdict agreement with programmatic evaluators, false-block rate, false-approve rate, consensus rate
- [x] Report generated with confidence intervals

## Tests

- [ ] Load tests: system stable at target concurrency
- [x] Benchmark tests: reproducible results across runs

---

# PHASE 17 — CLOUD DEPLOYMENT & CI/CD

## Containers

- [x] Multi-stage Dockerfiles for all services
- [x] Non-root user in all runtime images
- [x] Health checks in all containers
- [ ] Image size optimization (<150MB for C++ if applicable)

## Orchestration

- [x] Helm charts for Kubernetes deployment
- [x] Terraform for one cloud provider (AWS or GCP)
- [x] GitHub OIDC to cloud provider
- [x] GitHub Container Registry (GHCR) push
- [x] Immutable image tags by SHA

## Environments

- [ ] Staging environment (auto-deploy on merge)
- [ ] Production environment (manual approval required)
- [x] Migration jobs as init containers or Kubernetes jobs
- [ ] Rollback procedure documented and tested
- [x] Demo environment with quotas and recorded-model fallback

## Tests

- [ ] Integration tests: merge deploys staging successfully
- [ ] Integration tests: production promotion uses immutable digest
- [ ] Integration tests: failed smoke test triggers rollback
- [ ] E2E tests: public demo reachable and functional

---

# PHASE 18 — FROZEN BENCHMARK & PUBLIC EVIDENCE

## Benchmark Suite

- [x] 300+ frozen scenarios (immutable, never tuned against)
- [x] 150+ failure-injected runs
- [x] 100+ tribunal-enabled scenarios
- [x] Runner script with deterministic seed
- [x] Confidence intervals for all metrics
- [x] Confusion matrices per incident family
- [x] Release-gate precision/recall/false-block metrics
- [x] **Tribunal quality metrics**: duration overhead, verdict agreement, false-block rate, false-approve rate, consensus rate
- [ ] Raw artefacts stored in S3/MinIO
- [x] Git commit, model versions, evaluator versions, tribunal prompt versions documented per run

## Evidence

- [x] `docs/benchmarks/RESUME_METRICS.md` with verified numbers
- [x] Every metric links to raw artefact
- [x] No frozen-test tuning (honest evaluation)

---

# PHASE 19 — PRODUCT POLISH & PORTFOLIO RELEASE

## UI Polish

- [x] Onboarding flow for new users
- [ ] Seeded organisation with demo data
- [x] Guided demo tour
- [x] Responsive design (mobile + desktop)
- [ ] Accessibility audit (WCAG 2.1 AA minimum)
- [x] Dark mode support

## Documentation

- [x] Product requirements document
- [x] Architecture overview with diagrams
- [x] API documentation (generated from OpenAPI)
- [x] ER diagram
- [x] Threat model
- [x] Retention policy
- [x] Evaluation methodology
- [x] Tribunal design and prompt versioning guide
- [x] Test strategy
- [x] SLO document
- [x] Deploy and rollback guides
- [x] Incident runbook
- [x] Contributor guide
- [x] 5+ ADRs completed
- [x] Synthetic incident postmortem
- [x] Technical deep dive blog post outline

## Demo

- [x] 5-minute demo script rehearsed
- [ ] 2-minute Loom/video recorded
- [x] Demo works in recorded mode without paid API keys
- [x] Replay-mode banner visible in demo

## Public Presence

- [x] README with architecture diagram, quickstart, and benchmark numbers
- [x] `RESUME_METRICS.md` with honest, verified claims
- [ ] Screenshots of dashboard, tribunal, and trace explorer
- [x] Release notes for v1.0.0

---

# DEFINITION OF DONE (GLOBAL)

AgentRail is complete only when:

- [ ] It is deployed to a public URL
- [ ] The UI is polished and accessible
- [x] TypeScript and Python codebases are substantial and well-tested
- [ ] Evaluation, multi-agent tribunal, replay, failure injection, approval, GitHub Checks, canary rollback, and OpenTelemetry all work
- [x] CI/CD is functional and green
- [x] Every change arrived through a PR
- [x] Benchmark metrics come from a frozen test suite
- [x] Tribunal debate is reproducible
- [x] The deterministic demo needs no paid API key
- [x] A recruiter can understand the value in 30 seconds
- [ ] An engineer can run the demo, reproduce the benchmark, inspect a failure, and verify tribunal evidence

---

Track progress by checking items off as they are implemented, tested, and merged. Do not check an item until the corresponding PR is merged to main and CI is green.
