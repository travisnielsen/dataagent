# Tasks: Foundry Evaluations for NL2SQL

**Input**: Design documents from `/specs/005-foundry-evaluations/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency installation, and evaluation package scaffolding

- [x] T001 Add `azure-ai-evaluation` dependency to `pyproject.toml` and run `uv sync --all-extras --dev`
- [x] T002 Create evaluation package structure with `src/backend/evaluations/__init__.py` and `src/backend/evaluations/evaluators/__init__.py`
- [x] T003 [P] Create `.foundry/` directory structure with `.foundry/agent-metadata.yaml`, `.foundry/datasets/`, `.foundry/evaluators/`, `.foundry/results/` and add `.foundry/datasets/`, `.foundry/evaluators/`, `.foundry/results/` to `.gitignore`
- [x] T004 [P] Add `src/backend/evaluations/datasets/` directory with `.gitkeep` for gold dataset storage

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core evaluation models and configuration that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Implement `ThresholdRule`, `EvaluatorRef`, and `EvaluationConfig` Pydantic models in `src/backend/evaluations/config.py` per data-model.md
- [x] T006 Implement `DatasetRecord` and `DatasetMetadata` Pydantic models in `src/backend/evaluations/models.py` per data-model.md and `contracts/dataset-record.schema.json`
- [x] T007 Implement `EvaluationRun`, `RunSummary`, and `MetricResult` Pydantic models in `src/backend/evaluations/models.py` per data-model.md and `contracts/evaluation-run-summary.schema.json`
- [x] T008 Implement `FailureRecord`, `FailureCluster`, `DeltaComparison`, `MetricDelta`, and `QualityGateDecision` Pydantic models in `src/backend/evaluations/models.py` per data-model.md
- [x] T009 [P] Implement evaluator profile models (`BuiltinEvaluatorProfile`, `CustomCodeEvaluatorProfile`, `CustomPromptEvaluatorProfile`) in `src/backend/evaluations/config.py` per data-model.md
- [x] T010 Define default evaluation contract with primary metrics (intent routing accuracy, clarification quality, SQL safety, answer usefulness) and secondary metrics (tool-call success, latency, zero-result quality) as default `EvaluationConfig` in `src/backend/evaluations/config.py`
- [x] T011 Export all public models and config from `src/backend/evaluations/__init__.py`

**Checkpoint**: Foundation ready — all evaluation models, config, and contract are defined. User story implementation can begin.

---

## Phase 3: User Story 1 — Establish an Evaluation Contract (Priority: P1) 🎯 MVP

**Goal**: Publish the NL2SQL evaluation contract with primary/secondary metrics, pipeline-specific targets, threshold definitions, and reporting format aligned to architecture components.

**Independent Test**: Review the published evaluation contract and verify that each pipeline target and metric has a definition, threshold owner, and reporting format.

### Implementation for User Story 1

- [x] T012 [US1] Define Phase 1 built-in evaluator profiles (intent_resolution, task_adherence, tool_call_accuracy, relevance, indirect_attack) with threshold defaults in `src/backend/evaluations/config.py`
- [x] T013 [P] [US1] Define pipeline-specific evaluation targets mapping DataAssistant, ParameterExtractor, QueryValidator, QueryBuilder to corresponding metrics in `src/backend/evaluations/config.py`
- [x] T014 [P] [US1] Define Phase 2 custom evaluator profiles (sql_safety, param_extraction_correctness, answer_adequacy, clarification_quality) with threshold defaults in `src/backend/evaluations/config.py`
- [x] T015 [US1] Implement `load_config()` and `save_config()` functions for reading/writing evaluation configuration from YAML in `src/backend/evaluations/config.py`
- [x] T016 [US1] Write unit tests for evaluation config loading, validation, and default contract completeness in `tests/unit/test_eval_config.py`

**Checkpoint**: Evaluation contract is defined, versioned, and testable. All metrics have definitions and thresholds.

---

## Phase 4: User Story 2 — Build and Version Evaluation Datasets (Priority: P1)

**Goal**: Create curated gold dataset and trace-harvested dataset with versioning, so test coverage reflects intended behavior and real production usage.

**Independent Test**: Build one curated dataset and one trace-harvested dataset, version both, and verify they can be reused for evaluation runs.

### Implementation for User Story 2

- [x] T017 [US2] Implement dataset loading and validation functions (`load_dataset()`, `validate_dataset()`) in `src/backend/evaluations/runner.py` — validates JSONL records against `DatasetRecord` schema
- [x] T018 [US2] Curate initial gold dataset `cadence-eval-gold-v1.jsonl` with 200-500 prompts spanning template, dynamic, clarification, what-if, and conversation scenario classes in `src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl`
- [x] T019 [US2] Extract P0 subset `cadence-eval-p0-v1.jsonl` (~50 critical prompts covering core scenarios) from gold dataset in `src/backend/evaluations/datasets/cadence-eval-p0-v1.jsonl`
- [x] T020 [P] [US2] Implement `DatasetMetadata` generation — compute `record_count`, `scenario_distribution`, and `sanitization_status` from loaded JSONL in `src/backend/evaluations/runner.py`
- [x] T021 [US2] Implement trace harvesting from Foundry in `src/backend/evaluations/harvest.py` — uses `AIProjectClient` to query Foundry sessions, extracts user/assistant message pairs, and converts to `DatasetRecord` format
- [x] T022 [US2] Implement data sanitization pass in `src/backend/evaluations/harvest.py` — removes/masks sensitive fields (emails, SSN, credit cards, phone) from trace-harvested records before dataset persistence
- [x] T023 [US2] Implement dataset versioning and merging in `src/backend/evaluations/harvest.py` — version tagging (`v<N>` convention), `merge_datasets()` function to combine gold and trace records with optional deduplication, and `dataset_uri` persistence
- [x] T024 [US2] Write unit tests for dataset loading, validation, metadata generation, and sanitization in `tests/unit/test_eval_datasets.py`

**Checkpoint**: Gold dataset and Foundry trace harvesting pipeline are functional. Mixed datasets (gold + harvested traces) are versioned and bound to evaluation runs. Nightly workflow auto-harvests and merges before evaluation.

---

## Phase 5: User Story 3 — Run Built-In and Custom Evaluators with CI Gates (Priority: P1)

**Goal**: Execute standardized evaluator phases (built-in Phase 1 + custom Phase 2), orchestrate CI gating, and produce run summaries.

**Independent Test**: Execute a CI run on the P0 subset and verify threshold regressions fail the merge gate; execute a full-suite run and verify summary publication.

### Implementation for User Story 3 — Phase 1 Built-In Evaluators

- [x] T025 [US3] Implement `run_evaluation()` async function in `src/backend/evaluations/runner.py` — orchestrates dataset load → evaluator init → evaluation execution → result aggregation
- [x] T026 [US3] Integrate Phase 1 built-in evaluators (`IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, `RelevanceEvaluator`, `ToolCallAccuracyEvaluator`, `indirect_attack`) via `azure-ai-evaluation` SDK in `src/backend/evaluations/runner.py`
- [x] T027 [US3] Implement conversation history preparation — include full message history with tool calls and tool results as evaluator input in `src/backend/evaluations/runner.py` (FR-009)
- [x] T028 [US3] Implement `RunSummary` aggregation — compute `MetricResult` statistics (mean, median, p5, p95, pass_rate) and `overall_pass` from per-record scores in `src/backend/evaluations/runner.py`
- [x] T029 [P] [US3] Implement SSE step events for evaluation lifecycle using existing `ProgressReporter` protocol in `src/backend/evaluations/runner.py` — emits dataset_loading, evaluator_init, evaluation_running, result_aggregation milestones
- [x] T030 [P] [US3] Implement `QualityGateDecision` computation — check P0 thresholds, compile failing metrics, produce gate pass/fail in `src/backend/evaluations/runner.py`

