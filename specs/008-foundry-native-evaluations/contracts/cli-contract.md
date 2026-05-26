# CLI Contract: evaluations

**Module**: `python -m evaluations`
**Interface Type**: Command-line interface (argparse)
**Consumers**: GitHub Actions workflows, operators

## Subcommands

### `evaluations setup`

Create or update the continuous evaluation rule in the Foundry project.

```
python -m evaluations setup [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--rule-id` | str | `cadence-continuous-eval` | Stable rule identifier |
| `--agent-name` | str | env `AZURE_AI_AGENT_NAME` | Agent name filter |
| `--max-hourly-runs` | int | 100 | Sampling cap |
| `--dry-run` | flag | False | Print config without submitting |
| `--out` | path | None | Write report JSON to file |

**Exit codes**: 0 = success (or dry-run), 1 = submission failure

---

### `evaluations benchmark`

Run golden dataset agent target evaluation.

```
python -m evaluations benchmark [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--dataset-version` | str | env `AZURE_FOUNDRY_DATASET_VERSION` | Dataset version to evaluate |
| `--evaluation-id` | str | `cadence-eval-benchmark` | Target evaluation in portal |
| `--agent-name` | str | env `AZURE_AI_AGENT_NAME` | Registered agent name |
| `--agent-version` | str | env `AZURE_AI_AGENT_VERSION` | Agent version |
| `--dry-run` | flag | False | Resolve config without submitting |
| `--out` | path | None | Write report JSON to file |

**Exit codes**: 0 = success or partial (per-row errors), 1 = submission failure

---

### `evaluations trace`

Run trace-based evaluation against production traces.

```
python -m evaluations trace [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--window` | str | `24h` | Lookback window (e.g., "24h", "48h", "7d") |
| `--trace-ids` | path | None | File with one trace ID per line (overrides agent filter) |
| `--evaluation-id` | str | `cadence-eval-traces` | Target evaluation in portal |
| `--agent-id` | str | env `AZURE_AI_AGENT_ID` | Agent ID (`name:version` format) |
| `--max-traces` | int | 200 | Maximum traces to evaluate |
| `--dry-run` | flag | False | Resolve filter without submitting |
| `--out` | path | None | Write report JSON to file |

**Exit codes**: 0 = success, partial, or zero-traces-in-window, 1 = submission failure

**Zero traces behavior**: When lookback yields zero qualifying traces, exit 0 with report noting `"status": "no_traces"`. Do NOT submit empty evaluation.

---

### `evaluations dataset upload`

Upload golden dataset to Foundry project as a versioned dataset.

```
python -m evaluations dataset upload [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--file` | path | Required | JSONL file to upload |
| `--version` | str | Auto from filename | Dataset version |
| `--dry-run` | flag | False | Validate without uploading |
| `--out` | path | None | Write upload report to file |

**Exit codes**: 0 = success (or already exists), 1 = upload failure

---

## Environment Variables

All subcommands read from environment when CLI options are not provided:

| Variable | Used by | Description |
|----------|---------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | all | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | benchmark, trace | Judge model deployment |
| `AZURE_AI_AGENT_NAME` | setup, benchmark | Registered agent name |
| `AZURE_AI_AGENT_VERSION` | benchmark | Agent version |
| `AZURE_AI_AGENT_ID` | trace | Agent ID (`name:version`) |
| `AZURE_FOUNDRY_DATASET_NAME` | benchmark | Dataset name in Foundry |
| `AZURE_FOUNDRY_DATASET_VERSION` | benchmark | Dataset version |
| `AZURE_AI_TRACE_LOOKBACK_HOURS` | trace | Lookback in hours (alt to --window) |
| `AZURE_AI_TRACE_MAX_TRACES` | trace | Max traces (alt to --max-traces) |

## Report Output Format

All `--out` files produce JSON matching the `EvalRunReport` schema:

```json
{
  "run_id": "run-abc-123",
  "evaluation_id": "cadence-eval-traces",
  "layer": "trace",
  "status": "completed",
  "report_url": "https://ai.azure.com/...",
  "submitted_at": "2026-05-25T06:00:00Z",
  "completed_at": "2026-05-25T06:05:23Z",
  "metrics_summary": {
    "coherence": 4.2,
    "task_adherence": 3.8,
    "intent_resolution": 4.5
  },
  "total_rows": 48,
  "error_rows": 2,
  "filter_params": {
    "agent_id": "DataAssistant:1",
    "lookback_window": "PT24H"
  },
  "dry_run": false
}
```

## Backward Compatibility

The legacy `evaluations run` and `evaluations harvest` subcommands are removed (FR-019). The `evaluations replay` entry point is removed. No transitional dual-path.
