# Research: Foundry-Native Multi-Layer Evaluations

**Feature**: 008-foundry-native-evaluations
**Date**: 2026-05-25

## R1: Azure AI Projects SDK 2.0 Evaluation API Surface

### Decision

Use `azure-ai-projects` >= 2.0.0 as the sole evaluation SDK. The `azure-ai-evaluation` SDK (classic) is NOT used.

### Rationale

The spec explicitly requires Foundry-native evaluation via `client.evals` (REST-backed). The 2.0 SDK provides:

- `client.evals.create(...)` — create a named evaluation (container for runs)
- `client.evals.runs.create(...)` — submit a single run with data source, evaluators, data mappings
- `project_client.evaluation_rules.create_or_update(...)` — continuous evaluation rules
- `project_client.datasets.upload_file(...)` — dataset versioning

The existing codebase already uses `azure-ai-projects` for agent operations and the `runner.py` already calls `run_cloud_evaluation()` using this SDK surface.

### Alternatives Considered

- **azure-ai-evaluation SDK**: The classic local-execution SDK. Rejected because it doesn't produce runs visible in the Foundry portal, doesn't enable Cluster Analysis, and the spec explicitly prohibits it.
- **Direct REST calls**: Feasible but unnecessary — the SDK wraps the REST API cleanly.

---

## R2: Continuous Evaluation (EvaluationRule) Pattern

### Decision

Use `project_client.evaluation_rules.create_or_update(rule_id, rule)` with:

- `event_type = EvaluationRuleEventType.RESPONSE_COMPLETED`
- Agent name filter to scope to Cadence
- Evaluator set: `builtin.violence`, `builtin.intent_resolution`, `builtin.coherence`
- `max_hourly_runs = 100` (configurable)

A `setup` CLI subcommand creates/updates the rule idempotently.

### Rationale

Continuous evaluation requires zero scheduler infrastructure — Foundry handles sampling and execution. The `create_or_update` pattern with a stable rule ID ensures idempotency (re-running `setup` is safe). The evaluator set covers safety (violence) and quality (intent_resolution, coherence) as minimum viable continuous monitoring.

### Key Details

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    EvaluationRule,
    EvaluationRuleEventType,
)

