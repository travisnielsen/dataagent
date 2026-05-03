# Feature Specification: Microsoft Foundry Evaluations for NL2SQL

**Feature Branch**: `005-foundry-evaluations`
**Created**: 2026-03-24
**Status**: Implemented (updated 2026-05-02)
**Input**: User description: "Create a new Spec Kit feature spec for incorporating Microsoft Foundry evaluations into this repository's NL2SQL multi-agent solution."

## Change Log

### 2026-05-02

- Aligned specification language with the implemented Foundry-native cloud evaluation flow.
- Clarified that nightly evaluations run in cloud mode and are expected to appear in Foundry Evaluations.
- Added explicit requirement for Foundry run linkage visibility to operators (Studio URL/ID exposure).
- Updated wording to reflect current `azure-ai-evaluation` cloud publishing path and removed stale batch API assumptions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Establish an Evaluation Contract (Priority: P1)

As a platform owner, I need a clear, shared evaluation contract for the NL2SQL assistant so quality expectations are explicit and regressions are measurable across releases.

**Why this priority**: Without a contract, evaluation runs cannot be interpreted consistently and cannot gate release quality.

**Independent Test**: Review the published evaluation contract and verify that each pipeline target and metric has a definition, threshold owner, and reporting format.

**Acceptance Scenarios**:

1. **Given** the DataAssistant routes a mix of conversational and data-query requests, **When** the evaluation contract is applied, **Then** primary metrics include intent routing accuracy, clarification quality and recovery rate, SQL safety/compliance rate, and business answer usefulness.
2. **Given** evaluation telemetry is collected from NL2SQLController and downstream executors, **When** secondary metrics are computed, **Then** tool-call success, latency percentiles, and zero-result handling quality are reported in the same run summary.
3. **Given** evaluation outputs are reviewed for a release candidate, **When** a metric definition is ambiguous, **Then** the run is considered non-compliant until the contract definition is explicit.

---

### User Story 2 - Build and Version Evaluation Datasets (Priority: P1)

As an evaluation owner, I need both curated and trace-harvested datasets so test coverage reflects intended behavior and real production usage.

**Why this priority**: Dataset quality and representativeness determine whether evaluator scores can drive real engineering decisions.

**Independent Test**: Build one curated dataset and one trace-harvested dataset (from Foundry), merge them, version both, and verify they can be reused for trend and regression analysis.

**Acceptance Scenarios**:

1. **Given** dataset curation for NL2SQL behavior, **When** the gold dataset is prepared, **Then** it contains 200-500 prompts spanning template queries, dynamic queries, clarifications, and what-if scenarios.
2. **Given** Foundry traces are available, **When** trace records are harvested using `AIProjectClient`, **Then** user/assistant message pairs are extracted, sanitized for sensitive data (emails, SSN, etc.), and stored as a versioned trace-harvested dataset.
3. **Given** two or more dataset versions exist, **When** a mixed dataset is created by merging gold and trace records with deduplication, **Then** the resulting dataset contains both authoritative gold examples and real-world user interactions, versioned as `cadence-eval-mixed-v<N>.jsonl`.
4. **Given** evaluation datasets exist, **When** an evaluation run is executed, **Then** the run records the exact dataset version and source (gold / trace_harvested / gold+trace_harvested) to support trend and regression comparison.

---

### User Story 3 - Run Built-In and Custom Evaluators with CI Gates (Priority: P1)

As a release engineer, I need standardized evaluator phases and CI gating so merges are blocked when critical quality regresses.

**Why this priority**: Quality gates prevent known degradations from reaching mainline and production.

**Independent Test**: Execute a CI run on the P0 subset and verify that threshold regressions fail the merge gate; execute nightly full-suite run with trace harvesting, mixed dataset creation, and verify the run appears in Foundry Evaluations and emits a Foundry Studio URL.

**Acceptance Scenarios**:

1. **Given** Phase 1 evaluation is enabled, **When** a run starts, **Then** built-in evaluators `intent_resolution`, `task_adherence`, `tool_call_accuracy`, `relevance`, and `indirect_attack` are executed.
2. **Given** an agent-style response is evaluated, **When** evaluator input is prepared, **Then** message history includes tool calls and tool results, not only final assistant text.
3. **Given** Phase 2 is enabled, **When** custom evaluators run, **Then** custom code evaluators assess SQL safety policy pass/fail and parameter extraction correctness, and custom prompt evaluators assess business answer adequacy and clarification question quality.
4. **Given** CI executes a pull request workflow, **When** P0 thresholds regress, **Then** the merge gate fails and reports the failing metrics.
5. **Given** nightly automation executes with trace harvesting enabled, **When** `python -m evaluations harvest` completes, **Then** a mixed dataset is created combining gold records with recently harvested Foundry traces.
6. **Given** nightly automation executes with cloud mode enabled, **When** the run succeeds, **Then** a Foundry-native evaluation record is created, appears in the Foundry Evaluations blade, and provides a Foundry Studio URL for operators to review results.