### Implementation for User Story 3 — Phase 2 Custom Code Evaluators

- [x] T031 [US3] Implement SQL safety code evaluator in `src/backend/evaluations/evaluators/sql_safety.py` — reuses `query_validator` logic for allowed tables, SELECT-only, parameterized execution, no injection patterns; returns boolean pass/fail
- [x] T032 [P] [US3] Implement parameter extraction correctness evaluator in `src/backend/evaluations/evaluators/param_extraction.py` — compares extracted parameters against `ground_truth_params` with field-level match scoring

### Implementation for User Story 3 — Phase 2 Custom Prompt Evaluators

- [x] T033 [US3] Implement business answer adequacy prompt evaluator in `src/backend/evaluations/evaluators/answer_adequacy.py` — LLM-judge scoring response against `expected_behavior` rubric (1-5 ordinal scale)
- [x] T034 [P] [US3] Implement clarification quality prompt evaluator in `src/backend/evaluations/evaluators/clarification_quality.py` — LLM-judge scoring for single-question, minimally ambiguous, actionable criteria (boolean pass/fail)

### Implementation for User Story 3 — CI Workflows

- [x] T035 [US3] Create PR gate GitHub Actions workflow in `.github/workflows/eval-pr-gate.yml` — triggers on PR to `main` modifying `src/backend/`, runs P0 subset, fails on threshold regression, posts metric summary as PR comment
- [x] T036 [P] [US3] Create nightly evaluation GitHub Actions workflow in `.github/workflows/eval-nightly.yml` — scheduled daily cron, runs full suite with `--cloud`, publishes results, opens GitHub issue on regression

