# Evaluation Framework for NL2SQL

Official evaluation framework for the Cadence NL2SQL multi-agent pipeline using **Microsoft Foundry native REST APIs**.

## ⚠️ CRITICAL: Evaluation Method

This repository uses **ONLY** the Foundry native REST API for all evaluation execution. This is the single and only supported method.

**Supported Method**: `run_cloud_evaluation()` in [`runner.py`](runner.py)
**NOT Supported**: Local evaluation, `azure-ai-evaluation` SDK in CI, fallback paths

See [`spec.md`](../../specs/005-foundry-evaluations/spec.md) for the full architectural decision.

## Usage

### Run Evaluations (Nightly Schedule)

```bash
# Foundry REST API method (ONLY supported)
set -a && source .env && set +a
PYTHONPATH=src/backend python -m evaluations run \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack \
  --trigger nightly \
  --cloud
```

**Result**: Evaluation runs in Foundry, results visible in Foundry Studio.

## Architecture

### `run_cloud_evaluation()` — Foundry Native REST API

Located in [`runner.py:360`](runner.py#L360)

**Authentication**: Azure DefaultAzureCredential
**Endpoints**:

- `POST /openai/v1/evals` — Create evaluation definition
- `POST /openai/v1/evals/{eval_id}/runs` — Submit async run
- `GET /openai/v1/evals/{eval_id}/runs/{run_id}` — Poll results

**Supported Evaluators** (built-in only):

- `intent_resolution` (0-5 scale)
- `task_adherence` (0-1 scale)
- `relevance` (0-5 scale)
- `tool_call_accuracy` (0-1 scale)
- `indirect_attack` (0-1 scale)

### CI/CD Policy

| Environment | Method | Status |
|-------------|--------|--------|
| PR Flow (ci.yml) | Tests only (no eval) | ✅ Active |
| Nightly (eval-nightly.yml) | Foundry REST API | ✅ Active |
| Local Dev | `--cloud` flag | ✅ Supported |
| Local Fallback | N/A | ❌ **NOT SUPPORTED** |

### Configuration

See [`config.py`](config.py):

- `EvaluationConfig`: Dataset name, version, Foundry project endpoint, model deployment
- `DEFAULT_THRESHOLDS`: P0/P1/P2 metric thresholds with min_score requirements

### Models

See [`models.py`](models.py):

- `DatasetRecord`: Query, expected behavior, scenario class
- `EvaluationRun`: Run metadata (ID, status, trigger, git SHA, branch)
- `RunSummary`: Aggregated metrics and pass rates
- `MetricResult`: Per-metric aggregations
- `QualityGateDecision`: Pass/fail gate result

## File Structure

```
evaluations/
├── __init__.py              # Package exports
├── __main__.py              # CLI entry point
├── config.py                # Configuration and thresholds
├── models.py                # Pydantic models
├── runner.py                # run_cloud_evaluation() — OFFICIAL METHOD
├── analysis.py              # Failure clustering and deltas
├── harvest.py               # Trace harvesting (KQL)
├── dataset_provisioner.py   # Dataset management
├── evaluators/              # Custom evaluator implementations
│   ├── __init__.py
│   ├── sql_safety.py        # SQL safety evaluator
│   ├── param_extraction.py  # Parameter extraction evaluator
│   └── ...
└── datasets/                # Gold datasets (source-controlled)
    ├── cadence-eval-gold-v1.jsonl
    └── ...
```

## When You Need to Modify This Code

1. **Bug in Foundry integration**: Edit `_submit_cloud_evaluation()` in `runner.py`
2. **Change metric thresholds**: Edit `DEFAULT_THRESHOLDS` in `config.py`
3. **Add new dataset**: Add to `src/backend/evaluations/datasets/`
4. **Extend evaluators**: Edit files in `evaluators/` (custom code only, built-ins are Foundry)

**DO NOT**:

- Add local evaluation fallback paths
- Import or use `azure-ai-evaluation` SDK in production code
- Remove the `--cloud` requirement
- Introduce alternative evaluation methods

Any future changes to evaluation methodology require updating:

1. This README
2. [`specs/005-foundry-evaluations/spec.md`](../../specs/005-foundry-evaluations/spec.md)
3. [`specs/005-foundry-evaluations/plan.md`](../../specs/005-foundry-evaluations/plan.md)
4. Commit with `feat(evals):` prefix and clear justification

## References

- Specification: [`specs/005-foundry-evaluations/spec.md`](../../specs/005-foundry-evaluations/spec.md)
- Implementation Plan: [`specs/005-foundry-evaluations/plan.md`](../../specs/005-foundry-evaluations/plan.md)
- API Docs: [Azure AI Evaluations REST API](https://learn.microsoft.com/en-us/azure/ai-studio/reference/rest-api-evaluations)
- Nightly Workflow: [`.github/workflows/eval-nightly.yml`](../../.github/workflows/eval-nightly.yml)
