# Data Model: Foundry Evaluations for NL2SQL

**Feature**: 005-foundry-evaluations
**Date**: 2026-03-24

## Entity Relationship

```text
EvaluationConfig (1) ──contains──> (*) EvaluatorProfile
EvaluationConfig (1) ──contains──> (*) ThresholdRule
DatasetMetadata (1) ──used-by───> (*) EvaluationRun
EvaluatorProfile(1) ──used-by───> (*) EvaluationRun
EvaluationRun   (1) ──produces──> (1) RunSummary
RunSummary      (1) ──contains──> (*) MetricResult
RunSummary      (1) ──contains──> (*) FailureRecord
FailureRecord   (*) ──grouped-by─> (1) FailureCluster
EvaluationRun   (2) ──compared──> (1) DeltaComparison
DeltaComparison (1) ──contains──> (*) MetricDelta
QualityGateDecision (1) ──references─> (1) RunSummary
QualityGateDecision (1) ──references─> (*) ThresholdRule
```

## Entities

### EvaluationConfig

Versioned configuration for evaluation runs. Stored as `src/backend/evaluations/config.py`.

```python
class ThresholdRule(BaseModel):
    """Threshold for a single metric in a quality gate."""
    metric: str                          # e.g., "intent_resolution", "sql_safety"
    min_score: float                     # Minimum passing score (0.0-1.0 or 1-5)
    priority: Literal["P0", "P1", "P2"]  # P0 = blocks merge, P1 = warns, P2 = informational

class EvaluatorRef(BaseModel):
    """Reference to an evaluator (built-in or custom)."""
    name: str                            # e.g., "relevance", "sql_safety"
    type: Literal["builtin", "custom_code", "custom_prompt"]
    version: str = "v1"

class EvaluationConfig(BaseModel):
    """Top-level evaluation configuration."""
    config_version: str = "v1"
    evaluators: list[EvaluatorRef]
    thresholds: list[ThresholdRule]
    dataset_name: str                    # Foundry dataset name
    dataset_version: str                 # e.g., "v1"
    judge_model_deployment: str          # Model for LLM-judge evaluators
    project_endpoint: str                # Azure AI Foundry project endpoint
```

### DatasetMetadata

Metadata for a versioned evaluation dataset. Tracks lineage from gold curation or trace harvesting.

```python
class DatasetRecord(BaseModel):
    """Single row in an evaluation dataset (JSONL format)."""
    query: str                                    # User question
    expected_behavior: str                        # Rubric for expected response
    context: str | None = None                    # Additional context
    ground_truth_sql: str | None = None           # Expected SQL (for extraction eval)
    ground_truth_params: dict[str, Any] | None = None  # Expected parameters
    scenario_class: Literal[
        "template", "dynamic", "clarification", "what_if", "conversation"
    ]
    conversation: list[dict[str, Any]] | None = None  # Full message history

class DatasetMetadata(BaseModel):
    """Metadata for a versioned dataset."""
    name: str                            # e.g., "cadence-eval-gold"
    version: str                         # e.g., "v1"
    stage: Literal["seed", "traces", "curated", "prod"]
    source: Literal["gold", "trace_harvested"]
    record_count: int
    scenario_distribution: dict[str, int]  # Counts by scenario_class
    created_at: str                      # ISO 8601 timestamp
    sanitization_status: Literal["passed", "failed", "pending"]
    dataset_uri: str | None = None       # Foundry dataset URI after upload
```

### EvaluatorProfile

Declares evaluator configuration for built-in and custom evaluators.

```python
class BuiltinEvaluatorProfile(BaseModel):
    """Built-in Foundry evaluator profile."""
    name: str                            # e.g., "intent_resolution"
    type: Literal["builtin"] = "builtin"
    phase: Literal[1, 2] = 1
    requires_conversation: bool = False  # Needs full message history
    requires_ground_truth: bool = False

class CustomCodeEvaluatorProfile(BaseModel):
    """Custom code evaluator (deterministic, no LLM)."""
    name: str                            # e.g., "sql_safety"
    type: Literal["custom_code"] = "custom_code"
    phase: Literal[1, 2] = 2
    module_path: str                     # e.g., "evaluations.evaluators.sql_safety"
    function_name: str                   # e.g., "evaluate_sql_safety"

class CustomPromptEvaluatorProfile(BaseModel):
    """Custom prompt evaluator (LLM-judge based)."""
    name: str                            # e.g., "business_answer_adequacy"
    type: Literal["custom_prompt"] = "custom_prompt"
    phase: Literal[1, 2] = 2
    prompt_template: str                 # Prompt with {{query}}, {{response}}, {{expected_behavior}}
    scoring_type: Literal["ordinal", "continuous", "boolean"]
    min_score: float = 1.0
    max_score: float = 5.0
    pass_threshold: float = 3.0
```

