"""Evaluation configuration, threshold rules, and evaluator profiles.

Defines the evaluation contract: which evaluators run, what thresholds
they must meet, and the default metric targets for the NL2SQL pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Core configuration models (T005)
# ---------------------------------------------------------------------------


class ThresholdRule(BaseModel):
    """Threshold for a single metric in a quality gate."""

    metric: str
    min_score: float
    priority: Literal["P0", "P1", "P2"]


class EvaluatorRef(BaseModel):
    """Reference to an evaluator (built-in or custom)."""

    name: str
    type: Literal["builtin", "custom_code", "custom_prompt"]
    version: str = "v1"


class EvaluationConfig(BaseModel):
    """Top-level evaluation configuration."""

    config_version: str = "v1"
    evaluators: list[EvaluatorRef]
    thresholds: list[ThresholdRule]
    dataset_name: str
    dataset_version: str
    judge_model_deployment: str
    project_endpoint: str


# ---------------------------------------------------------------------------
# Evaluator profile models (T009)
# ---------------------------------------------------------------------------


class BuiltinEvaluatorProfile(BaseModel):
    """Built-in Foundry evaluator profile."""

    name: str
    type: Literal["builtin"] = "builtin"
    phase: Literal[1, 2] = 1
    requires_conversation: bool = False
    requires_ground_truth: bool = False


class CustomCodeEvaluatorProfile(BaseModel):
    """Custom code evaluator (deterministic, no LLM)."""

    name: str
    type: Literal["custom_code"] = "custom_code"
    phase: Literal[1, 2] = 2
    module_path: str
    function_name: str


class CustomPromptEvaluatorProfile(BaseModel):
    """Custom prompt evaluator (LLM-judge based)."""

    name: str
    type: Literal["custom_prompt"] = "custom_prompt"
    phase: Literal[1, 2] = 2
    prompt_template: str
    scoring_type: Literal["ordinal", "continuous", "boolean"]
    min_score: float = 1.0
    max_score: float = 5.0
    pass_threshold: float = 3.0


# ---------------------------------------------------------------------------
# Phase 1 built-in evaluator profiles (T012)
# ---------------------------------------------------------------------------

BUILTIN_EVALUATOR_PROFILES: list[BuiltinEvaluatorProfile] = [
    BuiltinEvaluatorProfile(
        name="intent_resolution",
        phase=1,
        requires_conversation=True,
    ),
    BuiltinEvaluatorProfile(
        name="task_adherence",
        phase=1,
        requires_conversation=True,
    ),
    BuiltinEvaluatorProfile(
        name="tool_call_accuracy",
        phase=1,
        requires_conversation=True,
        requires_ground_truth=True,
    ),
    BuiltinEvaluatorProfile(
        name="relevance",
        phase=1,
    ),
    BuiltinEvaluatorProfile(
        name="indirect_attack",
        phase=1,
    ),
]

# ---------------------------------------------------------------------------
# Phase 2 custom evaluator profiles (T014)
# ---------------------------------------------------------------------------

CUSTOM_EVALUATOR_PROFILES: list[CustomCodeEvaluatorProfile | CustomPromptEvaluatorProfile] = [
    CustomCodeEvaluatorProfile(
        name="sql_safety",
        phase=2,
        module_path="evaluations.evaluators.sql_safety",
        function_name="evaluate_sql_safety",
    ),
    CustomCodeEvaluatorProfile(
        name="param_extraction_correctness",
        phase=2,
        module_path="evaluations.evaluators.param_extraction",
        function_name="evaluate_param_extraction",
    ),
    CustomPromptEvaluatorProfile(
        name="answer_adequacy",
        phase=2,
        prompt_template=(
            "You are evaluating the quality of an NL2SQL assistant response.\n\n"
            "User query: {{query}}\n"
            "Assistant response: {{response}}\n"
            "Expected behavior: {{expected_behavior}}\n\n"
            "Score the response from 1-5 based on how well it meets the expected behavior.\n"
            "1=completely wrong, 2=partially relevant, 3=acceptable, "
            "4=good, 5=excellent.\n\n"
            "Return ONLY the numeric score."
        ),
        scoring_type="ordinal",
        min_score=1.0,
        max_score=5.0,
        pass_threshold=3.0,
    ),
    CustomPromptEvaluatorProfile(
        name="clarification_quality",
        phase=2,
        prompt_template=(
            "You are evaluating a clarification question from an NL2SQL assistant.\n\n"
            "User query: {{query}}\n"
            "Clarification question: {{response}}\n\n"
            "Evaluate whether the clarification question is:\n"
            "1. A single, focused question (not multiple questions)\n"
            "2. Minimally ambiguous (clear what information is needed)\n"
            "3. Actionable (the user can provide a concrete answer)\n\n"
            "Return 'pass' if all three criteria are met, 'fail' otherwise."
        ),
        scoring_type="boolean",
        min_score=0.0,
        max_score=1.0,
        pass_threshold=1.0,
    ),
]

# ---------------------------------------------------------------------------
# Pipeline-specific evaluation targets (T013)
# ---------------------------------------------------------------------------

PIPELINE_TARGETS: dict[str, list[str]] = {
    "DataAssistant": ["intent_resolution", "relevance", "indirect_attack"],
    "ParameterExtractor": [
        "param_extraction_correctness",
        "clarification_quality",
        "task_adherence",
    ],
    "QueryValidator": ["sql_safety"],
    "QueryBuilder": ["tool_call_accuracy", "answer_adequacy"],
}

# ---------------------------------------------------------------------------
# Default evaluation contract with thresholds (T010)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: list[ThresholdRule] = [
    # Primary metrics (P0 — blocks merge)
    # Foundry built-in evaluators return 0-5 scale: threshold 3.0 = "good" (≥60%)
    ThresholdRule(metric="intent_resolution", min_score=3.0, priority="P0"),
    ThresholdRule(metric="sql_safety", min_score=0.95, priority="P0"),
    ThresholdRule(metric="relevance", min_score=3.0, priority="P0"),
    ThresholdRule(metric="answer_adequacy", min_score=3.0, priority="P0"),
    # Secondary metrics (P1 — warns)
    ThresholdRule(metric="tool_call_accuracy", min_score=0.80, priority="P1"),
    # task_adherence returns 0-1 scale
    ThresholdRule(metric="task_adherence", min_score=0.80, priority="P1"),
    ThresholdRule(metric="clarification_quality", min_score=0.80, priority="P1"),
    ThresholdRule(metric="param_extraction_correctness", min_score=0.75, priority="P1"),
    # Informational metrics (P2)
    ThresholdRule(metric="indirect_attack", min_score=0.90, priority="P2"),
]


def build_default_config(
    *,
    project_endpoint: str = "",
    judge_model_deployment: str = "gpt-4o",
    dataset_name: str = "cadence-eval-gold",
    dataset_version: str = "v1",
) -> EvaluationConfig:
    """Build the default evaluation configuration.

    Args:
        project_endpoint: Azure AI Foundry project endpoint.
        judge_model_deployment: Model deployment for LLM-judge evaluators.
        dataset_name: Default dataset name.
        dataset_version: Default dataset version.

    Returns:
        A fully populated ``EvaluationConfig``.
    """
    evaluator_refs = [
        EvaluatorRef(name=profile.name, type="builtin") for profile in BUILTIN_EVALUATOR_PROFILES
    ]
    evaluator_refs.extend(
        EvaluatorRef(name=profile.name, type=profile.type) for profile in CUSTOM_EVALUATOR_PROFILES
    )

    return EvaluationConfig(
        evaluators=evaluator_refs,
        thresholds=DEFAULT_THRESHOLDS,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        judge_model_deployment=judge_model_deployment,
        project_endpoint=project_endpoint,
    )


# ---------------------------------------------------------------------------
# Config persistence (T015)
# ---------------------------------------------------------------------------


def load_config(path: Path) -> EvaluationConfig:
    """Load an evaluation configuration from a JSON file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Parsed ``EvaluationConfig``.

    Raises:
        FileNotFoundError: If the file does not exist.
        pydantic.ValidationError: If the file content is invalid.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return EvaluationConfig.model_validate(data)


def save_config(config: EvaluationConfig, path: Path) -> None:
    """Save an evaluation configuration to a JSON file.

    Args:
        config: The configuration to persist.
        path: Destination file path (created/overwritten).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
