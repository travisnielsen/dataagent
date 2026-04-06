"""Clarification quality prompt evaluator.

LLM-judge scoring for clarification questions: single-question,
minimally ambiguous, and actionable criteria. Returns boolean pass/fail.
"""

from __future__ import annotations

CLARIFICATION_QUALITY_PROMPT = """\
You are evaluating a clarification question from an NL2SQL assistant.

User query: {query}
Clarification question: {response}

Evaluate whether the clarification question meets ALL three criteria:
1. Single, focused question — not multiple questions bundled together
2. Minimally ambiguous — clear what specific information is needed
3. Actionable — the user can provide a concrete, useful answer

Return ONLY 'pass' if all three criteria are met, or 'fail' if any criterion is not met."""


def build_prompt(*, query: str, response: str) -> str:
    """Build the clarification quality evaluation prompt.

    Args:
        query: The user's original question.
        response: The assistant's clarification question.

    Returns:
        Formatted prompt string for the LLM judge.
    """
    return CLARIFICATION_QUALITY_PROMPT.format(query=query, response=response)


def parse_score(llm_output: str) -> float:
    """Parse the LLM judge output into a boolean score.

    Args:
        llm_output: Raw text output from the LLM judge.

    Returns:
        ``1.0`` for pass, ``0.0`` for fail.
    """
    return 1.0 if "pass" in llm_output.strip().lower() else 0.0
