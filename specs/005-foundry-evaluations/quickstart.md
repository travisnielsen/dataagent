# Quickstart: Foundry Evaluations for NL2SQL

**Feature**: 005-foundry-evaluations
**Date**: 2026-03-24

## Prerequisites

- Python 3.11+ with `uv` package manager
- Azure AI Foundry project with deployed model (e.g., `gpt-4o`)
- Application Insights connected to the Foundry project
- `.env` file configured in `src/backend/` with:
  - `AZURE_AI_PROJECT_ENDPOINT`
  - `AZURE_AI_MODEL_DEPLOYMENT_NAME`
  - `APPLICATIONINSIGHTS_CONNECTION_STRING`
  - `ENABLE_INSTRUMENTATION=true`

## Install Dependencies

```bash
# Add evaluation SDK to project dependencies
uv add azure-ai-evaluation
uv sync --all-extras --dev
```

## Run a Local Evaluation (Manual)

```bash
# Run evaluation against gold dataset with built-in evaluators
uv run python -m evaluations.runner \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack \
  --trigger manual

# Run with custom evaluators (Phase 2)
uv run python -m evaluations.runner \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,sql_safety,param_extraction_correctness \
  --trigger manual
```

## Run Evaluation in CI (PR Gate)

The PR gate runs automatically via GitHub Actions on pull requests that modify `src/backend/`:

```bash
# Equivalent manual command for the P0 subset
uv run python -m evaluations.runner \
  --dataset src/backend/evaluations/datasets/cadence-eval-p0-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack \
  --trigger ci_pr \
  --gate
```

The `--gate` flag enforces P0 thresholds and exits non-zero on regression.

## Analyze Failures

```bash
# Generate failure cluster report from the last run
uv run python -m evaluations.analysis \
  --run-id <run-id> \
  --output specs/005-foundry-evaluations/results/
```

## Compare Runs (Delta Report)

```bash
# Compare before/after remediation
uv run python -m evaluations.analysis \
  --compare <before-run-id> <after-run-id> \
  --dataset-version v1
```

## Harvest Traces to Dataset

Trace harvesting uses KQL queries against Application Insights. The workflow:

1. Run KQL harvest (error, latency, or low-eval patterns)
2. Review extracted candidates
3. Approve/edit/reject rows
4. Persist as versioned dataset in `.foundry/datasets/`

```bash
# Harvest error traces from last 7 days
uv run python -m evaluations.harvest \
  --type errors \
  --days 7 \
  --output .foundry/datasets/cadence-traces-v1.jsonl
```

## Project Layout

```text
src/backend/
├── evaluations/                      # NEW: Evaluation package
│   ├── __init__.py
│   ├── runner.py                     # Orchestration: dataset load, evaluator init, run, report
│   ├── analysis.py                   # Failure clustering and delta comparison
│   ├── harvest.py                    # Trace-to-dataset pipeline
│   ├── config.py                     # EvaluationConfig, ThresholdRule models
│   ├── models.py                     # EvaluationRun, RunSummary, etc.
│   ├── evaluators/                   # Custom evaluator implementations
│   │   ├── __init__.py
│   │   ├── sql_safety.py             # SQL safety policy evaluator (code-based)
│   │   └── param_extraction.py       # Parameter extraction correctness (code-based)
│   └── datasets/                     # Gold evaluation datasets (source-controlled)
│       └── cadence-eval-gold-v1.jsonl

.github/workflows/
├── eval-pr-gate.yml                  # PR gate: P0 subset evaluation
└── eval-nightly.yml                  # Nightly: full suite evaluation

.foundry/                             # Foundry workspace state (gitignored except metadata)
├── agent-metadata.yaml
├── datasets/                         # Trace-harvested datasets (local cache)
├── evaluators/                       # Evaluator definitions (local cache)
└── results/                          # Run results (local cache)

tests/unit/
├── test_eval_runner.py               # Evaluation runner tests
├── test_eval_sql_safety.py           # SQL safety evaluator tests
├── test_eval_param_extraction.py     # Parameter extraction evaluator tests
└── test_eval_analysis.py             # Failure clustering tests
```
