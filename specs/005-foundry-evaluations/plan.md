# Implementation Plan: Foundry Evaluations for NL2SQL

**Branch**: `005-foundry-evaluations` | **Date**: 2026-03-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-foundry-evaluations/spec.md`

## Summary

Integrate Microsoft Foundry evaluation capabilities into the NL2SQL multi-agent pipeline to enable systematic quality measurement and evaluation-driven optimization. Implementation uses **ONLY** the Foundry native REST API with built-in evaluators, versioned JSONL datasets (gold-curated), and nightly GitHub Actions CI/CD workflow. Evaluations are executed exclusively via `run_cloud_evaluation()` in `src/backend/evaluations/runner.py` with **NO fallback to local evaluation** — if Foundry is unavailable, the run fails fast.

This is the single and only evaluation path in this repository. The `azure-ai-evaluation` SDK is **NOT** used for production evaluation workflows.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `azure-ai-evaluation`, `azure-ai-projects` (via `agent-framework`), `azure-identity`, FastAPI, Pydantic v2
**Storage**: JSONL files (datasets), Foundry project (cloud eval runs and session traces)
**Testing**: `pytest`/`pytest-asyncio`, `uv run poe check`
**Target Platform**: Linux-hosted backend + GitHub Actions CI
**Project Type**: Backend evaluation package added to existing web-service monorepo
**Performance Goals**: PR gate evaluation completes within 10 minutes for ~50 P0 prompts; nightly full suite within 60 minutes for 200-500 prompts
**Constraints**: No blocking I/O in evaluation code paths; evaluation must not impact runtime request handling; gold datasets must be source-controlled
**Scale/Scope**: 200-500 evaluation dataset records (mixed gold+trace), 5 built-in + 4 custom evaluators, 2 CI workflows

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Async-First | **PASS** | Evaluation runner uses `async def` for Foundry API calls and dataset I/O. CI scripts run as standalone processes, not blocking the event loop. |
| II. Validated Data at Boundaries | **PASS** | All evaluation models (config, runs, results, datasets) are Pydantic `BaseModel`. Dataset records validated against schema on load. |
| III. Fully Typed | **PASS** | All evaluation functions fully typed with parameter and return annotations. |
| IV. Single-Responsibility Executors | **PASS** | Evaluation is a dedicated `evaluations/` package. Custom evaluators are individual modules. No cross-cutting into existing executor logic. |
| V. Automated Quality Gates | **PASS** | `uv run poe check` gate extended to cover evaluation code. CI workflows enforce evaluation thresholds before merge. |

**Post-Phase 1 Re-check**: PASS. Design preserves async boundaries, typed models at all interfaces, and single-responsibility separation between evaluation orchestration, custom evaluators, analysis, and trace harvesting.

## Project Structure

### Documentation (this feature)

```text
specs/005-foundry-evaluations/
├── plan.md                                       # This file
├── research.md                                   # Phase 0: technology decisions
├── data-model.md                                 # Phase 1: Pydantic models
├── quickstart.md                                 # Phase 1: developer guide
├── contracts/
│   ├── evaluation-run-request.schema.json        # Evaluation run request contract
│   ├── evaluation-run-summary.schema.json        # Run summary response contract
│   └── dataset-record.schema.json                # Dataset record format
└── tasks.md                                      # Phase 2: implementation checklist
```

### Source Code (repository root)

```text
src/backend/
├── evaluations/                          # NEW: Evaluation package
│   ├── __init__.py                       # Package exports
│   ├── config.py                         # EvaluationConfig, ThresholdRule, EvaluatorRef models
│   ├── models.py                         # EvaluationRun, RunSummary, MetricResult, etc.
│   ├── runner.py                         # Evaluation orchestration (dataset → evaluators → results)
│   ├── analysis.py                       # Failure clustering and delta comparison
│   ├── harvest.py                        # Trace-to-dataset pipeline (KQL extraction)
│   ├── evaluators/                       # Custom evaluator implementations
│   │   ├── __init__.py
│   │   ├── sql_safety.py                 # SQL safety policy evaluator (reuses query_validator)
│   │   ├── param_extraction.py           # Parameter extraction correctness evaluator
│   │   ├── answer_adequacy.py            # Business answer prompt evaluator (Phase 2)
│   │   └── clarification_quality.py      # Clarification question prompt evaluator (Phase 2)
│   └── datasets/                         # Gold evaluation datasets (source-controlled)
│       ├── cadence-eval-gold-v1.jsonl    # Full gold dataset (200-500 records)
│       └── cadence-eval-p0-v1.jsonl      # P0 subset for PR gate (~50 records)

.github/workflows/
├── eval-nightly.yml                      # ONLY evaluation workflow: nightly schedule via Foundry REST API
└── (eval-pr-gate.yml removed 2026-05-03) # PR gate: removed to simplify CI, no evaluations in PR flow

.foundry/                                 # Foundry workspace state
├── agent-metadata.yaml                   # Agent config with testCases[]
├── datasets/                             # Trace-harvested datasets (local cache)
├── evaluators/                           # Evaluator definitions (local cache)
└── results/                              # Evaluation run results (local cache)

