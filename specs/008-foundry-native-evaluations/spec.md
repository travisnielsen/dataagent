# Feature Specification: Foundry-Native Multi-Layer Evaluations

**Feature Branch**: `008-foundry-native-evaluations` *(to be created)*
**Created**: 2026-05-17 (rescoped 2026-05-25)
**Status**: Draft
**Depends on**: [007-foundry-framework-upgrade](../007-foundry-framework-upgrade/spec.md) — must be merged and producing chat traces tagged with `gen_ai.agent.id` (via `agent_reference`) before this feature begins implementation.

## Overview

Replace the legacy replay-against-`/chat` evaluation harness with a comprehensive, multi-layer evaluation strategy built entirely on Microsoft Foundry's native evaluation platform (`azure-ai-projects` ≥ 2.0.0). The three layers provide complementary coverage:

| Layer | Foundry Feature | Trigger | Purpose |
|-------|----------------|---------|---------|
| **Real-time** | Continuous Evaluation (`EvaluationRule`) | Every agent response (sampled) | Catch safety/quality regressions immediately; feed the Monitor dashboard |
| **Nightly benchmark** | Agent Target Evaluation (scheduled) | Nightly cron | Run golden query set against the live agent; compare to ground truth; detect drift |
| **Nightly production audit** | Trace Evaluation | Nightly cron | Score yesterday's real user conversations for quality and safety |

All three layers use the same `client.evals` API surface and share the same evaluator set. Results land in the Foundry portal's Evaluation tab with **Cluster Analysis** (the "Analyze Results" button) enabled — an AI-powered visualization that groups evaluation samples by semantic similarity, identifies recurring failure patterns, and provides actionable recommendations for agent improvement.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Continuous evaluation on production responses (Priority: P1)

As responses flow through the Cadence agent in production, Foundry continuously samples them and runs evaluators. Operators see near-real-time quality and safety scores on the Monitor dashboard without any batch job or scheduler.

**Why this priority**: Continuous evaluation is the fastest feedback loop. It detects regressions (model deployment change, prompt drift, tool failures) within minutes rather than waiting for the nightly batch. It also powers the Foundry Monitor tab alerts.

**Independent Test**: After deployment, generate 10+ agent interactions. Within 15 minutes, verify evaluation scores appear on the Monitor tab for the configured agent. Verify no custom scheduler or cron job is involved.

**Acceptance Scenarios**:

1. **Given** an `EvaluationRule` is configured with `event_type=RESPONSE_COMPLETED` and `filter=agent_name`, **When** the agent produces a response, **Then** Foundry evaluates it at the configured sample rate and scores appear on the Monitor dashboard.
2. **Given** the rule is configured with `max_hourly_runs=100`, **When** traffic exceeds 100 responses/hour, **Then** excess responses are sampled (not queued indefinitely), and no errors are raised.
3. **Given** the evaluator detects a safety violation (e.g., violence score ≥ threshold), **When** the Monitor alert is configured, **Then** an alert fires within the configured detection window.

---

### User Story 2 - Nightly benchmark against golden dataset (Priority: P1)

A curated golden dataset of representative queries with expected outcomes is run against the live Cadence agent every night. The responses are scored against ground truth, producing a stable benchmark that is independent of production traffic volume or mix.

**Why this priority**: Trace evaluation only measures what users happened to ask. A golden dataset ensures consistent coverage of critical query categories (complex joins, parameter extraction edge cases, empty results, ambiguous queries) regardless of whether real users exercised them that day. It also enables NLP evaluators that require `ground_truth` (F1, similarity, response completeness).

**Independent Test**: Trigger the benchmark evaluation against the golden dataset. Verify it produces scored results in the Foundry portal with per-row ground-truth comparison. Verify the same dataset version produces comparable scores across runs (no randomness in query selection).

**Acceptance Scenarios**:

1. **Given** a versioned golden dataset (JSONL) is uploaded to the Foundry project via `project_client.datasets.upload_file(...)`, **When** the nightly benchmark job runs, **Then** it sends each query to the Cadence agent via `azure_ai_target_completions` with target `azure_ai_agent`, evaluates responses, and the job exits 0.
2. **Given** the golden dataset contains `ground_truth` fields, **When** results are scored, **Then** NLP evaluators (`f1_score`, `similarity`) are included alongside quality evaluators (`coherence`, `task_adherence`).
3. **Given** a model deployment change degrades SQL generation quality, **When** the next nightly benchmark runs, **Then** the `f1_score` and `task_adherence` pass rates drop measurably compared to the previous run, visible as a trend line in the portal.
4. **Given** the golden dataset is updated (new version uploaded), **When** the benchmark references the new version, **Then** the old version remains available for historical comparison.

