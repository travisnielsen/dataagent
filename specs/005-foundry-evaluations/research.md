# Research: Foundry Evaluations for NL2SQL

**Feature**: 005-foundry-evaluations
**Date**: 2026-03-24

## R-001: Evaluation SDK and Runtime

**Decision**: Use `azure-ai-evaluation` SDK `evaluate()` for both local/CI and Foundry-native cloud runs, with cloud publishing enabled through `azure_ai_project=<project-endpoint>`.

**Rationale**: The codebase already depends on `azure-ai-projects` (via `agent-framework`) and `azure-identity`. The `azure-ai-evaluation` package provides `evaluate()`, built-in evaluators (`IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, `RelevanceEvaluator`, `ToolCallAccuracyEvaluator`), and project publishing through `azure_ai_project`. This allows the same evaluator orchestration path to run locally and to create Foundry-native evaluation records.

**Alternatives considered**:

- Custom evaluation scripts without SDK: rejected because built-in evaluators provide calibrated LLM-judge scoring and Foundry result tracking.
- Third-party evaluation frameworks (e.g., LangSmith): rejected to stay within the Azure ecosystem already used.

## R-002: Dataset Format and Storage

**Decision**: Use JSONL format with fields `query`, `response`, `expected_behavior`, `context`, and `conversation` (full message history including tool calls). Gold datasets stored in `src/backend/evaluations/datasets/`, trace-harvested datasets in `.foundry/datasets/`. Versioning uses `v<N>` suffix convention.

**Rationale**: Foundry evaluators expect JSONL with `query`/`response` at minimum. Including `expected_behavior` in every row from the start enables Phase 2 custom evaluators without dataset regeneration. The `conversation` field carries full message history including tool calls/results, which is required for `tool_call_accuracy` and `task_adherence` evaluators to score agent-style interactions correctly. Gold datasets live in source control for version tracking; trace-harvested datasets use the `.foundry/` cache convention.

**Alternatives considered**:

- CSV format: rejected because nested conversation history cannot be represented.
- Azure Blob-only storage: rejected because gold datasets need source-control versioning and review.

## R-003: Trace Harvesting — Foundry Native Traces (Updated 2026-05-02)

**Decision**: Use `AIProjectClient` from `azure-ai-projects` SDK to query Foundry project sessions and extract conversation traces directly. Harvest recent session/run data using the agents API, extract user/assistant message pairs, and convert to `DatasetRecord` format. Mixed strategy: combine gold-curated records with automatically harvested traces, with optional deduplication by query text.

**Rationale**: Foundry is the canonical storage for all evaluation artifacts (runs, datasets, evaluators). Using Foundry's native trace export (via `AIProjectClient`) eliminates the dependency on Application Insights KQL and provides a single source of truth. The `azure-ai-projects` SDK is already a project dependency (via `agent-framework`). Message pairs are extracted by pairing consecutive user/assistant messages in session history, converted to `DatasetRecord` objects, and merged with gold records for a mixed evaluation dataset.

**Previous Decision (R-003v1)**: KQL queries via Application Insights have been superseded. Initial KQL approach used dependency/custom event correlation; Foundry trace export is simpler and more direct.

**Alternatives considered**:

- Remaining with App Insights KQL: rejected because it introduces a secondary telemetry dependency; Foundry traces are first-class storage.
- App Insights + Foundry hybrid: rejected as over-engineering; Foundry alone is sufficient.
- No trace harvesting: rejected because real-world usage patterns are essential for representative evaluation datasets.

## R-004: Evaluator Phasing Strategy

**Decision**: Two-phase approach. Phase 1 uses 5 built-in evaluators only (`intent_resolution`, `task_adherence`, `tool_call_accuracy`, `relevance`, `indirect_attack`). Phase 2 adds custom code evaluators (SQL safety, parameter extraction correctness) and custom prompt evaluators (business answer adequacy, clarification quality).

**Rationale**: Phase 1 establishes a fast baseline using calibrated built-in LLM judges. Tool call accuracy is included because the NL2SQL pipeline uses tool calls for template search, SQL execution, and table search. Phase 2 custom evaluators target domain-specific gaps that built-in evaluators cannot capture: SQL safety policy compliance (checking allowed tables, parameterized queries, no mutations) and parameter extraction correctness (comparing extracted values against expected parameters).

**Alternatives considered**:

- All evaluators at once: rejected because front-loading custom evaluators delays the first actionable baseline.
- Built-in only without custom: rejected because NL2SQL-specific quality dimensions (SQL safety, extraction accuracy) require domain-aware scoring.

## R-005: CI/CD Integration Pattern

**Decision**: GitHub Actions workflow with two tiers: (1) PR gate running P0 subset (~50 prompts) using `azure-ai-evaluation` SDK locally, (2) nightly scheduled workflow running full suite (~200-500 prompts) in cloud mode using `python -m evaluations --cloud`.

**Rationale**: PR gate must complete within a practical CI window. Running ~50 P0 prompts locally with the SDK avoids Foundry API latency for small datasets. The nightly run uses Foundry cloud publishing for the full dataset, producing Foundry-native run records while still writing local summary artifacts. Threshold regressions in the PR gate fail the merge; nightly regressions open issues.

**Alternatives considered**:

- Cloud publish mode for both PR and nightly: rejected because PR gate would be too slow.
- Local-only evaluation: rejected because Foundry result tracking and trending requires cloud runs.

## R-006: Custom Code Evaluator Design

**Decision**: Implement custom code evaluators as Python functions following the `azure-ai-evaluation` evaluator protocol. SQL safety evaluator checks: allowed tables only, SELECT-only statements, parameterized execution, no SQL injection patterns (reuses existing `query_validator` logic). Parameter extraction evaluator compares extracted parameters against gold-standard expected parameters with confidence scoring.

**Rationale**: The existing `query_validator/validator.py` already implements SQL safety checks (allowed tables, SELECT-only, injection patterns). Wrapping this as an evaluator function reuses validated logic. Parameter extraction correctness maps naturally to comparing `SQLDraft.extracted_parameters` against expected values in the dataset.

**Alternatives considered**:

- Prompt-based SQL safety evaluation: rejected because SQL safety is deterministic and code-based checks are more reliable than LLM judgment.
- Generic accuracy evaluator: rejected because parameter extraction requires field-level comparison, not just text similarity.

## R-007: Custom Prompt Evaluator Design

**Decision**: Implement custom prompt evaluators for local evaluation and keep catalog registration as a follow-on enhancement. Business answer adequacy evaluator scores against per-query `expected_behavior` rubric (1-5 scale). Clarification quality evaluator scores whether clarification questions are single-question, minimally ambiguous, and actionable (boolean pass/fail).

**Rationale**: These dimensions require LLM judgment against domain-specific rubrics. The `expected_behavior` field in datasets provides per-query scoring context. Cloud-mode evaluator mapping currently prioritizes built-in evaluators and deterministic custom-code evaluators, while prompt-evaluator catalog registration is deferred to reduce rollout risk.

**Alternatives considered**:

- Generic relevance for answer quality: rejected because relevance doesn't capture business-specific adequacy (e.g., "show me orders" needs tabular data, not a text summary).
- Code-based clarification check: rejected because clarification quality is subjective and requires LLM judgment.

## R-008: Failure Clustering Approach

**Decision**: Post-evaluation clustering by failure category: intent misroute (routing accuracy failures), extraction errors (parameter extraction mismatches), validator rejections (SQL safety/policy failures), poor answer quality (low relevance/adequacy scores). Clustering is implemented as a Python analysis module that groups evaluation results by failure type and generates summary reports.

**Rationale**: The NL2SQL pipeline has clear stage boundaries (intent classification → template/dynamic routing → parameter extraction → validation → execution → response). Each stage maps to a failure cluster category. This alignment enables targeted remediation: routing failures fix `assistant_prompt.md`, extraction failures fix `parameter_extractor/prompt.md`, etc.

**Alternatives considered**:

- Automatic ML-based clustering: rejected as over-engineering for the initial implementation; manual category mapping to pipeline stages is sufficient.
- Flat failure list: rejected because without clustering, remediation efforts cannot be prioritized by impact.

## R-009: SSE Step Events for Evaluation

**Decision**: Emit step events using the existing `ProgressReporter` protocol and `emit_step_start`/`emit_step_end` pattern for evaluation lifecycle milestones: dataset loading, evaluator initialization, evaluation execution, result aggregation.

**Rationale**: The codebase already uses `contextvars`-based step event queues for SSE streaming. Evaluation runs triggered interactively (e.g., via an admin endpoint) should report progress through the same mechanism for UI consistency. Batch/CI runs skip SSE emission since there is no interactive consumer.

**Alternatives considered**:

- Separate WebSocket channel: rejected because SSE is the established streaming pattern.
- No progress reporting: rejected because long evaluation runs need visibility.

## R-010: Project Structure for Evaluation Code

**Decision**: New `src/backend/evaluations/` package containing: `runner.py` (evaluation orchestration), `evaluators/` (custom evaluator implementations), `datasets/` (gold dataset JSONL files), `analysis.py` (failure clustering and delta reporting). CI workflow in `.github/workflows/`.

**Rationale**: Evaluation code is a cross-cutting concern that touches all pipeline stages but doesn't belong inside any single executor. A dedicated `evaluations/` package follows the single-responsibility principle from the constitution while keeping evaluation logic co-located. Gold datasets in source control enable review and versioning.

**Alternatives considered**:

- Evaluation code in `tests/`: rejected because evaluations are a runtime/CI capability, not test fixtures.
- Evaluation code in each executor: rejected because evaluation orchestration spans multiple executors.