tests/unit/
├── test_eval_runner.py                   # Runner orchestration tests
├── test_eval_sql_safety.py               # SQL safety evaluator tests
├── test_eval_param_extraction.py         # Parameter extraction evaluator tests
└── test_eval_analysis.py                 # Failure clustering and delta tests
```

**Structure Decision**: Extend the existing backend monorepo with a new `evaluations/` package at `src/backend/evaluations/`. This keeps evaluation code co-located with the pipeline it evaluates while maintaining single-responsibility separation. CI workflows live in `.github/workflows/`. Gold datasets are source-controlled in the evaluations package; trace-harvested datasets use `.foundry/` local cache.

## Phase Plan

### Phase 0: Research and Decision Lock

- Confirm `azure-ai-evaluation` SDK compatibility with existing `agent-framework` dependency.
- Confirm built-in evaluator availability: `intent_resolution`, `task_adherence`, `tool_call_accuracy`, `relevance`, `indirect_attack`.
- Confirm KQL trace harvesting patterns against current Application Insights instrumentation.
- Confirm Foundry project endpoint supports `azure-ai-evaluation` cloud publishing via `azure_ai_project`.
- Research deliverables: [research.md](research.md).

### Phase 1: Evaluation Foundation (P1 — Stories 1, 2, 3)

**Slice 1a: Models and Configuration**
- Implement Pydantic models: `EvaluationConfig`, `ThresholdRule`, `EvaluatorRef`, `DatasetRecord`, `DatasetMetadata`, `EvaluationRun`, `RunSummary`, `MetricResult`, `FailureRecord`, `FailureCluster`, `DeltaComparison`, `MetricDelta`, `QualityGateDecision`.
- Define evaluation contract with primary metrics (intent routing accuracy, clarification quality, SQL safety, answer usefulness) and secondary metrics (tool-call success, latency percentiles, zero-result quality).
- Store threshold defaults in `config.py`.

**Slice 1b: Gold Dataset Curation**
- Create initial gold dataset `cadence-eval-gold-v1.jsonl` with 200-500 prompts spanning template queries, dynamic queries, clarifications, what-if scenarios, and conversational turns.
- Create P0 subset `cadence-eval-p0-v1.jsonl` (~50 critical prompts).
- Each record includes `query`, `expected_behavior`, `scenario_class`, and optional `ground_truth_sql`/`ground_truth_params`.

**Slice 1c: Evaluation Runner with Foundry REST API**
- Implement `runner.py` with `run_cloud_evaluation()` async function using **ONLY** Foundry native REST APIs.
- Integrate built-in evaluators via Azure AI Projects SDK: `intent_resolution`, `task_adherence`, `relevance`, `tool_call_accuracy`, `indirect_attack`.
- Use Azure AI Projects `AIProjectClient` to:
  - Load Foundry dataset assets by name and version
  - Create evaluation definitions via `POST /openai/v1/evals`
  - Submit async evaluation runs via `POST /openai/v1/evals/{eval_id}/runs`
  - Poll for results via `GET /openai/v1/evals/{eval_id}/runs/{run_id}`
- **NO** fallback to local evaluation — if Foundry is unavailable, fail fast
- CLI support via `--cloud` flag (only method available)
- Emit SSE step events for evaluation lifecycle using existing `ProgressReporter` protocol

**Slice 1d: Unit Tests for Foundation**
- Test model validation, config loading, dataset loading, runner orchestration (with mocked evaluators).

### Phase 2: Custom Evaluators and Analysis (P1 — Story 3, P2 — Story 4)

**Slice 2a: Custom Code Evaluators**
- `sql_safety.py`: Reuses `query_validator` logic to check allowed tables, SELECT-only, parameterized execution, no injection patterns. Returns boolean pass/fail.
- `param_extraction.py`: Compares `SQLDraft.extracted_parameters` against `ground_truth_params`. Scores field-level match accuracy.

**Slice 2b: Custom Prompt Evaluators**
- `answer_adequacy.py`: LLM-judge evaluator scoring response against `expected_behavior` rubric (1-5 ordinal scale). Registered in Foundry evaluator catalog.
- `clarification_quality.py`: LLM-judge evaluator scoring clarification questions for single-question, minimally ambiguous, actionable criteria (boolean pass/fail).

**Slice 2c: Failure Analysis**
- `analysis.py`: Cluster failures by pipeline stage: intent misroute, extraction error, validator rejection, poor answer quality, safety violation, tool call failure.
- Generate `FailureCluster` summaries with representative queries and remediation targets.
- Delta comparison between two runs against the same dataset version.

**Slice 2d: Unit Tests for Custom Evaluators**
- Test SQL safety evaluator against known-safe and known-unsafe queries.
- Test parameter extraction evaluator against exact/partial/missing parameter matches.
- Test failure clustering with synthetic evaluation results.

### Phase 3: CI/CD Integration (P1 — Story 3)

**Slice 3a: Nightly Evaluation Workflow (ONLY evaluation in CI)**
- `.github/workflows/eval-nightly.yml`: Scheduled cron (daily).
- Runs full evaluation suite using `run_cloud_evaluation()` via Foundry native REST API.
- Invokes: `python -m evaluations run --dataset cadence-eval-gold-v1.jsonl --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack --trigger nightly --cloud`
- Publishes results to `.foundry/results/` and opens GitHub issue on metric regression.
- **NOTE**: PR gate evaluation removed as of 2026-05-03 (commit cdf5b10) — CI focuses on code quality and tests only, evaluations run nightly only

**Slice 3b: Trace Harvesting Pipeline**
- `harvest.py`: KQL-based extraction from Application Insights.
- Error harvest, latency harvest, low-eval-score harvest templates.
- Sanitization pass for sensitive data removal.
- Human review gate before dataset persistence.
- Version and persist as `.foundry/datasets/cadence-traces-vN.jsonl`.

**Slice 3d: Optimization Loop**
- Re-run same dataset version after remediation.
- Delta comparison report with measured improvements.
- Integration with `.foundry/` cache for Foundry artifact persistence.

## Complexity Tracking

No constitution violations to justify. All principles pass cleanly.