---

### User Story 3 - Nightly trace evaluation of production traffic (Priority: P1)

The nightly trace evaluation scores the previous day's real user conversations by evaluating recorded agent traces from Application Insights. This measures what users actually experienced, using the same evaluators as the other layers.

**Why this priority**: The current replay harness produces evaluator runs that show as Failed in the Foundry portal with the **Analyze Results** button greyed out. Trace evaluation lights up that affordance and removes replay traffic against the live `/chat` endpoint.

**Independent Test**: Trigger the nightly eval workflow against the last 24 hours of production traces. Verify it produces an evaluation row in the Foundry portal under `cadence-eval-v1`, with per-metric scores populated and the **Analyze Results** button enabled. Verify zero requests landed on `/api/chat/stream` from the eval workflow's identity during the run.

**Acceptance Scenarios**:

1. **Given** at least N completed chat turns in the configured lookback window with traces in Application Insights tagged `gen_ai.agent.id = "<configured-name>:<version>"`, **When** the nightly eval job runs, **Then** it submits one evaluation run via `client.evals.runs.create(...)` with `data_source_type: azure_ai_traces` and the agent filter, and the job exits 0.
2. **Given** the previous step completes, **When** the operator opens the evaluation view in the Foundry portal, **Then** the most recent run is listed, per-metric columns (intent_resolution, task_adherence, tool_call_accuracy, …) are populated, and the **Analyze Results** button is enabled.
3. **Given** the lookback window contains zero qualifying traces, **When** the nightly eval job runs, **Then** it exits 0 with a clear "no traces in window" report — it does NOT submit an empty evaluation, and it does NOT call the chat endpoint.

---

### User Story 4 - Per-run resilience and operator visibility (Priority: P1)

A single transient evaluator failure (e.g., 429, network blip on a single trace) must not abort any evaluation job. The Foundry-side evaluation run records per-row outcomes (pass / fail / error), and the harness exits non-zero only if the *entire submission* failed.

**Why this priority**: Without this, eval jobs are flaky tripwires. Bundling it with the evaluation layers ensures trust from day one.

**Independent Test**: Submit a known-bad fixture (e.g., a trace whose `invoke_agent` span is missing a required attribute) alongside good ones: the overall submission succeeds, the bad row reports its error in the portal, and the CLI exit code is 0.

**Acceptance Scenarios**:

1. **Given** the eval submission succeeds with one row marked error in the portal, **When** the CLI exits, **Then** exit code is 0 and the local report file notes the partial-success state (with a deep link to the portal row).
2. **Given** the eval submission itself fails (auth error, service outage), **When** the CLI exits, **Then** exit code is non-zero and the failure reason is logged.

---

### User Story 5 - Operator-friendly CLI for ad-hoc evaluation (Priority: P2)

An operator can re-run any evaluation layer locally: trace evaluation with a custom window or explicit trace IDs, benchmark against the golden dataset, dry-run mode, and target either the production evaluation or a throwaway one.

**Why this priority**: Falls out of getting the CLI shape right at the same time as the nightly jobs.

**Independent Test**: Run `python -m evaluations trace --window 24h --dry-run --out /tmp/eval.json`. Confirm: no Foundry API call is made, the report file contains the resolved filter parameters, exit code is 0.

**Acceptance Scenarios**:

1. **Given** `--dry-run` on any subcommand, **When** the CLI runs, **Then** no Foundry evaluation submission occurs and the report file is still produced.
2. **Given** `evaluations trace --trace-ids <file>`, **When** the CLI runs, **Then** the agent-filter mode is bypassed and the supplied ids are submitted verbatim.
3. **Given** `evaluations benchmark --dataset-version 2`, **When** the CLI runs, **Then** that specific dataset version is used for the agent target evaluation.
4. **Given** `--evaluation-id <id>` on any subcommand, **When** results are submitted, **Then** the new run lands under that evaluation in the portal.

---

### User Story 6 - Golden dataset curation and versioning (Priority: P2)

Operators maintain the golden dataset as a JSONL file in the repository. A CLI command uploads it to Foundry as a versioned dataset. The dataset covers critical query categories for the NL2SQL domain.

**Why this priority**: The benchmark layer is only as good as its dataset. Providing a clear workflow for maintaining and uploading it ensures the benchmark stays relevant.