rule = EvaluationRule(
    event_type=EvaluationRuleEventType.RESPONSE_COMPLETED,
    agent_name="<configured-agent-name>",
    evaluators={
        "violence": {"id": "builtin.violence"},
        "intent_resolution": {"id": "builtin.intent_resolution"},
        "coherence": {"id": "builtin.coherence"},
    },
    max_hourly_runs=100,
)
project_client.evaluation_rules.create_or_update(
    rule_id="cadence-continuous-eval",
    rule=rule,
)
```

### Prerequisites

- Project managed identity has **Foundry User** role
- Agent is producing responses with `agent_reference` set (feature 007)

---

## R3: Agent Target Evaluation (Golden Dataset Benchmark)

### Decision

Use `client.evals.runs.create(...)` with:

- `data_source_type: azure_ai_target_completions`
- Target: `{"type": "azure_ai_agent", "name": "<agent-name>", "version": "<version>"}`
- Data mapping: `{{item.query}}` for input, `{{sample.output_text}}` for evaluator scoring
- Evaluators: `f1_score`, `coherence`, `task_adherence`, `intent_resolution`

### Rationale

Agent Target Evaluation sends each dataset row's query to the live agent, captures the response, and evaluates it — all server-side. This replaces the legacy replay.py + manual trace submission pattern. The Foundry portal shows per-row results with ground truth comparison.

### Key Details

```python
run = client.evals.runs.create(
    evaluation_id="cadence-eval-benchmark",
    data={
        "type": "azure_ai_target_completions",
        "target": {
            "type": "azure_ai_agent",
            "name": agent_name,
            "version": agent_version,
        },
        "dataset": {
            "id": dataset_id,
            "version": dataset_version,
        },
    },
    evaluators={
        "f1_score": {"id": "builtin.f1_score"},
        "coherence": {"id": "builtin.coherence"},
        "task_adherence": {"id": "builtin.task_adherence"},
        "intent_resolution": {"id": "builtin.intent_resolution"},
    },
    data_mappings={
        "query": "${item.query}",
        "ground_truth": "${item.ground_truth}",
        "response": "${sample.output_text}",
    },
)
```

### Dataset Format

The golden dataset JSONL needs at minimum:

```json
{"query": "...", "ground_truth": "...", "category": "template|dynamic|clarification|..."}
```

The existing `cadence-eval-gold-v1.jsonl` has `query`, `expected_behavior`, `ground_truth_sql`, `scenario_class`. It needs a `ground_truth` field mapping (can alias `expected_behavior`).

---

## R4: Trace Evaluation Pattern

### Decision

Use `client.evals.runs.create(...)` with:

- `data_source_type: azure_ai_traces`
- Agent filter mode (default): filter by `gen_ai.agent.id` + lookback window
- Explicit IDs mode: supply list of trace IDs directly
- Evaluators: same set as benchmark (minus ground-truth-dependent ones like `f1_score`)

### Rationale

Trace evaluation scores real production conversations without issuing any HTTP requests to the Cadence API. It reads from Application Insights via the project's Log Analytics workspace connection. This enables Cluster Analysis on completed runs.

### Key Details

```python
# Agent filter mode
run = client.evals.runs.create(
    evaluation_id="cadence-eval-traces",
    data={
        "type": "azure_ai_traces",
        "agent": {
            "id": f"{agent_name}:{agent_version}",
        },
        "lookback_window": "PT24H",  # ISO 8601 duration
    },
    evaluators={
        "coherence": {"id": "builtin.coherence"},
        "task_adherence": {"id": "builtin.task_adherence"},
        "intent_resolution": {"id": "builtin.intent_resolution"},
        "tool_call_accuracy": {"id": "builtin.tool_call_accuracy"},
        "violence": {"id": "builtin.violence"},
    },
)