---

### User Story 4 - Close the Optimization Loop (Priority: P2)

As a maintainer, I need failure clustering and targeted remediation so each evaluation cycle improves prompts, routing, or validators in a measurable way.

**Why this priority**: Evaluation without remediation loops creates reporting overhead but limited product improvement.

**Independent Test**: Cluster failures from one run, apply one targeted fix, re-run against the same dataset version, and verify measurable delta.

**Acceptance Scenarios**:

1. **Given** an evaluation run completes, **When** failure analysis is performed, **Then** failures are clustered by intent misroute, extraction issues, validator rejection, and poor answer quality.
2. **Given** one cluster is selected for remediation, **When** prompt/routing/validator changes are applied, **Then** the system can re-run the same dataset version and compare before/after deltas.
3. **Given** multiple deltas are available, **When** release readiness is reviewed, **Then** the decision references measured improvements and remaining regressions.

---

### User Story 5 - Roll Out in Phases with Production Feedback (Priority: P2)

As an engineering lead, I need a phased rollout plan that starts with baseline measurement, adds CI enforcement, and matures into a production feedback loop.

**Why this priority**: Phased adoption reduces operational risk while increasing confidence over time.

**Independent Test**: Verify week-by-week deliverables are completed and corresponding governance checks are in place.

**Acceptance Scenarios**:

1. **Given** Week 1 rollout scope, **When** baseline is executed, **Then** built-in evaluator thresholds are established and recorded.
2. **Given** Week 2 rollout scope, **When** custom safety/quality evaluators are introduced, **Then** CI merge gating is active for agreed thresholds.
3. **Given** Week 3+ rollout scope, **When** nightly trace-to-dataset refresh and weekly trend review run, **Then** production feedback continuously updates evaluation focus.

### Edge Cases

- Trace-harvested data includes sensitive business or personal fields and sanitization fails to remove all protected values before dataset publication.
- Evaluators produce false negatives due to incomplete context, stale rubrics, or missing tool history.
- A prompt has valid intent but produces zero rows; the evaluation must score uncertainty and zero-result messaging quality, not only correctness.
- Validator rejection is correct from policy perspective but appears as usefulness failure unless policy-driven behavior is tagged in the run output.
- Foundry evaluation service delays or partial failures occur; CI must fail safely and distinguish infrastructure error from model-quality regression.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST define and publish an NL2SQL evaluation contract aligned to repository architecture components: DataAssistant, NL2SQLController, ParameterExtractor, ParameterValidator, QueryValidator, QueryBuilder.
- **FR-002**: System MUST include primary metrics in the contract: intent routing accuracy, clarification quality and recovery rate, SQL safety/compliance rate, and answer usefulness for business users.
- **FR-003**: System MUST include secondary metrics in the contract: tool-call success, latency percentiles, and zero-result handling quality.
- **FR-004**: System MUST define pipeline-specific targets for evaluation: DataAssistant/Orchestrator routing correctness, ParameterExtractor clarification and next-turn recovery behavior, query safety/correctness against policy and schema, and final response quality with grounding, uncertainty messaging, and zero-row behavior.
- **FR-005**: System MUST maintain a curated gold dataset of 200-500 prompts covering templates, dynamic queries, clarifications, and what-if scenarios.
- **FR-006**: System MUST build a trace-harvested dataset from Application Insights traces, sanitize the data, and label records for evaluation use.
- **FR-007**: System MUST version all datasets and bind each evaluation run to explicit dataset version metadata.
- **FR-008**: System MUST run Phase 1 built-in evaluators: `intent_resolution`, `task_adherence`, `tool_call_accuracy`, `relevance`, and `indirect_attack`.
- **FR-009**: System MUST provide full conversation/message history including tool calls and tool results as evaluator input for agent-style evaluations.
- **FR-010**: System MUST support Phase 2 custom code evaluators for SQL safety policy pass/fail and parameter extraction correctness against expected parameters.
- **FR-011**: System MUST support Phase 2 custom prompt evaluators for business answer adequacy against expected-behavior rubric and clarification question quality (single-question, minimally ambiguous, actionable).
- **FR-012**: System MUST orchestrate Foundry-native evaluation runs using `azure-ai-evaluation` `evaluate(..., azure_ai_project=<project-endpoint>)`, with run creation and summary publication visible in Foundry.
- **FR-013**: System MUST fail CI on threshold regressions for the P0 subset and block merge until regression is resolved or explicitly waived by policy.
- **FR-014**: System MUST execute the full evaluation suite on a nightly schedule in cloud mode and publish run outputs to Foundry plus local workflow artifacts.
- **FR-015**: System MUST cluster evaluation failures by at least these categories: intent misroute, bad extraction, validator rejection, poor answer quality.
- **FR-016**: System MUST support targeted remediation updates to prompts, routing behavior, and validator logic with documented rationale.
- **FR-017**: System MUST re-run the same dataset version after remediation and report metric deltas against the prior run.
- **FR-018**: System MUST emit SSE step events for evaluation runner lifecycle milestones (dataset loading, evaluator initialization, evaluation execution, result aggregation) so users can observe run progress in the same interaction model used by the assistant. Trace harvesting and offline analysis operate as batch CLI commands and do not require SSE.
- **FR-019**: System MUST include Application Insights tracing signals needed to correlate evaluation outcomes with request flow stages.
- **FR-020**: System MUST maintain phased rollout controls:
  - Week 1 baseline with built-in evaluators and threshold definition
  - Week 2 custom evaluators with CI merge gate activation
  - Week 3+ production feedback loop with nightly trace refresh and weekly trend review