### Implementation for User Story 3 — CLI Entry Point

- [x] T037 [US3] Implement CLI entry point `src/backend/evaluations/__main__.py` — supports `--dataset`, `--evaluators`, `--trigger`, `--gate`, `--cloud` flags per quickstart.md

### Tests for User Story 3

- [x] T038 [P] [US3] Write unit tests for evaluation runner orchestration (mocked evaluators, dataset load, summary aggregation) in `tests/unit/test_eval_runner.py`
- [x] T039 [P] [US3] Write unit tests for SQL safety evaluator (known-safe and known-unsafe queries) in `tests/unit/test_eval_sql_safety.py`
- [x] T040 [P] [US3] Write unit tests for parameter extraction evaluator (exact/partial/missing parameter matches) in `tests/unit/test_eval_param_extraction.py`
- [x] T041 [P] [US3] Write unit tests for quality gate decision logic (pass, fail, waiver scenarios) in `tests/unit/test_eval_runner.py`

**Checkpoint**: Full evaluation pipeline is functional — built-in + custom evaluators run, CI gates enforce thresholds, nightly runs publish trends.

---

## Phase 6: User Story 4 — Close the Optimization Loop (Priority: P2)

**Goal**: Cluster evaluation failures by pipeline stage, enable targeted remediation, and measure improvement via delta comparison against the same dataset version.

**Independent Test**: Cluster failures from one run, apply one targeted fix, re-run against the same dataset version, and verify measurable delta.

### Implementation for User Story 4

- [x] T042 [US4] Implement failure clustering in `src/backend/evaluations/analysis.py` — group `FailureRecord` entries by cluster type (intent_misroute, extraction_error, validator_rejection, poor_answer_quality, safety_violation, tool_call_failure)
- [x] T043 [US4] Implement `FailureCluster` summary generation in `src/backend/evaluations/analysis.py` — compute representative queries, remediation targets (mapping cluster → prompt/config file), and severity levels
- [x] T044 [US4] Implement delta comparison in `src/backend/evaluations/analysis.py` — compare two `RunSummary` results against the same dataset version, produce `DeltaComparison` with per-metric `MetricDelta` and regression detection
- [x] T045 [P] [US4] Implement analysis CLI entry point in `src/backend/evaluations/analysis.py` — supports `--run-id` for cluster report and `--compare <before> <after>` for delta report per quickstart.md
- [x] T046 [US4] Write unit tests for failure clustering and delta comparison with synthetic evaluation results in `tests/unit/test_eval_analysis.py`

**Checkpoint**: Failure analysis and remediation loop are functional. Deltas are measurable and attributable.

---

## Phase 7: User Story 5 — Roll Out in Phases with Production Feedback (Priority: P2)

**Goal**: Implement phased rollout controls — Week 1 baseline with built-in evaluators, Week 2 CI gating with custom evaluators, Week 3+ production feedback loop with nightly trace refresh.

**Independent Test**: Verify week-by-week deliverables are completed and corresponding governance checks are in place.

### Implementation for User Story 5

- [x] T047 [US5] Implement Foundry cloud evaluation path in `src/backend/evaluations/runner.py` — uses `azure-ai-evaluation` `evaluate(..., azure_ai_project=...)` for full-suite cloud runs with local summary persistence
- [x] T048 [US5] Implement Foundry cloud evaluator mapping in `src/backend/evaluations/runner.py` — wire built-ins and deterministic custom-code evaluators; warn for unsupported cloud evaluators
- [x] T049 [US5] Implement run result persistence to `.foundry/results/<run-id>.json` and integration with `.foundry/agent-metadata.yaml` test cases in `src/backend/evaluations/runner.py`
- [x] T050 [P] [US5] Implement App Insights correlation — attach `correlation_id` to evaluation runs and emit OpenTelemetry spans linking evaluation outcomes to request flow stages in `src/backend/evaluations/runner.py` (FR-019)
- [x] T051 [US5] Implement nightly trace-to-dataset refresh automation — `harvest` subcommand queries Foundry traces, merges with gold dataset via `merge_datasets()`, persists versioned mixed dataset; nightly GitHub workflow runs harvest pre-step before evaluation
- [x] T052 [US5] Write unit tests for Foundry cloud evaluation path and result persistence (mocked API calls) in `tests/unit/test_eval_runner.py`

