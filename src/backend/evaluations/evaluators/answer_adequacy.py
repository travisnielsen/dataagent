"""Business answer adequacy prompt evaluator.

LLM-judge scoring of response against expected_behavior rubric
on a 1-5 ordinal scale. Designed for registration in Foundry
evaluator catalog.
"""

from __future__ import annotations

ANSWER_ADEQUACY_PROMPT = """\
You are evaluating the quality of an NL2SQL assistant response.

User query: {query}
Assistant response: {response}
Expected behavior: {expected_behavior}

Score the response from 1-5 based on how well it meets the expected behavior:
1 = Completely wrong or irrelevant response
2 = Partially relevant but missing key information
3 = Acceptable — addresses the question with minor gaps
4 = Good — accurate and complete response
5 = Excellent — precise, well-formatted, and actionable

Return ONLY the numeric score (1, 2, 3, 4, or 5)."""

_MAX_SCORE = 5


def build_prompt(*, query: str, response: str, expected_behavior: str) -> str:
    """Build the answer adequacy evaluation prompt.

    Args:
        query: The user's original question.
        response: The assistant's response.
        expected_behavior: The rubric describing expected behavior.

    Returns:
        Formatted prompt string for the LLM judge.
    """
    return ANSWER_ADEQUACY_PROMPT.format(
        query=query,
        response=response,
        expected_behavior=expected_behavior,
    )


def parse_score(llm_output: str) -> float:
    """Parse the LLM judge output into a numeric score.

    Args:
        llm_output: Raw text output from the LLM judge.

    Returns:
        Score between 1.0 and 5.0. Returns 1.0 if parsing fails.
    """
    stripped = llm_output.strip()
    for char in stripped:
        if char.isdigit():
            score = int(char)
            if 1 <= score <= _MAX_SCORE:
                return float(score)
    return 1.0
