"""Parameter extraction correctness evaluator.

Compares extracted parameters against expected (ground truth) parameters
with field-level match scoring.
"""

from __future__ import annotations

import math


def evaluate_param_extraction(
    *,
    extracted_params: dict[str, object],
    expected_params: dict[str, object],
) -> float:
    """Evaluate parameter extraction correctness.

    Computes a field-level match score between extracted and expected
    parameters. Score is the fraction of expected fields that match.

    Args:
        extracted_params: Parameters extracted by the ParameterExtractor.
        expected_params: Ground-truth expected parameters.

    Returns:
        Score from 0.0 to 1.0 (1.0 = all expected params matched).
    """
    if not expected_params:
        return 1.0  # Nothing to check

    if not extracted_params:
        return 0.0

    matched = 0
    total = len(expected_params)

    for key, expected_value in expected_params.items():
        if key not in extracted_params:
            continue
        extracted_value = extracted_params[key]
        if _values_match(extracted_value, expected_value):
            matched += 1

    return matched / total


def _values_match(extracted: object, expected: object) -> bool:
    """Check if extracted value matches expected value.

    Handles type coercion for common parameter types (string-to-int,
    case-insensitive string comparison).

    Args:
        extracted: The extracted value.
        expected: The expected value.

    Returns:
        True if values are considered a match.
    """
    # Direct equality
    if extracted == expected:
        return True

    # Case-insensitive string comparison
    if isinstance(extracted, str) and isinstance(expected, str):
        return extracted.strip().lower() == expected.strip().lower()

    # Numeric coercion
    try:
        return math.isclose(float(extracted), float(expected))  # type: ignore[arg-type]
    except (ValueError, TypeError):
        pass

    # String representation comparison
    return str(extracted).strip().lower() == str(expected).strip().lower()
