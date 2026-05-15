"""Foundry evaluation package for NL2SQL quality measurement."""

import importlib

from .config import (
    BUILTIN_EVALUATOR_PROFILES,
    CUSTOM_EVALUATOR_PROFILES,
    DEFAULT_THRESHOLDS,
    PIPELINE_TARGETS,
    BuiltinEvaluatorProfile,
    CustomCodeEvaluatorProfile,
    CustomPromptEvaluatorProfile,
    EvaluationConfig,
    EvaluatorRef,
    ThresholdRule,
    build_default_config,
    load_config,
    save_config,
)
from .models import (
    DatasetMetadata,
    DatasetRecord,
    DeltaComparison,
    EvaluationRun,
    FailureCluster,
    FailureRecord,
    MetricDelta,
    MetricResult,
    QualityGateDecision,
    RunSummary,
)

__all__ = [
    "BUILTIN_EVALUATOR_PROFILES",
    "CUSTOM_EVALUATOR_PROFILES",
    "DEFAULT_THRESHOLDS",
    "PIPELINE_TARGETS",
    "BuiltinEvaluatorProfile",
    "CustomCodeEvaluatorProfile",
    "CustomPromptEvaluatorProfile",
    "DatasetMetadata",
    "DatasetRecord",
    "DeltaComparison",
    "EvaluationConfig",
    "EvaluationRun",
    "EvaluatorRef",
    "FailureCluster",
    "FailureRecord",
    "MetricDelta",
    "MetricResult",
    "QualityGateDecision",
    "RunSummary",
    "ThresholdRule",
    "build_default_config",
    "load_config",
    "save_config",
]


_LAZY_IMPORTS: dict[str, str] = {
    "load_dataset": ".runner",
    "validate_dataset": ".runner",
    "generate_metadata": ".runner",
    "run_evaluation": ".runner",
    "run_cloud_evaluation": ".runner",
    "compute_quality_gate": ".runner",
    "persist_run_results": ".runner",
    "register_custom_evaluator": ".runner",
    "cluster_failures": ".analysis",
    "classify_failure": ".analysis",
    "compare_runs": ".analysis",
    "sanitize_text": ".harvest",
    "sanitize_records": ".harvest",
    "build_kql_query": ".harvest",
    "persist_dataset": ".harvest",
    "get_next_version": ".harvest",
}


def __getattr__(name: str) -> object:
    """Lazy imports for runner, analysis, and harvest functions."""
    if name in _LAZY_IMPORTS:
        module = importlib.import_module(_LAZY_IMPORTS[name], package=__name__)
        return getattr(module, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
