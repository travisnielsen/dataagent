"""Shared test fixtures for Cadence."""

import sys
import warnings
from pathlib import Path
from typing import Any

# Suppress agent-framework 1.4.x ExperimentalWarning emitted at import time for
# preview sub-features (MemoryStore, SkillResource, etc.) that Cadence does not
# use. Must run before any agent_framework import. Matched by message to avoid
# importing the warning class (which itself triggers the warning).
warnings.filterwarnings(
    "ignore",
    message=r".*experimental and may change.*",
)

import pytest

# Ensure src/backend/ and src/evaluations/ are on the path for imports
_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC / "backend"))
sys.path.insert(0, str(_SRC))

from config.settings import Settings
from shared.protocols import NoOpReporter

# ---------------------------------------------------------------------------
# Protocol fakes
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE_THRESHOLD = 0.80
AMBIGUITY_GAP_THRESHOLD = 0.03


class FakeTemplateSearch:
    """In-memory fake satisfying the ``TemplateSearchService`` protocol.

    Stores canned results and records every ``search`` call for assertions.
    """

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results: list[dict[str, Any]] = results or []
        self.calls: list[str] = []

    async def search(self, user_question: str) -> dict[str, Any]:
        """Return a dict mimicking ``search_query_templates()`` output."""
        self.calls.append(user_question)

        if not self.results:
            return {
                "has_high_confidence_match": False,
                "is_ambiguous": False,
                "best_match": None,
                "confidence_score": 0.0,
                "confidence_threshold": HIGH_CONFIDENCE_THRESHOLD,
                "ambiguity_gap": 0.0,
                "ambiguity_gap_threshold": AMBIGUITY_GAP_THRESHOLD,
                "all_matches": [],
                "message": "No query templates found",
            }

        best = self.results[0]
        top_score = best.get("score", 0.0)
        has_high = top_score >= HIGH_CONFIDENCE_THRESHOLD

        score_gap = top_score
        is_ambiguous = False
        if len(self.results) >= 2:
            second_score = self.results[1].get("score", 0.0)
            score_gap = top_score - second_score
            if has_high:
                is_ambiguous = score_gap < AMBIGUITY_GAP_THRESHOLD

        is_valid = has_high and not is_ambiguous

        return {
            "has_high_confidence_match": is_valid,
            "is_ambiguous": is_ambiguous,
            "best_match": best if is_valid else None,
            "confidence_score": top_score,
            "confidence_threshold": HIGH_CONFIDENCE_THRESHOLD,
            "ambiguity_gap": score_gap,
            "ambiguity_gap_threshold": AMBIGUITY_GAP_THRESHOLD,
            "all_matches": self.results,
            "message": (
                f"High confidence unambiguous match: '{best.get('intent', '')}'"
                if is_valid
                else "No high confidence match"
            ),
        }


class FakeTableSearch:
    """In-memory fake satisfying the ``TableSearchService`` protocol.

    Stores canned table dicts and records every ``search`` call.
    """

    def __init__(self, tables: list[dict[str, Any]] | None = None) -> None:
        self.tables: list[dict[str, Any]] = tables or []
        self.calls: list[str] = []

    async def search(self, user_question: str) -> dict[str, Any]:
        """Return a dict mimicking ``search_tables()`` output."""
        self.calls.append(user_question)

        if not self.tables:
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "message": "No tables found matching the query",
            }

        return {
            "has_matches": True,
            "tables": self.tables,
            "table_count": len(self.tables),
            "message": f"Found {len(self.tables)} relevant table(s)",
        }


class FakeSqlExecutor:
    """In-memory fake satisfying the ``SqlExecutor`` protocol.

    Returns canned rows/columns or an error, and records every call.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        self.rows: list[dict[str, Any]] = rows or []
        self.columns: list[str] = columns or []
        self.error: str | None = error
        self.calls: list[tuple[str, list[Any] | None]] = []

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Return a success/failure dict mimicking ``execute_sql`` output."""
        self.calls.append((query, params))

        if self.error:
            return {
                "success": False,
                "error": self.error,
                "columns": [],
                "rows": [],
                "row_count": 0,
            }

        return {
            "success": True,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
        }


class SequentialFakeSqlExecutor:
    """Fake that returns different results for sequential SQL calls.

    Each call to ``execute`` pops the next result from ``responses``.
    Falls back to the last response if more calls are made than responses
    provided.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[tuple[str, list[Any] | None]] = []

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((query, params))
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        return self._responses[idx]


class SpyReporter:
    """Spy satisfying the ``ProgressReporter`` protocol.

    Captures every ``step_start`` / ``step_end`` call for assertions.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    def step_start(self, step: str) -> None:
        """Record a step-start event."""
        self.events.append({"step": step, "status": "started"})

    def step_end(self, step: str) -> None:
        """Record a step-end event."""
        self.events.append({"step": step, "status": "completed"})


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings() -> Settings:
    """Return a ``Settings`` instance populated with safe test defaults."""
    return Settings(
        azure_ai_project_endpoint="https://test.cognitiveservices.azure.com",
        azure_search_endpoint="https://test.search.windows.net",
        azure_sql_server="test-server.database.windows.net",
        azure_sql_database="TestDB",
        azure_ai_model_deployment_name="test-model",
    )


@pytest.fixture
def fake_template_search() -> FakeTemplateSearch:
    """Return an empty ``FakeTemplateSearch`` instance."""
    return FakeTemplateSearch()


@pytest.fixture
def fake_sql_executor() -> FakeSqlExecutor:
    """Return an empty ``FakeSqlExecutor`` instance."""
    return FakeSqlExecutor()


@pytest.fixture
def spy_reporter() -> SpyReporter:
    """Return a fresh ``SpyReporter`` instance."""
    return SpyReporter()


@pytest.fixture
def noop_reporter() -> NoOpReporter:
    """Return a ``NoOpReporter`` from the protocols module."""
    return NoOpReporter()