**Why this priority**: The benchmark layer is only as good as its dataset. Providing a clear workflow for maintaining and uploading it ensures the benchmark stays relevant.

**Independent Test**: Run `python -m evaluations dataset upload --file golden_queries.jsonl --version 3`. Verify the dataset appears in the Foundry project with version 3 and the correct row count.

**Acceptance Scenarios**:

1. **Given** a JSONL file with query/ground_truth/category fields, **When** the upload command runs, **Then** it creates a versioned dataset in the Foundry project via `project_client.datasets.upload_file(...)`.
2. **Given** the dataset contains rows tagged by category (e.g., "complex_join", "empty_result", "ambiguous"), **When** benchmark results are analyzed, **Then** per-category pass rates can be derived from the evaluation output.

---

### User Story 7 - Cluster Analysis for failure pattern identification (Priority: P1)

After evaluation runs complete, operators use Foundry's Cluster Analysis (preview) feature — triggered via the "Analyze Results" button on the evaluation detail page — to identify recurring failure patterns, group semantically similar issues, and receive AI-generated recommendations for agent improvement.

**Why this priority**: Cluster Analysis is the primary diagnostic affordance in the Foundry evaluation portal. Without completed evaluation runs, the button is greyed out (current state for trace evaluations). Ensuring runs complete successfully is a prerequisite for this workflow, making it tightly coupled to all three evaluation layers.

**Independent Test**: After a nightly benchmark or trace evaluation run completes successfully, navigate to the evaluation detail page in the Foundry portal, select the completed run, and click "Analyze Results". Verify the cluster map renders with grouped samples, failure categories are identified, and recommendations are generated.

**Acceptance Scenarios**:

1. **Given** one or more evaluation runs with status "Completed" are selected on the evaluation detail page, **When** the operator clicks "Analyze Results", **Then** the cluster analysis visualization renders showing samples grouped by semantic similarity with pass/fail breakdown.
2. **Given** the cluster analysis is generated, **When** the operator selects a cluster, **Then** a detail panel shows the cluster name, entry count, subclusters, a diagnostic summary explaining the likely cause, and recommendations for improvement.
3. **Given** the cluster analysis is generated, **When** the operator clicks "Download", **Then** a CSV export of the analysis is produced for offline review.
4. **Given** trace evaluation runs that previously showed as "Failed" (current baseline), **When** the implementation is complete and trace evaluation runs show as "Completed", **Then** the "Analyze Results" button is enabled for those runs.

---

### Edge Cases

- The configured `gen_ai.agent.id` rotates between deployments (e.g., the orchestrator agent version increments). The trace evaluation agent-filter mode must scope on the configured name+version exactly; the CLI must surface that scoping in its report so operators can spot a misconfiguration that returns zero rows.
- The Application Insights resource is connected to the project but the project's managed identity is missing **Log Analytics Reader** on it. Trace evaluation MUST fail fast with a clear remediation pointer.
- Traces older than App Insights retention have aged out. They are simply not part of the result set; this is not an error.
- A trace exists but lacks required GenAI semantic-convention attributes (e.g., the `invoke_agent` span is missing `gen_ai.agent.id`). It is silently skipped by the Foundry-side filter — this is correct behavior and not a harness fault.
- The golden dataset references SQL patterns that no longer exist in the database schema. Those rows will produce low scores — this is the desired signal (the dataset needs updating), not a harness error.
- Continuous evaluation rule encounters a burst of responses exceeding `max_hourly_runs`. Excess responses are dropped (not queued). This is acceptable — the sample rate provides statistical coverage, not exhaustive evaluation.
- The Foundry agent name/version used in Agent Target Evaluation doesn't match what's registered. The submission MUST fail with a clear error naming the expected agent.

## Requirements *(mandatory)*

### Functional Requirements

#### Layer 1: Continuous Evaluation

- **FR-001**: The project MUST configure at least one `EvaluationRule` with `event_type=EvaluationRuleEventType.RESPONSE_COMPLETED` and a filter scoped to the Cadence agent name via `project_client.evaluation_rules.create_or_update(...)`.
- **FR-002**: The continuous evaluation rule MUST include safety evaluators (`builtin.violence` at minimum) and at least one quality evaluator (`builtin.intent_resolution` or `builtin.coherence`).
- **FR-003**: The rule MUST be configured with a reasonable `max_hourly_runs` (default: 100) to avoid runaway costs under burst traffic.
- **FR-004**: The project's managed identity MUST hold the **Foundry User** role on the Foundry project (documented prerequisite for continuous evaluation).
- **FR-005**: A setup script or CLI command MUST exist to create/update the evaluation rule idempotently (`create_or_update` with a stable rule ID).

