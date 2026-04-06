"""Unit tests for parameter extraction correctness evaluator."""

from __future__ import annotations

import pytest
from evaluations.evaluators.param_extraction import evaluate_param_extraction


class TestParamExtractionEvaluator:
    def test_exact_match(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"customer_name": "Tailspin Toys", "days": 90},
            expected_params={"customer_name": "Tailspin Toys", "days": 90},
        )
        assert score == pytest.approx(1.0)

    def test_partial_match(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"customer_name": "Tailspin Toys"},
            expected_params={"customer_name": "Tailspin Toys", "days": 90},
        )
        assert score == pytest.approx(0.5)

    def test_no_match(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"other_param": "value"},
            expected_params={"customer_name": "Tailspin Toys", "days": 90},
        )
        assert score == pytest.approx(0.0)

    def test_empty_expected_params(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"some": "value"},
            expected_params={},
        )
        assert score == pytest.approx(1.0)

    def test_empty_extracted_params(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={},
            expected_params={"customer_name": "Tailspin Toys"},
        )
        assert score == pytest.approx(0.0)

    def test_case_insensitive_string_match(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"name": "tailspin toys"},
            expected_params={"name": "Tailspin Toys"},
        )
        assert score == pytest.approx(1.0)

    def test_numeric_coercion(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"count": "10"},
            expected_params={"count": 10},
        )
        assert score == pytest.approx(1.0)

    def test_extra_extracted_params_ignored(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"name": "Tailspin", "extra": "val"},
            expected_params={"name": "Tailspin"},
        )
        assert score == pytest.approx(1.0)

    def test_wrong_value(self) -> None:
        score = evaluate_param_extraction(
            extracted_params={"name": "Wrong Name"},
            expected_params={"name": "Correct Name"},
        )
        assert score == pytest.approx(0.0)
