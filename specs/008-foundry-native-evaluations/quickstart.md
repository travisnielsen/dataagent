# Quickstart: Foundry-Native Multi-Layer Evaluations

## Prerequisites

- Feature 007 merged (agent traces tagged with `gen_ai.agent.id`)
- `azure-ai-projects` >= 2.0.0 installed (`uv sync --all-extras`)
- Azure authentication configured (UAMI or `az login`)
- Environment variables set (see below)

## Environment Setup

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://<project>.services.ai.azure.com"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o"
export AZURE_AI_AGENT_NAME="DataAssistant"
export AZURE_AI_AGENT_VERSION="1"
export AZURE_AI_AGENT_ID="DataAssistant:1"
export AZURE_FOUNDRY_DATASET_NAME="cadence-eval-gold"
export AZURE_FOUNDRY_DATASET_VERSION="v1"
```

## Layer 1: Setup Continuous Evaluation

```bash
# Dry run — see what would be configured
uv run python -m evaluations setup --dry-run --out /tmp/setup-report.json

# Apply the rule
uv run python -m evaluations setup --out /tmp/setup-report.json
```

Verify: Check the Foundry portal → Monitor tab → Continuous evaluation shows the rule.

## Layer 2: Run Golden Dataset Benchmark

```bash
# Upload the golden dataset (first time or after updates)
uv run python -m evaluations dataset upload \
  --file src/evaluations/data/golden_queries.jsonl

# Run benchmark evaluation
uv run python -m evaluations benchmark --out /tmp/benchmark-report.json

# Dry run to see resolved config
uv run python -m evaluations benchmark --dry-run --out /tmp/benchmark-dry.json
```

Verify: Check Foundry portal → Evaluations → `cadence-eval-benchmark` shows a completed run.

## Layer 3: Run Trace Evaluation

```bash
# Evaluate last 24h of production traces
uv run python -m evaluations trace --window 24h --out /tmp/trace-report.json

# Evaluate specific traces
uv run python -m evaluations trace --trace-ids /tmp/trace-ids.txt --out /tmp/trace-report.json

# Dry run
uv run python -m evaluations trace --dry-run --out /tmp/trace-dry.json
```

Verify: Check Foundry portal → Evaluations → `cadence-eval-traces` shows a completed run with "Analyze Results" button enabled.

## Cluster Analysis (Portal)

After any evaluation run completes:
1. Navigate to Foundry portal → Evaluations
2. Select the completed run
3. Click "Analyze Results"
4. Review cluster map, failure patterns, and recommendations

## Nightly Workflow (Automated)

The GitHub Actions workflow (`eval-nightly.yml`) runs both layers nightly:

```yaml
# Runs at 6am UTC daily
- name: Run benchmark evaluation
  run: uv run python -m evaluations benchmark --out results/benchmark.json

- name: Run trace evaluation
  run: uv run python -m evaluations trace --window 24h --out results/trace.json
```

## Common Operations

### Check evaluation status
```bash
# Benchmark with specific dataset version
uv run python -m evaluations benchmark --dataset-version v2

# Trace eval with custom window
uv run python -m evaluations trace --window 48h

# Trace eval targeting a specific evaluation container
uv run python -m evaluations trace --evaluation-id my-test-eval
```

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Trace eval returns 0 traces | Missing `gen_ai.agent.id` on spans | Verify feature 007 is producing tagged traces |
| Permission denied on trace eval | Missing RBAC | Add Log Analytics Reader to project identity |
| Benchmark fails with agent not found | Agent not registered | Register agent in Foundry project |
| Analyze Results greyed out | Run status is "Failed" | Check harness logs — submission issue |
