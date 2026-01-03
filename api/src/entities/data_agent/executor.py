"""
NL2SQL Agent Executor for workflow integration.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
from pathlib import Path
from typing import Any

from agent_framework import (
    AgentThread,
    ChatAgent,
    Executor,
    Role,
    WorkflowContext,
    handler,
)
from agent_framework_azure_ai import AzureAIAgentClient

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import NL2SQLResponse  # type: ignore[import-not-found]
except ImportError:
    from src.entities.models import NL2SQLResponse

from .tools import execute_sql, search_cached_queries

logger = logging.getLogger(__name__)

# Shared state key for Foundry thread ID (must match ChatAgentExecutor)
FOUNDRY_THREAD_ID_KEY = "foundry_thread_id"


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


class NL2SQLAgentExecutor(Executor):
    """
    Executor that handles NL2SQL data queries.

    This executor:
    1. Receives user questions from the ChatAgentExecutor
    2. Uses the NL2SQL agent to search cached queries and execute SQL
    3. Returns structured NL2SQLResponse data
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "nl2sql"):
        """
        Initialize the NL2SQL executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        self.agent = ChatAgent(
            name="nl2sql-agent",
            instructions=instructions,
            chat_client=chat_client,
            tools=[search_cached_queries, execute_sql],
        )

        super().__init__(id=executor_id)
        logger.info("NL2SQLAgentExecutor initialized with tools: ['search_cached_queries', 'execute_sql']")

    async def _get_or_create_thread(self, ctx: WorkflowContext[Any, Any]) -> AgentThread:
        """
        Get existing Foundry thread from shared state or create a new one.
        
        The ChatAgentExecutor typically creates the thread first. This executor
        retrieves it from shared state to continue the same conversation.
        """
        try:
            thread_id = await ctx.get_shared_state(FOUNDRY_THREAD_ID_KEY)
            if thread_id:
                logger.info("NL2SQL using existing Foundry thread: %s", thread_id)
                return self.agent.get_new_thread(service_thread_id=thread_id)
        except KeyError:
            pass
        
        # Create a new thread if none exists yet
        logger.info("NL2SQL creating new Foundry thread")
        return self.agent.get_new_thread()
    
    async def _store_thread_id(self, ctx: WorkflowContext[Any, Any], thread: AgentThread) -> None:
        """Store the Foundry thread ID in shared state if it was created."""
        if thread.service_thread_id:
            try:
                existing = await ctx.get_shared_state(FOUNDRY_THREAD_ID_KEY)
                if existing:
                    return  # Already stored
            except KeyError:
                pass
            await ctx.set_shared_state(FOUNDRY_THREAD_ID_KEY, thread.service_thread_id)
            logger.info("NL2SQL stored Foundry thread ID: %s", thread.service_thread_id)

    @handler
    async def handle_question(
        self,
        question: str,
        ctx: WorkflowContext[str]
    ) -> None:
        """
        Handle a user question by running the NL2SQL agent.

        Args:
            question: The user's natural language question
            ctx: Workflow context for sending the response (as JSON string)
        """
        logger.info("NL2SQLAgentExecutor processing question: %s", question[:100])

        try:
            # Get or create the Foundry thread for this conversation
            thread = await self._get_or_create_thread(ctx)

            # Run the NL2SQL agent with the thread
            response = await self.agent.run(question, thread=thread)

            # Store the thread ID after the run (Foundry assigns it on first run)
            await self._store_thread_id(ctx, thread)

            # Parse the agent's response to extract structured data
            nl2sql_response = self._parse_agent_response(response)

            logger.info(
                "NL2SQL completed: rows=%d, cached=%s, confidence=%.2f",
                nl2sql_response.row_count,
                nl2sql_response.used_cached_query,
                nl2sql_response.confidence_score
            )

        except (ValueError, RuntimeError, OSError) as e:
            logger.error("NL2SQL execution error: %s", e)
            nl2sql_response = NL2SQLResponse(
                sql_query="",
                error=str(e)
            )

        # Send structured response as JSON string to the next executor
        await ctx.send_message(nl2sql_response.model_dump_json())

    def _parse_agent_response(self, response) -> NL2SQLResponse:
        """Parse the agent's response to extract structured data."""
        sql_query = ""
        sql_response: list[dict] = []
        columns: list[str] = []
        row_count = 0
        confidence_score = 0.0
        used_cached_query = False
        error = None

        # Extract data from tool call results in the messages
        for message in response.messages:
            if message.role == Role.TOOL:
                for content in message.contents:
                    if hasattr(content, 'result'):
                        result = content.result
                        # Parse JSON string if needed
                        if isinstance(result, str):
                            try:
                                result = json.loads(result)
                            except json.JSONDecodeError:
                                continue

                        if isinstance(result, dict):
                            # Check for execute_sql result
                            if 'rows' in result and result.get('success', False):
                                sql_response = result.get('rows', [])
                                columns = result.get('columns', [])
                                row_count = result.get('row_count', len(sql_response))

                            # Check for search result with confidence
                            if 'has_high_confidence_match' in result:
                                used_cached_query = result.get('has_high_confidence_match', False)
                                if 'best_match' in result and result['best_match']:
                                    best_match = result['best_match']
                                    confidence_score = best_match.get('score', 0.0)
                                    if used_cached_query:
                                        sql_query = best_match.get('query', '')

                            # Check for error
                            if not result.get('success', True) and 'error' in result:
                                error = result['error']

            # Look for function calls to get the SQL query
            if message.role == Role.ASSISTANT:
                for content in message.contents:
                    if hasattr(content, 'name') and content.name == 'execute_sql':
                        if hasattr(content, 'arguments'):
                            args = content.arguments
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    pass
                            if isinstance(args, dict) and 'query' in args:
                                sql_query = args['query']

        return NL2SQLResponse(
            sql_query=sql_query,
            sql_response=sql_response,
            columns=columns,
            row_count=row_count,
            confidence_score=confidence_score,
            used_cached_query=used_cached_query,
            error=error
        )