- **FR-021**: System MUST preserve compatibility with existing NL2SQL runtime behavior when evaluations are disabled or unavailable.
- **FR-022**: System MUST retain auditable records of evaluator versions, dataset versions, threshold configuration, and gate decisions per run.
- **FR-023**: System MUST expose Foundry run linkage for operators (e.g., Studio URL) from nightly/manual cloud runs.

### Non-Functional Requirements

- **NFR-001 (Reliability)**: Evaluation orchestration and CI gating MUST produce deterministic pass/fail outcomes for identical code, evaluator versions, and dataset versions.
- **NFR-002 (Observability)**: Every evaluation run MUST include traceable identifiers linking run summary, dataset version, evaluator set, and application traces.
- **NFR-003 (Security & Privacy)**: Trace-harvested datasets MUST be sanitized to remove or mask sensitive fields before use in evaluation or CI artifacts.
- **NFR-004 (Performance)**: Evaluation execution for PR gating (P0 subset) MUST complete within 10 minutes. Full-suite nightly evaluation MUST complete within 60 minutes. Drift beyond these thresholds MUST be monitored and investigated.
- **NFR-005 (Governance)**: Any threshold or evaluator changes MUST be reviewable and attributable to an owner and effective date.

### Key Entities *(include if feature involves data)*

- **EvaluationConfig**: Versioned definition of metrics, target behaviors, thresholds, and gating scope.
- **DatasetMetadata**: Versioned evaluation corpus with source type (gold or trace-harvested), sanitization status, labels, and lineage metadata.
- **EvaluatorProfile**: Declares evaluator type (built-in, custom code, custom prompt), version, inputs, and target metric mapping.
- **EvaluationRun**: Immutable record of a single execution including dataset version, evaluator set, status, metric outcomes, and correlation IDs.
- **FailureCluster**: Categorized set of failed cases grouped by root-cause class for remediation planning.
- **QualityGateDecision**: CI decision artifact that records pass/fail against P0 thresholds and any approved waivers.

### Out of Scope

- Auto-remediation that changes prompts, routing, or validators without human review.
- Replacement of existing NL2SQL business logic outside evaluation-driven changes.
- Real-time production blocking based solely on evaluator output without policy review.
- Introducing unrelated model hosting migration work beyond Foundry evaluation integration.

### Risks

- Privacy risk: trace-harvested datasets may leak sensitive values if sanitization rules are incomplete or drift over time.
- Quality risk: evaluator false negatives may incorrectly fail good behavior, especially for nuanced clarification or tool-mediated flows.
- Process risk: excessive gate sensitivity may slow delivery if thresholds are not calibrated to stable baselines.

### Dependencies

- Foundry runtime availability for cloud run execution and result publication.
- Application Insights tracing and retention policy sufficient for dataset harvesting and lineage.
- CI pipeline support for threshold enforcement, artifact publication, and nightly scheduling.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of evaluation runs include all primary and secondary contract metrics with explicit pass/fail threshold status.
- **SC-002**: Gold dataset contains between 200 and 500 prompts and includes all required scenario classes (templates, dynamic, clarifications, what-if) before CI gating is enforced.
- **SC-003**: At least 95% of trace-harvested dataset records pass sanitization validation before being accepted into a versioned dataset release.
- **SC-004**: For P0 CI runs, merge gate decisions are produced for 100% of pull requests that modify NL2SQL assistant behavior.
- **SC-005**: Nightly full-suite evaluations execute on schedule at least 95% of days in a rolling 30-day period.
- **SC-006**: For each remediation cycle, delta comparison against the same dataset version is available and attributable for 100% of changes promoted to mainline.
- **SC-007**: Routing correctness, extraction/clarification recovery, safety/compliance, and response-quality targets each show non-regressing trend over three consecutive weekly reviews after Week 3 starts.
- **SC-008**: Zero-result responses in the evaluation set achieve at least 90% quality pass rate for uncertainty messaging and actionable next-step guidance.

## Assumptions

- Existing Application Insights instrumentation provides enough trace coverage to bootstrap trace-harvested datasets.
- Repository teams can curate and maintain gold dataset labels for expected behaviors.
- P0 threshold governance and waiver process will be defined by the maintainers before strict merge blocking is enforced.
- Foundry evaluation runtime and CI infrastructure are available in environments where gating is required.
