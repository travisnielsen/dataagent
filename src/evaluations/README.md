# Evaluation Framework for NL2SQL

Offline evaluation harness for the Cadence NL2SQL multi-agent pipeline. Runs in
GitHub Actions and on developer machines; **never** ships inside the API
container.

## Why this lives in `src/evaluations/` (not `src/backend/`)

This package is a **sibling** of the backend, not a part of it.

- API container deploys ([cd-api.yml](../../.github/workflows/cd-api.yml))
  trigger on `src/backend/**` only — evaluation changes do not redeploy the
  API.
- [src/backend/Dockerfile](../backend/Dockerfile) copies `src/backend/` only —
  datasets, harvesters, judges, and CLI tooling stay out of the runtime image.
- The eval runner imports nothing from `src/backend/`; it talks to Foundry,
  Application Insights, and the deployed Cadence API via HTTP.

## Evaluation Method: Foundry Trace-Based Evaluation

Cadence uses **trace-based evaluations** on Microsoft Foundry. Foundry reads
`invoke_agent` OpenTelemetry spans from Application Insights and scores them
with built-in evaluators. There is **no fallback** to local/SDK evaluation.

Two-step pipeline:

1. **Replay** — `evaluations replay` hits the deployed Cadence API for every
   row in the gold dataset, exercising the real `NL2SQLController` pipeline.
   The backend emits OTel spans (`gen_ai.agent.id = "DataAssistant:1"`) into
   Application Insights.
2. **Evaluate** — `evaluations run --cloud` submits a Foundry trace eval that
   queries those spans by `agent_id` within a lookback window and runs the
   built-in judges (intent_resolution, task_adherence, relevance,
   tool_call_accuracy).

This means evaluators score the **real agent runtime** — not static
`expected_behavior` text — which is required for Foundry's failure-cluster
analysis to produce meaningful embeddings.

## Quick Start

### Nightly pipeline (CI)

See [.github/workflows/eval-nightly.yml](../../.github/workflows/eval-nightly.yml).
Runs on the self-hosted private runner with a UAMI that has Foundry data
access and can acquire an AAD token for the API audience.

### Local run against the deployed environment

```bash
# 1. Load .env (Foundry endpoint, model deployment, dataset name/version)
set -a && source src/backend/.env && set +a

# 2. Replay the gold dataset against the deployed API
PYTHONPATH=src \
CADENCE_API_BASE_URL="https://<api-fqdn>" \
AZURE_AD_CLIENT_ID="<api-app-registration-client-id>" \
uv run python -m evaluations replay \
  --dataset src/evaluations/datasets/cadence-eval-gold-v1.jsonl

# 3. Wait ~2-3 minutes for App Insights ingestion, then submit the eval
PYTHONPATH=src \
AZURE_AI_AGENT_ID="DataAssistant:1" \
AZURE_AI_TRACE_LOOKBACK_HOURS=1 \
uv run python -m evaluations run \
  --dataset src/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy \
  --trigger manual \
  --cloud
```

Results render in Foundry Studio.

## CLI Commands

| Command | Purpose |
|---|---|
| `evaluations replay`      | Drive traffic through the deployed API to emit OTel spans |
| `evaluations run --cloud` | Submit a Foundry trace evaluation that reads those spans |
| `evaluations harvest`     | Pull past traces from App Insights into a JSONL dataset for review |

Run `python -m evaluations <cmd> --help` for full options.

## Required Environment

| Variable | Used by | Source |
|---|---|---|
| `CADENCE_API_BASE_URL`            | `replay` | Container App URL (TF output → repo var `NEXT_PUBLIC_API_URL`) |
| `AZURE_AD_CLIENT_ID`              | `replay` | API app registration client id (TF output) |
| `AZURE_AI_PROJECT_ENDPOINT`       | `run`    | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME`  | `run` (judges) | Foundry deployment |
| `AZURE_AI_AGENT_ID`               | `run` (trace filter) | `<agent-name>:<version>`, e.g. `DataAssistant:1` |
| `AZURE_AI_TRACE_LOOKBACK_HOURS`   | `run`    | Defaults to `1` |
| `AZURE_AI_TRACE_MAX_TRACES`       | `run`    | Defaults to `200` |
| `AZURE_FOUNDRY_EVAL_NAME`         | `run`    | Stable eval definition name |

All Azure auth uses `DefaultAzureCredential` (UAMI on the runner; developer
identity locally).

## Replay Authentication

The Cadence API uses Azure AD JWT auth
([src/backend/api/middleware/auth.py](../backend/api/middleware/auth.py)). The
replay client acquires a token for `api://<AZURE_AD_CLIENT_ID>/.default` and
sends `Authorization: Bearer ...` on every `GET /api/chat/stream` request.

