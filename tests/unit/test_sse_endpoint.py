"""Tests for SSE chat streaming endpoint.

Tests ``generate_orchestrator_streaming_response`` and
``generate_clarification_response_stream`` by calling the async generators
directly with mocked dependencies.  No HTTP client, no Azure credentials.
"""

from __future__ import annotations

import contextlib
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# Prevent ``api.__init__`` from importing ``api.main`` (which pulls in auth
# middleware and triggers a pydantic deprecation error).  We only need the
# thin modules ``api.session_manager``, ``api.step_events``, etc.
if "api" not in sys.modules:
    from pathlib import Path as _Path

    _api_pkg_dir = str(_Path(__file__).resolve().parents[2] / "src" / "backend" / "api")
    _api_stub = types.ModuleType("api")
    _api_stub.__path__ = [_api_pkg_dir]  # type: ignore[attr-defined]
    _api_stub.__package__ = "api"
    sys.modules["api"] = _api_stub

# Force the submodules we patch to be importable through the stub.
from assistant.assistant import ClassificationResult
from models import ClarificationRequest, NL2SQLResponse

# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_sse_events(raw_chunks: list[str]) -> list[dict]:
    """Parse SSE ``data:`` lines into dicts, skipping non-JSON."""
    events: list[dict] = []
    for chunk in raw_chunks:
        for line in chunk.strip().split("\n"):
            if line.startswith("data: "):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line[6:]))
    return events


async def _collect(gen) -> list[str]:
    """Exhaust an async generator and return its chunks."""
    return [chunk async for chunk in gen]


def _mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.azure_ai_project_endpoint = "https://test.openai.azure.com/"
    settings.azure_client_id = None
    settings.azure_ai_orchestrator_model = "gpt-4o"
    settings.azure_ai_model_deployment_name = "gpt-4o"
    return settings


def _mock_assistant(
    intent: str = "data_query",
    query: str = "Show orders",
    conversation_id: str = "test-thread-123",
) -> MagicMock:
    assistant = MagicMock()
    assistant.conversation_id = conversation_id
    assistant.classify_intent = AsyncMock(
        return_value=ClassificationResult(intent=intent, query=query),
    )
    assistant.build_nl2sql_request.return_value = MagicMock()
    assistant.update_context = MagicMock()
    assistant.enrich_response = MagicMock()
    assistant.handle_conversation = AsyncMock(return_value="Hello there!")
    assistant.render_response.return_value = {
        "text": "Here are the results",
        "tool_call": {"tool_name": "nl2sql_query", "result": {}},
        "conversation_id": conversation_id,
    }
    return assistant


def _success_response(**overrides) -> NL2SQLResponse:
    defaults: dict = {
        "sql_query": "SELECT TOP 10 * FROM Sales.Orders",
        "sql_response": [{"OrderID": 1}],
        "columns": ["OrderID"],
        "row_count": 1,
        "query_source": "template",
        "confidence_score": 0.92,
    }
    defaults.update(overrides)
    return NL2SQLResponse(**defaults)


def _clarification_request(**overrides) -> ClarificationRequest:
    defaults: dict = {
        "parameter_name": "city",
        "prompt": "Which city would you like to filter by?",
        "allowed_values": ["Seattle", "Portland"],
        "original_question": "Show orders",
        "template_id": "tpl-orders",
        "template_json": '{"id": "tpl-orders"}',
        "extracted_parameters": {"year": "2024"},
    }
    defaults.update(overrides)
    return ClarificationRequest(**defaults)


# Shared patch paths for the deferred imports inside
# generate_orchestrator_streaming_response
_ORCH_PATCHES = {
    "process_query": "nl2sql_controller.pipeline.process_query",
    "create_clients": "workflow.clients.create_pipeline_clients",
    "get_settings": "config.settings.get_settings",
    "get_assistant": "api.session_manager.get_assistant",
    "store_assistant": "api.session_manager.store_assistant",
    "DataAssistant": "assistant.DataAssistant",
    "load_prompt": "assistant.load_assistant_prompt",
    "FoundryAgent": "agent_framework.foundry.FoundryAgent",
    "AIProjectClient": "azure.ai.projects.aio.AIProjectClient",
    "DefaultCred": "azure.identity.aio.DefaultAzureCredential",
}


# ── Data Query: Full SSE Stream ─────────────────────────────────────────