### EvaluationRun

Immutable record of a single evaluation execution.

```python
class EvaluationRun(BaseModel):
    """Record of a single evaluation run."""
    run_id: str                          # Unique identifier
    eval_id: str | None = None           # Foundry run linkage (Studio URL/ID for cloud runs)
    dataset_name: str
    dataset_version: str
    evaluator_names: list[str]
    config_version: str
    trigger: Literal["ci_pr", "nightly", "manual"]
    status: Literal["running", "completed", "failed", "cancelled"]
    started_at: str                      # ISO 8601
    completed_at: str | None = None
    git_sha: str | None = None           # Commit SHA for traceability
    branch: str | None = None
    correlation_id: str | None = None    # App Insights correlation
```

### RunSummary / MetricResult

Aggregated results from a completed evaluation run.

```python
class MetricResult(BaseModel):
    """Result for a single metric across all dataset records."""
    metric: str                          # e.g., "intent_resolution"
    mean_score: float
    median_score: float
    p5_score: float                      # 5th percentile
    p95_score: float                     # 95th percentile
    pass_rate: float                     # Fraction meeting threshold
    sample_count: int
    threshold: float | None = None       # Applied threshold
    passed: bool | None = None           # Met threshold?

class RunSummary(BaseModel):
    """Aggregated results from a completed evaluation run."""
    run_id: str
    metrics: list[MetricResult]
    total_records: int
    total_passed: int
    total_failed: int
    overall_pass: bool                   # All P0 thresholds met
    failure_count_by_cluster: dict[str, int]  # Counts by FailureCluster type
```

### FailureRecord / FailureCluster

Individual failure details and clustering for remediation.

```python
class FailureRecord(BaseModel):
    """Single failed evaluation record with diagnostic context."""
    record_index: int                    # Row index in dataset
    query: str
    response: str | None = None
    expected_behavior: str | None = None
    failing_metrics: dict[str, float]    # metric_name -> score
    cluster: Literal[
        "intent_misroute",
        "extraction_error",
        "validator_rejection",
        "poor_answer_quality",
        "safety_violation",
        "tool_call_failure",
    ]
    diagnostic_context: str | None = None  # Pipeline stage details

class FailureCluster(BaseModel):
    """Grouped failures for remediation planning."""
    cluster_type: str                    # Same as FailureRecord.cluster
    count: int
    percentage: float                    # Of total failures
    representative_queries: list[str]    # Top 3-5 examples
    remediation_target: str              # e.g., "assistant_prompt.md", "parameter_extractor/prompt.md"
    severity: Literal["critical", "high", "medium", "low"]
```

### DeltaComparison / MetricDelta

Before/after comparison for remediation validation.

```python
class MetricDelta(BaseModel):
    """Change in a single metric between two runs."""
    metric: str
    before_score: float
    after_score: float
    delta: float                         # after - before
    improved: bool
    statistically_significant: bool      # Based on sample size

class DeltaComparison(BaseModel):
    """Comparison between two evaluation runs."""
    before_run_id: str
    after_run_id: str
    dataset_version: str                 # Must be same for valid comparison
    deltas: list[MetricDelta]
    overall_improved: bool
    regressions: list[str]               # Metric names that regressed
```

### QualityGateDecision

CI decision artifact for merge gating.

```python
class QualityGateDecision(BaseModel):
    """CI quality gate decision."""
    run_id: str
    gate_result: Literal["pass", "fail", "error"]
    p0_results: list[MetricResult]       # P0 metrics only
    failing_metrics: list[str]           # Metric names below threshold
    waivers: list[str]                   # Approved waivers (if any)
    decided_at: str                      # ISO 8601
    git_sha: str
    branch: str
```

## State Transitions

### EvaluationRun Lifecycle

```text
                 ┌──────────┐
                 │ running  │
                 └────┬─────┘
                      │
              ┌───────┴───────┐
              ▼               ▼
       ┌────────────┐  ┌───────────┐
       │ completed  │  │  failed   │
       └─────┬──────┘  └───────────┘
             │
             ▼
     ┌──────────────┐
     │  gate check  │
     └──────┬───────┘
            │
     ┌──────┴──────┐
     ▼             ▼
  ┌──────┐    ┌──────┐
  │ pass │    │ fail │
  └──────┘    └──────┘
```

### Dataset Lifecycle

```text
  seed (initial curation)
    │
    ▼
  curated (reviewed, labeled, v1)
    │
    ├──► trace-harvested (App Insights extraction, vN)
    │         │
    │         ▼
    │    curated (human-reviewed traces merged, vN+1)
    │
    ▼
  prod (CI-gated, regression-tested)
```