#### Layer 2: Golden Dataset Benchmark

- **FR-006**: A curated golden dataset MUST be maintained as a JSONL file in the repository (`src/evaluations/data/golden_queries.jsonl`) with fields: `query`, `ground_truth`, and optional `category`.
- **FR-007**: A CLI command (`python -m evaluations dataset upload`) MUST upload the golden dataset to the Foundry project as a versioned dataset via `project_client.datasets.upload_file(...)`.
- **FR-008**: The nightly benchmark job MUST create an evaluation with `data_source_type: azure_ai_target_completions` and target `{"type": "azure_ai_agent", "name": "<agent-name>", "version": "<version>"}`, sending golden queries to the live agent and evaluating responses.
- **FR-009**: The benchmark evaluation MUST include evaluators that leverage `ground_truth`: at minimum `builtin.f1_score` and `builtin.coherence`. It SHOULD also include `builtin.task_adherence` and `builtin.intent_resolution`.
- **FR-010**: The benchmark MUST use `{{item.query}}` for input and `{{sample.output_text}}` / `{{sample.output_items}}` for evaluator data mappings, following the documented Agent Target Evaluation pattern.
- **FR-011**: The golden dataset MUST contain at minimum 50 queries covering the documented query categories (complex joins, parameter extraction, empty results, ambiguous queries, conversational follow-ups).

#### Layer 3: Trace Evaluation

- **FR-012**: The nightly trace evaluation MUST submit evaluations using `client.evals` with `data_source_type: azure_ai_traces`. It MUST NOT issue HTTP requests to Cadence's own `/api/chat/stream` endpoint.
- **FR-013**: By default, trace evaluation MUST use agent-filter mode (`gen_ai.agent.id`-filtered) over a configurable lookback window (default 24h). It MUST support explicit-ids mode (`trace_ids`) when the operator supplies an ids file.
- **FR-014**: Trace evaluation MUST use the data mapping documented for trace evaluation: `{{item.query}}`, `{{item.response}}`, `{{item.tool_calls}}`, `{{item.tool_definitions}}` (no `item.` or `sample.` prefixes on trace-extracted fields for the Foundry service — it handles extraction internally).

#### Cluster Analysis Enablement

- **FR-023**: All evaluation runs (trace and benchmark) MUST complete with status "Completed" in the Foundry portal so that the Cluster Analysis ("Analyze Results") button is enabled. Runs that land as "Failed" indicate a harness or configuration bug, not expected behavior.
- **FR-024**: The evaluation harness MUST NOT produce runs with empty result sets (zero scored rows) — such runs render Cluster Analysis unusable even if technically "Completed". When zero qualifying traces exist, the harness skips submission entirely (per US-3 acceptance scenario 3).

#### Shared Requirements

- **FR-015**: All three layers MUST submit results to configurable target evaluations. The trace and benchmark layers default to named evaluations (`cadence-eval-traces`, `cadence-eval-benchmark`) so trending is preserved across runs.
- **FR-016**: When per-row errors occur inside any Foundry-managed evaluation, the harness MUST treat them as non-fatal (exit 0). When the *submission itself* fails, the harness MUST exit non-zero.
- **FR-017**: The CLI MUST support subcommands: `trace` (layer 3), `benchmark` (layer 2), `dataset upload` (golden dataset management), and `setup` (layer 1 rule creation). All subcommands support `--dry-run` and `--out <path>`.
- **FR-018**: If any `client.evals.runs.create(...)` call returns a `report_url`, the CLI report MUST include it for one-click portal navigation.
- **FR-019**: The previous replay-based harness (`src/evaluations/replay.py`, the per-record evaluator loop in `runner.py`, and any custom `AIAgentConverter`-driven harvester) MUST be removed. No transitional dual-path code remains.
- **FR-020**: The nightly GitHub workflow MUST invoke both `trace` and `benchmark` subcommands and MUST NOT invoke Cadence's chat endpoint.
- **FR-021**: No new persistent storage is introduced. Reports are written to `--out`. Datasets are stored in Foundry's managed storage.
- **FR-022**: The harness MUST run under an identity that holds the documented prerequisites: **Foundry User** role on the project, and for trace evaluation the project's managed identity needs **Log Analytics Reader** on Application Insights and its linked Log Analytics workspace.

