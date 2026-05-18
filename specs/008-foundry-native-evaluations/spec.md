# Feature Specification: Foundry Trace-Based Nightly Evaluations

**Feature Branch**: `008-foundry-native-evaluations` *(to be created)*
**Created**: 2026-05-17 (rescoped 2026-05-17)
**Status**: Draft
**Depends on**: [007-foundry-framework-upgrade](../007-foundry-framework-upgrade/spec.md) — must be merged and producing chat traces tagged with `gen_ai.agent.id` (via `agent_reference`) before this feature begins implementation.
**Input**: User description: "Rewrite the nightly evaluation harness to use Foundry's documented trace-based evaluation API (`azure-ai-projects` ≥ 2.0.0 `client.evals.create(...)` with `data_source_type: azure_ai_traces`, agent-filter mode keyed on `gen_ai.agent.id`) against recorded production traces in Application Insights. Replace the custom replay-against-`/chat` harness and the custom run-harvest/`AIAgentConverter` plumbing — both reflect a legacy classic-Foundry path. Goal: align with Microsoft's documented GA evaluation flow with minimal custom code, and get the **Analyze Results** affordance lit up in the existing `cadence-eval-v1` evaluation."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Nightly evaluation runs against recorded production traces (Priority: P1)

The nightly Foundry evaluation job measures real production behavior by attaching evaluators to recorded agent traces (Application Insights `invoke_agent` spans, filtered by `gen_ai.agent.id`) instead of replaying user prompts against the live chat endpoint with fabricated session ids. Operators get trustworthy scores that reflect what users actually experienced.

**Why this priority**: The current replay harness produces evaluator runs that show as Failed in the Foundry portal (`cadence-eval-v1`) with the **Analyze Results** button greyed out. The combination of (a) feature 007 setting `agent_reference` on responses calls and (b) this feature submitting evaluations via the documented `client.evals` trace-based path is what lights up that affordance. It also removes replay traffic against the live `/chat` endpoint.

**Independent Test**: Trigger the nightly eval workflow against the last 24 hours of production traces. Verify it produces an evaluation row in the Foundry portal under `cadence-eval-v1`, with per-metric scores populated and the **Analyze Results** button enabled. Verify zero requests landed on `/api/chat/stream` from the eval workflow's identity during the run.

**Acceptance Scenarios**:

1. **Given** at least N completed chat turns in the configured lookback window with traces in Application Insights tagged `gen_ai.agent.id = "<configured-name>:<version>"`, **When** the nightly eval job runs, **Then** it submits one evaluation run via `client.evals.create(...)` with `data_source_type: azure_ai_traces` and the agent filter, and the job exits 0.
2. **Given** the previous step completes, **When** the operator opens the `cadence-eval-v1` view in the Foundry portal, **Then** the most recent run is listed, per-metric columns (intent_resolution, task_adherence, tool_call_accuracy, …) are populated, and the **Analyze Results** button is enabled.
3. **Given** the lookback window contains zero qualifying traces, **When** the nightly eval job runs, **Then** it exits 0 with a clear "no traces in window" report — it does NOT submit an empty evaluation, and it does NOT call the chat endpoint.

---

### User Story 2 - Per-run resilience and operator visibility (Priority: P1)

