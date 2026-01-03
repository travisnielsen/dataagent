"""
Chat Agent Executor for workflow integration.

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
    ChatMessage,
    Executor,
    Role,
    WorkflowContext,
    handler,
)
from agent_framework_azure_ai import AzureAIAgentClient
from typing_extensions import Never

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import NL2SQLResponse  # type: ignore[import-not-found]
except ImportError:
    from src.entities.models import NL2SQLResponse

logger = logging.getLogger(__name__)

# Shared state keys for thread management
FOUNDRY_THREAD_ID_KEY = "foundry_thread_id"


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


class ChatAgentExecutor(Executor):
    """
    Executor that handles user-facing chat interactions.

    This executor:
    1. Receives user messages
    2. Forwards data questions to the NL2SQL executor
    3. Renders structured responses for the chat client
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "chat"):
        """
        Initialize the Chat executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        self.agent = ChatAgent(
            name="chat-agent",
            instructions=instructions,
            chat_client=chat_client,
        )

        super().__init__(id=executor_id)
        logger.info("ChatAgentExecutor initialized")

    async def _get_or_create_thread(self, ctx: WorkflowContext[Any, Any]) -> AgentThread:
        """
        Get existing Foundry thread from shared state or create a new one.
        
        The first executor to call this creates the thread and stores the ID
        in shared state. Subsequent calls return a thread with the same ID.
        """
        try:
            thread_id = await ctx.get_shared_state(FOUNDRY_THREAD_ID_KEY)
            if thread_id:
                logger.info("Using existing Foundry thread: %s", thread_id)
                return self.agent.get_new_thread(service_thread_id=thread_id)
        except KeyError:
            pass
        
        # Create a new thread - Foundry will assign the ID on first run
        logger.info("Creating new Foundry thread")
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
            logger.info("Stored Foundry thread ID in shared state: %s", thread.service_thread_id)

    @handler
    async def handle_chat_message(
        self,
        message: ChatMessage,
        ctx: WorkflowContext[str]
    ) -> None:
        """
        Handle a single ChatMessage (e.g., from DevUI).

        Args:
            message: Single chat message
            ctx: Workflow context for sending to next executor
        """
        user_text = message.text or ""
        logger.info("ChatAgentExecutor received user message: %s", user_text[:100] if user_text else "(empty)")

        # Forward the question to NL2SQL executor
        await ctx.send_message(user_text)

    @handler
    async def handle_user_messages(
        self,
        messages: list[ChatMessage],
        ctx: WorkflowContext[str]
    ) -> None:
        """
        Handle a list of ChatMessages (for workflow.as_agent() compatibility).

        Args:
            messages: List of chat messages
            ctx: Workflow context for sending to next executor
        """
        # Get the last user message
        user_text = ""
        for msg in reversed(messages):
            if msg.role == Role.USER and msg.text:
                user_text = msg.text
                break

        logger.info("ChatAgentExecutor received user messages: %s", user_text[:100] if user_text else "(empty)")

        # Forward the question to NL2SQL executor
        await ctx.send_message(user_text)

    @handler
    async def handle_nl2sql_response(
        self,
        response_json: str,
        ctx: WorkflowContext[Never, str]
    ) -> None:
        """
        Handle structured response from NL2SQL and render for user.

        Args:
            response_json: JSON string containing NL2SQL response with query results
            ctx: Workflow context for yielding final output
        """
        logger.info("ChatAgentExecutor rendering NL2SQL response")

        # Deserialize the JSON string back to NL2SQLResponse model
        response = NL2SQLResponse.model_validate_json(response_json)

        # Build a prompt for the chat agent to render the response
        render_prompt = self._build_render_prompt(response)

        # Get or create the Foundry thread for this conversation
        thread = await self._get_or_create_thread(ctx)

        # Use the chat agent to generate a user-friendly response with the thread
        agent_response = await self.agent.run(render_prompt, thread=thread)

        # Store the thread ID after the run (Foundry assigns it on first run)
        await self._store_thread_id(ctx, thread)

        # Get the Foundry thread ID (may have been created by NL2SQL agent if this is first run)
        foundry_thread_id = thread.service_thread_id
        if not foundry_thread_id:
            try:
                foundry_thread_id = await ctx.get_shared_state(FOUNDRY_THREAD_ID_KEY)
            except KeyError:
                pass

        # Yield structured output with both text and thread ID
        final_text = agent_response.text or self._fallback_render(response)
        output = {
            "text": final_text,
            "thread_id": foundry_thread_id,
        }
        await ctx.yield_output(json.dumps(output))

    def _build_render_prompt(self, response: NL2SQLResponse) -> str:
        """Build a prompt for the chat agent to render the response."""
        if response.error:
            return f"""Please help the user understand this error from the data query:

Error: {response.error}

SQL Query attempted: {response.sql_query or 'None'}

Provide a helpful explanation of what went wrong and suggest how they might rephrase their question."""

        # Format the data for the agent
        data_preview = ""
        if response.sql_response:
            sample_rows = response.sql_response[:10]
            data_preview = json.dumps(sample_rows, indent=2, default=str)

        cache_info = ""
        if response.used_cached_query:
            cache_info = f"This used a pre-tested cached query with confidence score: {response.confidence_score:.2f}"
        else:
            cache_info = "This query was generated for this specific question."

        return f"""Please present these data query results to the user in a clear, well-formatted way:

**Query Results Summary:**
- Total rows returned: {response.row_count}
- Columns: {', '.join(response.columns)}
- {cache_info}

**SQL Query Used:**
```sql
{response.sql_query}
```

**Data (sample of up to 10 rows):**
```json
{data_preview}
```

Format this nicely with a markdown table and helpful context. If the data is empty, explain that no matching records were found."""

    def _fallback_render(self, response: NL2SQLResponse) -> str:
        """Fallback rendering if the agent fails."""
        if response.error:
            return f"**Error:** {response.error}"

        lines = [f"**Query Results** ({response.row_count} rows)\n"]

        if response.columns and response.sql_response:
            lines.append("| " + " | ".join(response.columns) + " |")
            lines.append("| " + " | ".join(["---"] * len(response.columns)) + " |")

            for row in response.sql_response[:10]:
                values = [str(row.get(col, "")) for col in response.columns]
                lines.append("| " + " | ".join(values) + " |")

        if response.sql_query:
            lines.append(f"\n<details><summary>SQL Query</summary>\n\n```sql\n{response.sql_query}\n```\n</details>")

        return "\n".join(lines)