If token acquisition fails with `AADSTS50105` / `AADSTS500011`, the API's
enterprise application has user-assignment-required enabled — either disable
it or define an app role on the API app registration and assign it to the
replay caller's principal (UAMI in CI, your user locally).

## Foundry Trace Evaluation Internals

See [`runner.py`](runner.py) — `_submit_cloud_evaluation` builds the
trace-mode request:

```jsonc
// data_source_config
{ "type": "azure_ai_source", "scenario": "traces" }

// run data_source
{
  "type": "azure_ai_traces",
  "agent_id": "DataAssistant:1",
  "max_traces": 200,
  "lookback_hours": 1
}

// data_mapping uses item.* (no sample.* prefix in trace mode)
{ "query": "{{item.query}}", "response": "{{item.response}}" }
```

`tool_call_accuracy` additionally maps `tool_calls` and `tool_definitions`.

Endpoints used:

- `POST /openai/v1/evals` — create/reuse eval definition
- `POST /openai/v1/evals/{eval_id}/runs` — submit async run
- `GET  /openai/v1/evals/{eval_id}/runs/{run_id}` — poll status

Supported built-in evaluators: `intent_resolution`, `task_adherence`,
`relevance`, `tool_call_accuracy`, `indirect_attack`.

## File Structure

```text
src/evaluations/
├── __init__.py
├── __main__.py              # CLI entry point (run, replay, harvest)
├── config.py                # EvaluationConfig + DEFAULT_THRESHOLDS
├── models.py                # Pydantic models
├── runner.py                # run_cloud_evaluation (Foundry trace mode)
├── replay.py                # Drives traffic through the deployed API
├── harvest.py               # KQL-based trace harvesting
├── analysis.py              # Failure clustering and run deltas
├── dataset_provisioner.py   # Foundry dataset asset management
├── refresh_dataset.py       # Delete-and-recreate dataset version
├── evaluators/              # Custom evaluators (judged offline)
│   ├── sql_safety.py
│   └── param_extraction.py
└── datasets/
    └── cadence-eval-gold-v1.jsonl
```

## Modifying This Code

| Goal | Where |
|---|---|
| Bug in Foundry submission   | `_submit_cloud_evaluation` in [runner.py](runner.py) |
| Replay misbehaving          | [replay.py](replay.py) |
| Adjust metric thresholds    | `DEFAULT_THRESHOLDS` in [config.py](config.py) |
| Add a dataset               | drop a `.jsonl` into `datasets/` |
| Add a custom evaluator      | new module in `evaluators/` |

**Do not**:

- Add local-evaluation fallback paths.
- Import `azure-ai-evaluation` in production code.
- Import anything from `src/backend/` — keep this package decoupled so eval
  changes never trigger API redeploys.
- Switch back to dataset-mode evaluation (`{{sample.*}}`) — cluster analysis
  needs real traces.

## References

- Spec: [specs/005-foundry-evaluations/spec.md](../../specs/005-foundry-evaluations/spec.md)
- Plan: [specs/005-foundry-evaluations/plan.md](../../specs/005-foundry-evaluations/plan.md)
- Foundry REST API: <https://learn.microsoft.com/en-us/azure/ai-studio/reference/rest-api-evaluations>
- Nightly workflow: [.github/workflows/eval-nightly.yml](../../.github/workflows/eval-nightly.yml)
- Dataset update workflow: [.github/workflows/eval-update-dataset.yml](../../.github/workflows/eval-update-dataset.yml)