### Key Entities

- **Continuous Evaluation Rule**: The `EvaluationRule` resource configured in the Foundry project — rule ID, agent filter, evaluator set, sample rate, max hourly runs. Created/updated via `setup` command.
- **Golden Dataset**: Versioned JSONL file uploaded to the Foundry project. Contains query, ground_truth, and category fields. Source of truth lives in the repository.
- **Trace Evaluation Filter**: The filter for one trace eval run — agent name+version, lookback window bounds (or explicit ids), target evaluation id, evaluator set. Logged at job start.
- **Evaluation Submission Summary**: Per-job output — submission status, evaluation run id, report URL, filter/dataset used, and process exit code. Written to `--out`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The Foundry Monitor dashboard for the Cadence agent shows continuous evaluation scores updating within 15 minutes of production traffic, with no custom scheduler running.
- **SC-002**: The `cadence-eval-benchmark` evaluation in the Foundry portal shows nightly benchmark runs with `f1_score`, `coherence`, `task_adherence`, and `intent_resolution` columns populated. Pass-rate trends are visible across runs.
- **SC-003**: The `cadence-eval-traces` evaluation in the Foundry portal shows nightly trace evaluation runs with status "Completed" and the **Analyze Results** (Cluster Analysis) button enabled. Clicking it produces a cluster map with failure pattern groupings and recommendations. Today's baseline: all runs show "Failed" with the button greyed out.
- **SC-004**: Nightly evaluation submissions (both trace and benchmark) succeed (exit 0) on ≥ 95% of scheduled runs over a 30-day window.
- **SC-005**: Nightly evaluation runs issue zero HTTP requests to Cadence's `/api/chat/stream` endpoint, verified by router-level request counters for the eval workflow's identity.
- **SC-006**: The `src/evaluations/` directory contains no `replay.py` and no references to Cadence's own chat endpoint URL after merge.
- **SC-007**: A model deployment change that degrades response quality by ≥10% on the golden dataset is detected within 24 hours (nightly benchmark detects the regression via F1 score drop).
- **SC-008**: The golden dataset contains ≥ 50 curated queries covering at least 5 distinct query categories.
- **SC-009**: Cluster Analysis can be successfully generated on both `cadence-eval-traces` and `cadence-eval-benchmark` completed runs, producing identifiable failure clusters with actionable recommendations.

## Assumptions

- Feature 007 has been merged and is producing chat traces tagged with `gen_ai.agent.id = "<configured-name>:<version>"` (via `agent_reference` set on responses calls). This is gating.
- The Foundry project is connected to Application Insights, and the project's managed identity holds **Log Analytics Reader** on the App Insights resource and its linked Log Analytics workspace.
- `azure-ai-projects` ≥ 2.0.0 is GA with support for `client.evals.create(...)` (trace, dataset, and agent target scenarios) and `project_client.evaluation_rules.create_or_update(...)`.
- The Cadence agent is registered as a PromptAgent (or HostedAgent) in the Foundry project, enabling it to be referenced as a target in Agent Target Evaluation by name and version.
- Application Insights retention covers the configured trace lookback window (default 24h is well inside the default 90-day retention).
- The Foundry project's managed identity holds the **Foundry User** role (prerequisite for continuous evaluation rules).

## Dependencies

- Feature 007 in production (gating).
- `azure-ai-projects` ≥ 2.0.0 pinned in `pyproject.toml`.
- Continued access to the existing Foundry project and its connected Application Insights resource.
- Agent registered in the Foundry project (for Agent Target Evaluation to address it by name).
- GPT model deployment available for AI-assisted evaluators (already provisioned for the project).

## Out of Scope

- Runtime client work (owned by feature 007).
- Adding custom evaluators beyond the built-in set. Custom evaluators are a follow-up once the native evaluator baseline is established.
- Synthetic data evaluation (preview feature — logical follow-up for coverage expansion once the golden dataset is established).
- Red team evaluation (preview feature — logical follow-up for adversarial testing once continuous evaluation is operational).
- Backfilling evaluator scores for historical traces that pre-date feature 007's `agent_reference` rollout.
- Introducing a separate eval database or storage layer.
- Frontend changes.
- The legacy `AIAgentConverter` / `azure.ai.evaluation` SDK path — that is the classic-Foundry surface being replaced.
- Alerting configuration (Monitor alerts are configured via the portal UI once continuous evaluation is producing data; no custom alerting code needed).