# Explicit IDs mode
run = client.evals.runs.create(
    evaluation_id="cadence-eval-traces",
    data={
        "type": "azure_ai_traces",
        "trace_ids": ["trace-id-1", "trace-id-2", ...],
    },
    evaluators={...},
)
```

### Prerequisites

- Application Insights connected to the Foundry project
- Project managed identity has **Log Analytics Reader** on the App Insights resource AND its linked Log Analytics workspace
- Agent traces tagged with `gen_ai.agent.id = "<name>:<version>"` (feature 007)
- Traces contain `invoke_agent` spans with GenAI semantic convention attributes

---

## R5: Cluster Analysis Requirements

### Decision

Cluster Analysis is a portal-only feature (no API). It requires:

1. Evaluation runs with status "Completed" (not "Failed")
2. A deployed model in the Foundry project for embedding generation
3. At least one completed run selected on the evaluation detail page

No implementation work is needed specifically for Cluster Analysis beyond ensuring evaluation runs complete successfully.

### Rationale

The spec notes that current trace evaluation runs show as "Failed" with the Analyze Results button greyed out. The root cause is likely: (a) missing `gen_ai.agent.id` on spans (feature 007 dependency), (b) RBAC misconfiguration (Log Analytics Reader missing), or (c) empty result sets. Fixing the evaluation harness to produce clean "Completed" runs enables this feature automatically.

### Alternatives Considered

- **Custom clustering**: Building local failure clustering. Rejected — Foundry provides this natively. The existing `analysis.py` does local failure clustering which can be retained as a supplementary offline tool.

---

## R6: CLI Architecture Decision

### Decision

Restructure the CLI with subcommands:

- `evaluations setup` — create/update continuous evaluation rule
- `evaluations benchmark` — run golden dataset agent target evaluation
- `evaluations trace` — run trace evaluation
- `evaluations dataset upload` — upload golden dataset to Foundry

All subcommands support `--dry-run` and `--out <path>`.

### Rationale

The existing CLI (`__main__.py`) has `run` and `harvest` subcommands tied to the legacy replay pattern. The new structure maps 1:1 to the three evaluation layers + dataset management, making operator intent clear.

### Migration Impact

Files to remove (FR-019):

- `replay.py` — replay against `/chat` endpoint
- `runner.py` (most of it) — the legacy evaluation orchestration loop
- `harvest.py` — the trace-to-dataset pipeline (replaced by Foundry native trace access)

Files to restructure:

- `__main__.py` — new subcommand structure
- `config.py` — keep ThresholdRule, EvaluationConfig; add ContinuousEvalConfig
- `models.py` — keep DatasetRecord, RunSummary; remove replay-specific models

Files to keep/adapt:

- `dataset_provisioner.py` — already does the right thing for dataset upload
- `analysis.py` — local failure clustering remains useful for offline analysis
- `evaluators/` — custom evaluator prompts remain valuable for reference/registration

---

## R7: Performance Goals and Constraints

### Decision

- **Performance Goal**: Nightly evaluation jobs complete within 30 minutes (combined trace + benchmark).
- **Constraint**: Zero HTTP requests to Cadence's `/api/chat/stream` from the eval harness (validation via router metrics).
- **Constraint**: Exit code 0 when Foundry-side per-row errors occur; non-zero only for submission-level failures.
- **Constraint**: No new persistent storage — reports to `--out`, datasets in Foundry managed storage.

### Rationale

The existing nightly workflow has a 90-minute timeout with ~3 min replay + 3 min sleep + eval. The new approach removes replay and sleep entirely. Agent Target Evaluation executes server-side (Foundry handles the agent calls), so the harness just submits and polls for completion.

---

## R8: Dataset Format Migration

### Decision

The golden dataset format evolves from the current schema:

```json
{"query": "...", "expected_behavior": "...", "ground_truth_sql": "...", "scenario_class": "..."}
```

to:

```json
{"query": "...", "ground_truth": "...", "category": "...", "ground_truth_sql": "..."}
```

Where:

- `ground_truth` = what was `expected_behavior` (natural language expected answer description)
- `category` = what was `scenario_class` (query category for analysis)
- `ground_truth_sql` retained for SQL-specific evaluators

### Rationale

The Foundry Agent Target Evaluation data mappings expect `${item.ground_truth}` for NLP evaluators (f1_score, similarity). Renaming aligns with the Foundry convention while maintaining backward traceability.

### Migration

A one-time migration script transforms existing JSONL rows. The 48-row gold dataset is small enough for manual review after transformation.

---

## R9: Nightly Workflow Restructuring

### Decision

The GitHub workflow `eval-nightly.yml` changes from:

1. ~~Replay dataset against live API~~ (removed)
2. ~~Sleep 180s for trace ingestion~~ (removed)
3. ~~Run evaluation suite with `--cloud`~~ (restructured)

To:

1. Run `evaluations benchmark --out results/benchmark.json`
2. Run `evaluations trace --window 24h --out results/trace.json`
3. Check results for regressions

### Rationale

Agent Target Evaluation replaces replay — Foundry sends queries to the agent server-side. Trace evaluation reads existing production traces — no sleep needed. Both steps are independent and could even run in parallel.

---

## R10: Identity and RBAC Requirements

### Decision

The evaluation runner identity (user-assigned managed identity on the self-hosted GitHub runner) needs:

- **Foundry User** role on the AI Foundry project (continuous eval, benchmark, trace submission)
- **Log Analytics Reader** on the Application Insights resource AND its linked Log Analytics workspace (trace evaluation)

The existing workflow already authenticates via UAMI (`EVALUATIONS_RUNNER_CLIENT_ID`).

### Rationale

The existing workflow already has the right identity mechanism. The spec documents RBAC as a prerequisite (FR-022). If Log Analytics Reader is missing, the trace evaluation must fail fast with a clear error message pointing to the missing role assignment.

### No Changes Needed

The Terraform `security.tf` should already assign these roles. If not, it's an infrastructure task tracked separately.