A single transient evaluator failure (e.g., 429, network blip on a single trace) must not abort the whole nightly job. The Foundry-side evaluation run records per-trace outcomes (pass / fail / error), and the harness exits non-zero only if the *entire submission* failed. Operators get paged for real problems (no submission), not for one bad row inside an otherwise successful submission (the portal's per-row error column is the right surface for that).

**Why this priority**: Without this, the eval job is a flaky tripwire. Bundling it with story 1 risks landing the rewrite without trust-building behavior.

**Independent Test**: With Foundry-managed evaluation, per-trace failures are surfaced in the portal per row. Verify by submitting a known-bad fixture (e.g., a trace whose `invoke_agent` span is missing a required attribute) alongside good ones: the overall submission succeeds, the bad row reports its error in the portal, and the CLI exit code is 0.

**Acceptance Scenarios**:

1. **Given** the eval submission succeeds with one row marked error in the portal, **When** the CLI exits, **Then** exit code is 0 and the local report file notes the partial-success state (with a deep link to the portal row).
2. **Given** the eval submission itself fails (auth error, service outage), **When** the CLI exits, **Then** exit code is non-zero and the failure reason is logged.

---

### User Story 3 - Operator-friendly CLI for ad-hoc evaluation (Priority: P2)

An operator can re-run the same window locally to debug a regression, point at a specific list of conversation ids or trace ids supplied as a file, dry-run the submission, and target either the production `cadence-eval-v1` evaluation or a throwaway one. The CLI does not silently submit when the operator only wants a dry-run.

**Why this priority**: Falls out of getting the CLI shape right at the same time as stories 1 and 2.

**Independent Test**: Run `python -m evaluations run --window 24h --dry-run --out /tmp/eval.json`. Confirm: no `client.evals.create(...)` call is made, the report file contains the resolved filter parameters (agent id, lookback, optional explicit conversation/trace ids), exit code is 0.

**Acceptance Scenarios**:

1. **Given** `--dry-run`, **When** the CLI runs, **Then** no Foundry evaluation submission occurs and the report file is still produced.
2. **Given** `--conversation-ids <file>` (or `--trace-ids <file>`), **When** the CLI runs, **Then** the agent-filter mode is bypassed and the supplied ids are submitted verbatim via the explicit-ids mode of `client.evals.create(...)`.
3. **Given** `--evaluation-id <id>`, **When** results are submitted, **Then** the new run lands under that evaluation in the portal (defaulting to `cadence-eval-v1`).

---

### Edge Cases

- The configured `gen_ai.agent.id` rotates between deployments (e.g., the orchestrator agent version increments). The agent-filter mode must scope on the configured name+version exactly; the CLI must surface that scoping in its report so operators can spot a misconfiguration that returns zero rows.
- The Application Insights resource is connected to the project but the project's managed identity is missing **Log Analytics Reader** on it. The submission MUST fail fast with a clear remediation pointer (this is the documented prerequisite in [cloud-evaluation — Trace evaluation](https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation#trace-evaluation)).
- Traces older than App Insights retention have aged out. They are simply not part of the result set; this is not an error.
- A trace exists but lacks required GenAI semantic-convention attributes (e.g., the `invoke_agent` span is missing `gen_ai.agent.id`). It is silently skipped by the Foundry-side filter — this is correct behavior and not a harness fault.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The nightly evaluation harness MUST submit evaluations using `azure-ai-projects` (>= the version that exposes `client.evals.create(...)` with `data_source_type: azure_ai_traces`; target `>= 2.0.0`). It MUST NOT issue HTTP requests to Cadence's own `/api/chat/stream` endpoint as part of evaluation.
- **FR-002**: By default, the harness MUST use agent-filter mode (`gen_ai.agent.id`-filtered) over a configurable lookback window (default 24h). It MUST support explicit-ids mode (`conversation_ids` or App Insights `operation_Id`-derived `trace_ids`) when the operator supplies an ids file.
- **FR-003**: The harness MUST submit results to a configurable target evaluation, defaulting to the existing `cadence-eval-v1` so historical trending is preserved. The submission MUST use the data mapping documented by Microsoft Learn for trace evaluation (no `item.` or `sample.` prefixes on the trace-extracted fields).
- **FR-004**: When per-row errors occur inside the Foundry-managed evaluation, the harness MUST treat them as a non-fatal outcome (exit 0, report includes a deep link to the portal row). When the *submission itself* fails (auth, network, service outage), the harness MUST exit non-zero with a clear error.
- **FR-005**: The CLI MUST support `--window` (e.g., `24h`, `7d`), `--conversation-ids <file>`, `--trace-ids <file>`, `--evaluation-id`, `--dry-run`, and `--out <path>` flags with documented behavior and exit codes.
- **FR-006**: The harness MUST run under an identity that already holds the documented prerequisites for Foundry trace evaluation: project Foundry RBAC for evaluations write + the project's managed identity needs **Log Analytics Reader** on the connected Application Insights resource and its linked Log Analytics workspace. No new role is introduced by this feature.
- **FR-007**: The previous replay-based harness (`src/evaluations/replay.py`, the per-record evaluator loop in `runner.py`, and any custom `AIAgentConverter`-driven harvester) MUST be removed once the new flow is operational. No transitional dual-path code remains.
- **FR-008**: The nightly GitHub workflow MUST invoke the new CLI and MUST NOT invoke Cadence's chat endpoint.
- **FR-009**: The harness MUST NOT introduce any new persistent storage. Reports are written to a file path passed via `--out`.
- **FR-010**: If `client.evals.create(...)` returns a `report_url`, the CLI report MUST include it so operators can navigate from the workflow log into the portal row in one click.

### Key Entities

- **Trace Evaluation Filter**: The reviewable filter for one nightly job — agent name+version (mapping to `gen_ai.agent.id`), lookback window bounds (or explicit ids), target evaluation id, evaluator set. Logged at job start.
- **Evaluation Submission Summary**: The per-job output — submission status (succeeded / failed), evaluation run id (if any), report URL (if any), filter that was used, and process exit code. Written to `--out`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The `cadence-eval-v1` evaluation in the Foundry portal shows the most recent nightly run with per-metric columns (intent_resolution, task_adherence, tool_call_accuracy, …) populated and the **Analyze Results** button enabled (today's baseline: all-Failed rows, button greyed out).
- **SC-002**: Nightly evaluation submissions succeed (exit 0, evaluation run created in the portal) on ≥ 95% of scheduled runs over a 30-day window.
- **SC-003**: Nightly evaluation runs issue zero HTTP requests to Cadence's `/api/chat/stream` endpoint, verified by router-level request counters for the eval workflow's identity.
- **SC-004**: The `src/evaluations/` directory contains no `replay.py` and no references to Cadence's own chat endpoint URL after merge.
- **SC-005**: First successful nightly run after rollout produces a portal evaluation row whose **Conversation ID** column for at least one trace matches the `conversation_id` value the corresponding production SSE turn returned (end-to-end correlation: runtime → trace → eval row).

## Assumptions

- Feature 007 has been merged and is producing chat traces tagged with `gen_ai.agent.id = "<configured-name>:<version>"` (via `agent_reference` set on responses calls). This is gating.
- The Foundry project is connected to Application Insights, and the project's managed identity holds **Log Analytics Reader** on the App Insights resource and its linked Log Analytics workspace. (These are documented prerequisites for trace evaluation; if missing, the harness fails fast per FR-006 / edge cases.)
- `azure-ai-projects` `client.evals.create(...)` with `data_source_type: azure_ai_traces` is GA in the version this feature pins. (If the API surface is still in preview, that is recorded in the planning artifacts; the spec scope does not change.)
- The existing `cadence-eval-v1` evaluation is configured for evaluators compatible with the data extracted from `invoke_agent` spans (intent_resolution, task_adherence, tool_call_accuracy at minimum; full list confirmed during planning).
- Application Insights retention covers the configured lookback window (default 24h is well inside the default 90-day retention).

## Dependencies

- Feature 007 in production (gating).
- `azure-ai-projects` upgraded to the version that exposes `client.evals.create(...)` with `data_source_type: azure_ai_traces` (target `>= 2.0.0`); pinned in `pyproject.toml`.
- Continued access to the existing Foundry project and its connected Application Insights resource.
- The existing `cadence-eval-v1` evaluation resource in the Foundry project.

## Out of Scope

- Runtime client work (owned by feature 007).
- Adopting Foundry **continuous evaluation** (`EvaluationRule` / `EvaluationRuleEventType` configured via the portal Monitor tab) — that is a logical follow-up that would replace this nightly job entirely, but is out of scope for this feature.
- Adding new evaluators, changing existing evaluator parameters, or changing the evaluation schedule. Only the *mechanism* by which traces are evaluated changes here.
- Backfilling evaluator scores for historical traces that pre-date feature 007's `agent_reference` rollout (those traces lack the `gen_ai.agent.id` attribute needed for the agent filter).
- Introducing a separate eval database or storage layer.
- Frontend changes.
- The legacy `AIAgentConverter` / `azure.ai.evaluation` SDK path — that is the classic-Foundry surface (`learn.microsoft.com/azure/foundry-classic/…`) and is explicitly the path being replaced.