**Checkpoint**: Phased rollout is complete — baseline established, CI gating active, production feedback loop operational.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T053 [P] Add evaluation settings (`eval_judge_model_deployment`, `eval_default_dataset`) to `src/backend/config/settings.py` with corresponding `.env.example` entries
- [x] T054 [P] Update `src/backend/evaluations/__init__.py` with complete public API exports for all models, runner, analysis, and harvest functions
- [x] T055 Run `uv run poe check` to verify all evaluation code passes linting, type checking, and tests
- [x] T056 [P] Validate quickstart.md commands end-to-end against implemented CLI entry points
- [x] T057 Update `pyproject.toml` to include `src/backend/evaluations` in lint/typecheck/test coverage paths if not already covered
- [x] T058 Verify evaluation package uses lazy imports or conditional guards so existing NL2SQL runtime is unaffected when `azure-ai-evaluation` is unavailable or evaluations are disabled (FR-021)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup (Phase 1) — BLOCKS all user stories
- **US1 — Evaluation Contract (Phase 3)**: Depends on Foundational (Phase 2)
- **US2 — Datasets (Phase 4)**: Depends on Foundational (Phase 2) — can run in parallel with US1
- **US3 — Evaluators & CI (Phase 5)**: Depends on US1 (T012-T015 for config) and US2 (T017-T019 for datasets)
- **US4 — Optimization Loop (Phase 6)**: Depends on US3 (T025-T028 for runner and summary output)
- **US5 — Phased Rollout (Phase 7)**: Depends on US3 (T035-T036 for CI workflows) and US4 (T042-T044 for analysis)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

```
Phase 1 (Setup)
    │
    ▼
Phase 2 (Foundational) ──── BLOCKS ALL ────┐
    │                                       │
    ├──► Phase 3 (US1: Contract) ──┐        │
    │                              ├──► Phase 5 (US3: Evaluators & CI)
    └──► Phase 4 (US2: Datasets) ──┘        │
                                            ▼
                                   Phase 6 (US4: Optimization Loop)
                                            │
                                            ▼
                                   Phase 7 (US5: Phased Rollout)
                                            │
                                            ▼
                                   Phase 8 (Polish)
```

### Parallel Opportunities per Phase

- **Phase 1**: T003 and T004 can run in parallel
- **Phase 2**: T006, T007, T008, T009 can all run in parallel (separate model groups in same file — coordinate merges)
- **Phase 3**: T013 and T014 can run in parallel
- **Phase 4**: T020 is parallelizable with T021-T023
- **Phase 5**: T029/T030 parallel; T031/T032 parallel; T033/T034 parallel; T035/T036 parallel; T038-T041 all parallel
- **Phase 6**: T045 is parallelizable with T042-T044
- **Phase 7**: T050 is parallelizable with T047-T049

### Implementation Strategy

**MVP (minimum viable delivery)**: Phases 1-3 + T017-T019 from Phase 4 + T025-T030 from Phase 5 = evaluation contract + gold dataset + built-in evaluators running locally. This covers the Week 1 baseline from the phased rollout plan.

**Week 2 target**: Add T031-T036 (custom evaluators + CI workflows) to activate merge gating.

**Week 3+ target**: Add Phases 6-8 (optimization loop, trace harvesting, production feedback).

---

## Summary

| Metric | Count |
|--------|-------|
| Total tasks | 57 |
| Phase 1 (Setup) | 4 |
| Phase 2 (Foundational) | 7 |
| Phase 3 (US1: Contract) | 5 |
| Phase 4 (US2: Datasets) | 8 |
| Phase 5 (US3: Evaluators & CI) | 17 |
| Phase 6 (US4: Optimization Loop) | 5 |
| Phase 7 (US5: Rollout) | 6 |
| Phase 8 (Polish) | 5 |
| Parallelizable tasks | 26 |