class TestDataQueryStream:
    """Verify SSE events for a successful data-query flow."""

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_emits_analyze_step_and_tool_call(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # Step 1+2: Analyzing request started / completed
        assert events[0]["step"] == "Analyzing request..."
        assert events[0]["status"] == "started"
        assert events[1]["step"] == "Analyzing request..."
        assert events[1]["status"] == "completed"
        assert "duration_ms" in events[1]

        # Step 3: rendered response with tool_call
        assert "tool_call" in events[2]

        # Last: done marker
        assert events[-1]["done"] is True
        assert events[-1]["conversation_id"] == "test-thread-123"

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_stores_assistant_after_response(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()

        await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )

        assistant.update_context.assert_called_once()
        assistant.enrich_response.assert_called_once()
        mock_store.assert_called_once_with("test-thread-123", assistant)


# ── Conversation Intent ─────────────────────────────────────────────────


class TestConversationStream:
    """Verify SSE events when the assistant classifies intent as conversation."""

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_conversation_events(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant(intent="conversation")
        mock_get_assistant.return_value = assistant
        mock_create_clients.return_value = MagicMock()

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Hello",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # Analyzing request started / completed
        assert events[0]["step"] == "Analyzing request..."
        assert events[0]["status"] == "started"
        assert events[1]["step"] == "Analyzing request..."
        assert events[1]["status"] == "completed"

        # Generating response started / completed
        assert events[2]["step"] == "Generating response..."
        assert events[2]["status"] == "started"
        assert events[3]["step"] == "Generating response..."
        assert events[3]["status"] == "completed"

        # Text response
        assert events[4]["text"] == "Hello there!"
        assert events[4]["conversation_id"] == "test-thread-123"

        # Done
        assert events[-1]["done"] is True

        # process_query should NOT have been called
        mock_process_query.assert_not_called()


# ── Clarification Result ────────────────────────────────────────────────


class TestClarificationResult:
    """Verify SSE events when process_query returns a ClarificationRequest."""

    @patch("api.workflow_cache.store_clarification_context")
    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_clarification_events(
        self,
        mock_store_asst,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
        mock_store_ctx,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant
        mock_process_query.return_value = _clarification_request()
        mock_create_clients.return_value = MagicMock()

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # Find the clarification event
        clar_events = [e for e in events if e.get("needs_clarification")]
        assert len(clar_events) == 1
        clar = clar_events[0]
        assert clar["clarification"]["parameter_name"] == "city"
        assert clar["clarification"]["prompt"] == ("Which city would you like to filter by?")
        assert "request_id" in clar["clarification"]

        # steps_complete marker
        steps_events = [e for e in events if e.get("steps_complete")]
        assert len(steps_events) == 1

        # done
        assert events[-1]["done"] is True


# ── Clarification Response Flow ─────────────────────────────────────────


class TestClarificationResponseStream:
    """Verify SSE events from generate_clarification_response_stream."""

    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    async def test_clarification_response_success(
        self,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
        mock_store,
        mock_get_assistant,
    ) -> None:
        from api.routers.chat import generate_clarification_response_stream

        mock_get_settings.return_value = _mock_settings()
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()
        mock_get_assistant.return_value = _mock_assistant()

        ctx = _clarification_request()
        chunks = await _collect(
            generate_clarification_response_stream(
                clarification_ctx=ctx,
                message="Seattle",
                request_id="clarify_abc123",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # tool_call present
        tool_events = [e for e in events if "tool_call" in e]
        assert len(tool_events) == 1
        assert tool_events[0]["tool_call"]["tool_name"] == "nl2sql_query"

        # steps_complete
        steps_events = [e for e in events if e.get("steps_complete")]
        assert len(steps_events) == 1

        # done
        assert events[-1]["done"] is True

    @patch("api.workflow_cache.store_clarification_context")
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    async def test_clarification_chains_to_another_clarification(
        self,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
        mock_store_asst,
        mock_get_assistant,
        mock_store_ctx,
    ) -> None:
        from api.routers.chat import generate_clarification_response_stream

        mock_get_settings.return_value = _mock_settings()
        second_clar = _clarification_request(
            parameter_name="year",
            prompt="Which year?",
            allowed_values=["2023", "2024"],
        )
        mock_process_query.return_value = second_clar
        mock_create_clients.return_value = MagicMock()
        mock_get_assistant.return_value = None

        ctx = _clarification_request()
        chunks = await _collect(
            generate_clarification_response_stream(
                clarification_ctx=ctx,
                message="Seattle",
                request_id="clarify_abc123",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        clar_events = [e for e in events if e.get("needs_clarification")]
        assert len(clar_events) == 1
        assert clar_events[0]["clarification"]["parameter_name"] == "year"
        assert events[-1]["done"] is True


# ── Error Handling ──────────────────────────────────────────────────────


class TestErrorHandling:
    """Verify SSE error events when exceptions are raised."""

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_process_query_exception(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant
        mock_process_query.side_effect = RuntimeError("DB connection failed")
        mock_create_clients.return_value = MagicMock()

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        error_events = [e for e in events if "error" in e]
        assert len(error_events) == 1
        assert "internal error" in error_events[0]["error"].lower()
        assert "correlation_id" in error_events[0]
        assert error_events[0]["done"] is True

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_classify_intent_exception(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        assistant.classify_intent = AsyncMock(
            side_effect=ValueError("LLM returned garbage"),
        )
        mock_get_assistant.return_value = assistant

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="???",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        error_events = [e for e in events if "error" in e]
        assert len(error_events) == 1
        assert error_events[0]["done"] is True

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    async def test_clarification_stream_error(
        self,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_clarification_response_stream

        mock_get_settings.return_value = _mock_settings()
        mock_process_query.side_effect = OSError("Network error")
        mock_create_clients.return_value = MagicMock()

        ctx = _clarification_request()
        chunks = await _collect(
            generate_clarification_response_stream(
                clarification_ctx=ctx,
                message="Seattle",
                request_id="clarify_abc",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        error_events = [e for e in events if "error" in e]
        assert len(error_events) == 1
        assert "internal error" in error_events[0]["error"].lower()


# ── Step Event Drain ────────────────────────────────────────────────────


class TestStepEventDrain:
    """Verify queued step events are emitted before the main response."""

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_step_events_drained_before_response(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:

        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant
        mock_create_clients.return_value = MagicMock()

        response = _success_response()

        async def _process_with_step_events(request, clients):
            """Simulate process_query that enqueues step events."""
            from api.step_events import get_step_queue

            q = get_step_queue()
            if q is not None:
                q.put_nowait({
                    "step": "Searching templates...",
                    "status": "started",
                })
                q.put_nowait({
                    "step": "Searching templates...",
                    "status": "completed",
                    "duration_ms": 42,
                })
            return response

        mock_process_query.side_effect = _process_with_step_events

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # Find step events from the pipeline (not the orchestrator's own)
        search_steps = [e for e in events if e.get("step") == "Searching templates..."]
        assert len(search_steps) == 2
        assert search_steps[0]["status"] == "started"
        assert search_steps[1]["status"] == "completed"

        # They must appear before the tool_call / text response
        tool_idx = next(i for i, e in enumerate(events) if "tool_call" in e)
        search_idx = next(
            i for i, e in enumerate(events) if e.get("step") == "Searching templates..."
        )
        assert search_idx < tool_idx


# ── Session Cache Integration ───────────────────────────────────────────


class TestSessionCacheIntegration:
    """Verify DataAssistant creation and reuse via session cache."""

    @patch(_ORCH_PATCHES["DefaultCred"])
    @patch(_ORCH_PATCHES["AIProjectClient"])
    @patch(_ORCH_PATCHES["FoundryAgent"])
    @patch(_ORCH_PATCHES["load_prompt"], return_value="test prompt")
    @patch(_ORCH_PATCHES["DataAssistant"])
    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_creates_new_assistant_when_cache_miss(  # noqa: PLR0913, PLR0917
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
        mock_data_assistant_cls,
        mock_load_prompt,
        mock_foundry_agent_cls,
        mock_project_client_cls,
        mock_default_cred_cls,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        mock_get_assistant.return_value = None  # cache miss

        new_assistant = _mock_assistant()
        mock_data_assistant_cls.return_value = new_assistant
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()

        await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id=None,
            ),
        )

        mock_data_assistant_cls.assert_called_once()
        mock_foundry_agent_cls.assert_called_once()
        mock_project_client_cls.assert_called_once()

    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_reuses_cached_assistant(
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        mock_get_settings.return_value = _mock_settings()
        assistant = _mock_assistant()
        mock_get_assistant.return_value = assistant  # cache hit
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()

        chunks = await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id="test-thread-123",
            ),
        )
        events = _parse_sse_events(chunks)

        # Should succeed without creating new assistant
        assert events[-1]["done"] is True
        assistant.classify_intent.assert_called_once_with("Show orders")

    @patch(_ORCH_PATCHES["DefaultCred"])
    @patch(_ORCH_PATCHES["AIProjectClient"])
    @patch(_ORCH_PATCHES["FoundryAgent"])
    @patch(_ORCH_PATCHES["load_prompt"], return_value="test prompt")
    @patch(_ORCH_PATCHES["DataAssistant"])
    @patch(_ORCH_PATCHES["process_query"], new_callable=AsyncMock)
    @patch(_ORCH_PATCHES["create_clients"])
    @patch(_ORCH_PATCHES["get_settings"])
    @patch(_ORCH_PATCHES["get_assistant"])
    @patch(_ORCH_PATCHES["store_assistant"])
    async def test_uses_managed_identity_when_client_id_set(  # noqa: PLR0913, PLR0917
        self,
        mock_store,
        mock_get_assistant,
        mock_get_settings,
        mock_create_clients,
        mock_process_query,
        mock_data_assistant_cls,
        mock_load_prompt,
        mock_foundry_agent_cls,
        mock_project_client_cls,
        mock_default_cred_cls,
    ) -> None:
        from api.routers.chat import generate_orchestrator_streaming_response

        settings = _mock_settings()
        settings.azure_client_id = "my-client-id"
        mock_get_settings.return_value = settings
        mock_get_assistant.return_value = None

        new_assistant = _mock_assistant()
        mock_data_assistant_cls.return_value = new_assistant
        mock_process_query.return_value = _success_response()
        mock_create_clients.return_value = MagicMock()

        await _collect(
            generate_orchestrator_streaming_response(
                message="Show orders",
                conversation_id=None,
            ),
        )

        mock_default_cred_cls.assert_called_once_with(
            managed_identity_client_id="my-client-id",
        )
