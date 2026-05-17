"""DataAssistant — manages chat sessions and invokes the NL2SQL workflow.

This agent owns the Foundry conversation session and history.
It classifies user intent, invokes the NL2SQL workflow for data questions,
and handles conversational refinements.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryAgent
from models import (
    NL2SQLRequest,
    NL2SQLResponse,
    ScenarioAssumptionSet,
    ScenarioIntent,
    SchemaSuggestion,
)
from shared.scenario_constants import (
    SCENARIO_ROUTING_CONFIDENCE_THRESHOLD,
    SCENARIO_TYPE_DEMAND,
    SCENARIO_TYPE_INVENTORY_POLICY,
    SCENARIO_TYPE_PRICE,
    SCENARIO_TYPE_SUPPLIER_COST,
)

logger = logging.getLogger(__name__)

SCHEMA_SUGGESTIONS: dict[str, list[SchemaSuggestion]] = {
    "sales": [
        SchemaSuggestion(
            title="Order trends",
            prompt="Show me order trends over the last 6 months",
        ),
        SchemaSuggestion(
            title="Invoice details",
            prompt="Drill into invoice line items for the most recent orders",
        ),
        SchemaSuggestion(
            title="Customer categories",
            prompt="Compare total revenue across customer buying groups",
        ),
        SchemaSuggestion(
            title="Special deals",
            prompt="Show active special deals and their discount percentages",
        ),
    ],
    "purchasing": [
        SchemaSuggestion(
            title="PO status",
            prompt="Track purchase order status and expected delivery dates",
        ),
        SchemaSuggestion(
            title="Supplier performance",
            prompt="Analyze supplier categories and order volumes",
        ),
        SchemaSuggestion(
            title="Supplier transactions",
            prompt="Review recent supplier transaction history",
        ),
    ],
    "warehouse": [
        SchemaSuggestion(
            title="Stock levels",
            prompt="Check current stock levels and holdings across warehouses",
        ),
        SchemaSuggestion(
            title="Stock categories",
            prompt="Explore stock groups and item categories",
        ),
        SchemaSuggestion(
            title="Stock transactions",
            prompt="Review stock transaction history for the last 30 days",
        ),
        SchemaSuggestion(
            title="Package types",
            prompt="Analyze color and package type distributions for stock items",
        ),
    ],
    "application": [
        SchemaSuggestion(
            title="People & contacts",
            prompt="Look up people, their roles, and contact information",
        ),
        SchemaSuggestion(
            title="Geographic data",
            prompt="Explore cities, states, and countries in the system",
        ),
        SchemaSuggestion(
            title="Delivery methods",
            prompt="Review available delivery and payment methods",
        ),
    ],
}


def _detect_schema_area(tables: list[str]) -> str | None:
    """Detect schema area from fully-qualified table names.

    Uses the FROM clause's primary table (first in list). Extracts the schema
    prefix (e.g., 'Sales.Orders' -> 'sales').

    Args:
        tables: List of fully-qualified table names (e.g., ['Sales.Orders', 'Application.People'])

    Returns:
        Lowercase schema area name, or None if undetectable.
    """
    if not tables:
        return None
    first_table = tables[0]
    if "." not in first_table:
        return None
    area = first_table.split(".")[0].lower()
    if area not in SCHEMA_SUGGESTIONS:
        return None
    return area


@dataclass
class ConversationContext:
    """Tracks the context of the current conversation for refinements."""

    # Template-based query context
    last_template_json: str | None = None
    last_params: dict[str, Any] = field(default_factory=dict)
    last_defaults_used: dict[str, Any] = field(default_factory=dict)
    last_query: str = ""

    # Dynamic query context
    last_sql: str | None = None
    last_tables: list[str] = field(default_factory=list)
    last_tables_json: str | None = None
    last_question: str = ""
    query_source: str = ""  # "template" or "dynamic"
    pending_dynamic_confirmation: bool = False

    # Schema area context
    current_schema_area: str | None = None
    schema_exploration_depth: int = 0


@dataclass
class ClassificationResult:
    """Result of intent classification."""

    intent: str  # "data_query", "refinement", "conversation", "scenario"
    query: str = ""  # The query to process (may be rewritten for refinements)
    param_overrides: dict = field(default_factory=dict)  # For refinements
    confirmation_action: str | None = None  # "accept" | "revise" | "none"
    scenario_intent: ScenarioIntent | None = None  # Populated for scenario intent
    scenario_discovery: bool = False  # LLM signals user is asking about scenario capabilities


def load_assistant_prompt() -> str:
    """Load the DataAssistant instructions prompt."""
    prompt_path = Path(__file__).parent / "assistant_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


class DataAssistant:
    """Manages conversation flow and invokes the NL2SQL workflow.

    Responsibilities:
    1. Own the Foundry conversation session and history
    2. Classify user intent (data query, refinement, conversation)
    3. Invoke NL2SQL workflow for data questions
    4. Handle conversational refinements using previous context
    5. Render results back to the user
    """

    def __init__(self, agent: Agent | FoundryAgent, conversation_id: str | None = None) -> None:
        """Initialize the DataAssistant.

        Args:
            agent: Pre-configured agent for LLM calls (``Agent`` or ``FoundryAgent``).
            conversation_id: Optional existing Foundry conversation ID to resume.
        """
        self.agent = agent
        self.context = ConversationContext()
        self._thread: AgentSession | None = None
        self._initial_conversation_id = conversation_id

        logger.debug("DataAssistant initialized (conversation_id=%s)", conversation_id)

    @property
    def conversation_id(self) -> str | None:
        """Get the current Foundry conversation ID."""
        if self._thread:
            service_session_id = getattr(self._thread, "service_session_id", None)
            if service_session_id:
                return service_session_id
            return self._thread.session_id
        return self._initial_conversation_id

    async def get_or_create_conversation(self) -> AgentSession:
        """Get or create the Foundry conversation session."""
        if self._thread is not None:
            return self._thread

        if self._initial_conversation_id:
            self._thread = self.agent.get_session(service_session_id=self._initial_conversation_id)
            logger.debug(
                "Resumed conversation: incoming_conversation_id=%s session_id=%s service_session_id=%s",
                self._initial_conversation_id,
                getattr(self._thread, "session_id", None),
                getattr(self._thread, "service_session_id", None),
            )
        else:
            self._thread = self.agent.create_session()
            logger.debug(
                "Created new conversation session: session_id=%s service_session_id=%s",
                getattr(self._thread, "session_id", None),
                getattr(self._thread, "service_session_id", None),
            )

        return self._thread

    async def classify_intent(self, user_message: str) -> ClassificationResult:
        """Classify the user's intent using the LLM.

        The LLM considers conversation history to detect refinements.

        Returns:
            ClassificationResult with intent type and any extracted overrides.
        """
        thread = await self.get_or_create_conversation()

        context_info = ""
        if self.context.query_source == "template" and self.context.last_template_json:
            try:
                template_data = json.loads(self.context.last_template_json)
                param_names = [p.get("name") for p in template_data.get("parameters", [])]
                context_info = f"""
Previous query context (TEMPLATE-BASED):
- Question: {self.context.last_query}
- Parameters used: {json.dumps(self.context.last_params)}
- Available parameters to modify: {param_names}
- Defaults that were applied: {json.dumps(self.context.last_defaults_used)}
"""
            except json.JSONDecodeError:
                pass
        elif self.context.query_source == "dynamic" and self.context.last_sql:
            context_info = f"""
Previous query context (DYNAMIC):
- Original question: {self.context.last_question}
- Tables used: {self.context.last_tables}
- SQL executed: {self.context.last_sql[:500]}...
"""

        pending_confirmation_info = ""
        if self.context.pending_dynamic_confirmation and self.context.query_source == "dynamic":
            pending_confirmation_info = f"""
    Pending dynamic confirmation context:
    - A dynamic SQL query is awaiting user approval before execution.
    - Previous question: {self.context.last_question}
    - Proposed SQL: {self.context.last_sql[:500] if self.context.last_sql else ""}...
    - If the user approves execution, set intent to "refinement" and confirmation_action to "accept".
    - If the user asks to change the query, set intent to "refinement" and confirmation_action to "revise".
    - If the user is off-topic, set intent to "conversation" and confirmation_action to "none".
    """

        classification_prompt = f"""Classify this user message and respond with JSON only.

{context_info}
    {pending_confirmation_info}

User message: {user_message}

Respond with ONE of these JSON formats:

1. For a NEW data question (asking about business data like sales, orders, customers, products):
{{"intent": "data_query", "query": "<the question>"}}

2. For a REFINEMENT of the previous query (e.g., "show me for 90 days", "what about last month", "make it 20", "filter by X"):
{{"intent": "refinement", "query": "<describe what the user wants changed>"}}

3. For general CONVERSATION (greetings, jokes, help, off-topic):
{{"intent": "conversation"}}

3b. For conversation where the user is ASKING ABOUT SCENARIO CAPABILITIES (what scenarios exist, what analyses are available, what what-if options they can explore):
{{"intent": "conversation", "scenario_discovery": true}}

4. For a WHAT-IF/SCENARIO question (hypothetical changes, assumptions about alternate outcomes):
{{"intent": "scenario", "query": "<the question>", "scenario_confidence": <0.0-1.0>, "detected_patterns": ["<key phrases>"], "reason": "<brief explanation>"}}

Examples of SCENARIO questions:
- "What if we raise prices by 5%?"
- "Assume costs increase 10%, what happens to profit?"
- "If we changed supplier pricing, how would revenue be affected?"
- "Show me the impact of raising demand by 20%"

NOT scenarios (these are conversation with scenario_discovery=true):
- "What scenarios can you do?" (conversation + scenario_discovery)
- "What what-if analyses are available?" (conversation + scenario_discovery)
- "Tell me about your what-if capabilities" (conversation + scenario_discovery)
- "What kinds of analysis can you run?" (conversation + scenario_discovery)

NOT scenario_discovery (these are data_query — exploring actual data):
- "Explore stock groups and item categories" (data_query — browsing data)
- "Explore customer orders" (data_query — browsing data)
- "What are the top selling products?" (data_query — descriptive analytics)
- "Show me product categories" (data_query — listing data)
- "If there are orders from Seattle, show them" (data_query — conditional filter)
- "Show me what happened last month" (data_query — historical lookup)
scenario_discovery is ONLY when the user explicitly asks about what-if capabilities or scenario features.
A query that explores or browses actual business data is ALWAYS a data_query, never scenario_discovery.
A scenario MUST involve a hypothetical assumption or change to explore alternate outcomes.

If pending dynamic confirmation context exists, include:
{{"confirmation_action": "accept" | "revise" | "none"}}

Important: A refinement ONLY applies if there was a previous query and the user is asking to modify it.

JSON response:"""

        result = await self.agent.run(
            classification_prompt,
            session=thread,
        )

        logger.debug(
            "classify_intent run completed: response_conversation_id=%s session_service_session_id=%s",
            getattr(result, "conversation_id", None),
            getattr(thread, "service_session_id", None),
        )

        response_text = result.text or ""

        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(response_text[json_start:json_end])

                intent = parsed.get("intent", "conversation")
                query = parsed.get("query", user_message)
                overrides = parsed.get("param_overrides", {})
                confirmation_action = parsed.get("confirmation_action")
                if isinstance(confirmation_action, str):
                    confirmation_action = confirmation_action.lower()
                else:
                    confirmation_action = None

                scenario_intent_obj = None
                if intent == "scenario":
                    try:
                        sc_conf = float(parsed.get("scenario_confidence", 0.0))
                    except (ValueError, TypeError):
                        sc_conf = 0.0
                    detected = parsed.get("detected_patterns", [])
                    if not isinstance(detected, list):
                        detected = []
                    reason = parsed.get("reason", "Scenario intent detected")

                    if sc_conf >= SCENARIO_ROUTING_CONFIDENCE_THRESHOLD and detected:
                        scenario_intent_obj = ScenarioIntent(
                            mode="scenario",
                            confidence=sc_conf,
                            reason=reason,
                            detected_patterns=detected,
                        )
                    else:
                        logger.info(
                            "Scenario below threshold (%.3f) or "
                            "no patterns — falling back to "
                            "data_query",
                            sc_conf,
                        )
                        intent = "data_query"

                logger.debug(
                    "Classified intent: %s (query=%s, overrides=%s, confirmation_action=%s, conversation_id=%s)",
                    intent,
                    query[:50],
                    overrides,
                    confirmation_action,
                    self.conversation_id,
                )
                scenario_discovery = bool(parsed.get("scenario_discovery", False))

                return ClassificationResult(
                    intent=intent,
                    query=query,
                    param_overrides=overrides,
                    confirmation_action=confirmation_action,
                    scenario_intent=scenario_intent_obj,
                    scenario_discovery=scenario_discovery,
                )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse classification response: %s", e)

        return ClassificationResult(intent="conversation")

    async def handle_conversation(self, user_message: str) -> str:
        """Handle general conversation using the LLM.

        Returns:
            The agent's conversational response.
        """
        thread = await self.get_or_create_conversation()

        result = await self.agent.run(
            user_message,
            session=thread,
        )

        logger.debug(
            "handle_conversation run completed: response_conversation_id=%s session_service_session_id=%s",
            getattr(result, "conversation_id", None),
            getattr(thread, "service_session_id", None),
        )

        return result.text or ""

    def build_nl2sql_request(self, classification: ClassificationResult) -> NL2SQLRequest:
        """Build an NL2SQL request based on the classification.

        For refinements, includes the previous template/query context.
        """
        if classification.intent == "refinement":
            if self.context.query_source == "template" and self.context.last_template_json:
                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_template_json=self.context.last_template_json,
                    base_params=self.context.last_params,
                    param_overrides=classification.param_overrides,
                )
            if self.context.query_source == "dynamic" and self.context.last_sql:
                pending_confirmation = self.context.pending_dynamic_confirmation
                action = classification.confirmation_action
                is_confirmed = pending_confirmation and action == "accept"
                should_reprompt_confirmation = pending_confirmation and action not in {
                    "accept",
                    "revise",
                }
                if pending_confirmation and action in {"accept", "revise"}:
                    self.context.pending_dynamic_confirmation = False

                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_sql=self.context.last_sql,
                    previous_tables=self.context.last_tables,
                    previous_tables_json=self.context.last_tables_json,
                    previous_question=self.context.last_question,
                    confirm_previous_sql=is_confirmed,
                    reprompt_pending_confirmation=should_reprompt_confirmation,
                )

        return NL2SQLRequest(
            user_query=classification.query,
            is_refinement=False,
        )

    @staticmethod
    def _infer_scenario_type(detected_patterns: list[str]) -> str:
        """Infer the scenario type from detected language patterns.

        Args:
            detected_patterns: Phrases from intent classification.

        Returns:
            A supported scenario type constant.
        """
        text = " ".join(detected_patterns).lower()
        if any(kw in text for kw in ("demand", "volume", "order")):
            return SCENARIO_TYPE_DEMAND
        if any(kw in text for kw in ("supplier", "purchasing")):
            return SCENARIO_TYPE_SUPPLIER_COST
        if any(kw in text for kw in ("inventory", "reorder", "stock")):
            return SCENARIO_TYPE_INVENTORY_POLICY
        return SCENARIO_TYPE_PRICE

    def build_scenario_assumption_set(
        self,
        scenario_intent: ScenarioIntent,
        _user_query: str,
    ) -> ScenarioAssumptionSet:
        """Build a preliminary ScenarioAssumptionSet from intent.

        Phase 3 creates an incomplete set identifying the scenario
        type.  Full assumption extraction is in later phases.

        Args:
            scenario_intent: Classified scenario intent.
            _user_query: Original user message (reserved for future use).

        Returns:
            An incomplete ``ScenarioAssumptionSet``.
        """
        scenario_type = self._infer_scenario_type(
            scenario_intent.detected_patterns,
        )
        return ScenarioAssumptionSet(
            scenario_type=scenario_type,
            assumptions=[],
            missing_requirements=["Full assumption extraction pending"],
            is_complete=False,
        )

    _CROSS_AREA_DEPTH_THRESHOLD = 3

    @staticmethod
    def _build_suggestions(
        schema_area: str | None,
        depth: int,
        *,
        has_results: bool = True,
    ) -> list[SchemaSuggestion]:
        """Select contextual follow-up suggestions based on schema area and depth.

        Args:
            schema_area: Current schema area (e.g., 'sales') or None.
            depth: How many consecutive queries in this area.
            has_results: Whether the query returned data (for empty-result recovery).

        Returns:
            2-3 relevant suggestions, or empty list if area is None.
        """
        if schema_area is None:
            return []

        area_suggestions = SCHEMA_SUGGESTIONS.get(schema_area, [])
        if not area_suggestions:
            return []

        count = len(area_suggestions)
        start = (depth - 1) % count
        rotated = [*area_suggestions[start:], *area_suggestions[:start]]
        selected = rotated[:3]

        if depth >= DataAssistant._CROSS_AREA_DEPTH_THRESHOLD:
            sorted_areas = sorted(SCHEMA_SUGGESTIONS.keys())
            current_idx = sorted_areas.index(schema_area)
            next_area = sorted_areas[(current_idx + 1) % len(sorted_areas)]
            cross_suggestion = SCHEMA_SUGGESTIONS[next_area][0]
            selected = [*selected[:2], cross_suggestion]

        if not has_results:
            recovery = SchemaSuggestion(
                title="Try broader filters",
                prompt=f"Show me all data in the {schema_area} area",
            )
            selected = [recovery, *selected[:2]]

        return selected

    def update_context(
        self, response: NL2SQLResponse, template_json: str | None, params: dict
    ) -> None:
        """Update conversation context after a successful query.

        Args:
            response: The NL2SQL response
            template_json: JSON of the template used (if any)
            params: The parameters that were used
        """
        if not response.error and response.sql_query:
            self.context.pending_dynamic_confirmation = bool(
                response.query_source == "dynamic"
                and response.needs_clarification
                and response.query_summary
            )
            self.context.query_source = response.query_source

            if response.query_source == "template" and template_json:
                self.context.last_template_json = template_json
                self.context.last_params = params
                self.context.last_defaults_used = response.defaults_used
                self.context.last_query = response.sql_query
                self.context.last_sql = None
                self.context.last_tables = []
                self.context.last_tables_json = None
                self.context.last_question = ""
            else:
                self.context.last_sql = response.sql_query
                self.context.last_tables = response.tables_used
                self.context.last_tables_json = response.tables_metadata_json
                self.context.last_question = response.original_question
                self.context.last_template_json = None
                self.context.last_params = {}
                self.context.last_defaults_used = {}
                self.context.last_query = response.sql_query

            if response.tables_used:
                tables = response.tables_used
            else:
                tables = re.findall(r"(?:FROM|JOIN)\s+([\w.]+)", response.sql_query, re.IGNORECASE)

            detected_area = _detect_schema_area(tables)
            if detected_area == self.context.current_schema_area:
                self.context.schema_exploration_depth += 1
            else:
                self.context.schema_exploration_depth = 1
            self.context.current_schema_area = detected_area

            logger.info(
                "Updated context for %s query refinement (schema_area=%s, depth=%d)",
                self.context.query_source,
                self.context.current_schema_area,
                self.context.schema_exploration_depth,
            )

    def enrich_response(self, response: NL2SQLResponse) -> NL2SQLResponse:
        """Add contextual suggestions to the response based on schema area.

        Called after update_context() so schema area is current.
        """
        if not response.error and not response.needs_clarification:
            has_results = len(response.sql_response) > 0
            suggestions = self._build_suggestions(
                self.context.current_schema_area,
                self.context.schema_exploration_depth,
                has_results=has_results,
            )
            response.suggestions = suggestions
        return response

    def render_response(self, response: NL2SQLResponse) -> dict:
        """Render the NL2SQL response for the frontend.

        Returns:
            A structured dict with text, conversation_id, and tool_call data.
        """
        text = self._format_response_text(response)

        return {
            "text": text,
            "conversation_id": self.conversation_id,
            "tool_call": {
                "tool_name": "nl2sql_query",
                "tool_call_id": f"nl2sql_{id(response)}",
                "args": {},
                "result": {
                    "sql_query": response.sql_query,
                    "sql_response": response.sql_response,
                    "columns": response.columns,
                    "row_count": response.row_count,
                    "confidence_score": response.confidence_score,
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,
                    "needs_clarification": response.needs_clarification,
                    "clarification": response.clarification.model_dump()
                    if response.clarification
                    else None,
                    "defaults_used": response.defaults_used,
                    "suggestions": [s.model_dump() for s in response.suggestions],
                    "hidden_columns": response.hidden_columns,
                    "query_summary": response.query_summary or None,
                    "query_confidence": response.query_confidence,
                    "error_suggestions": [s.model_dump() for s in response.error_suggestions],
                    "is_scenario": response.is_scenario,
                    "scenario_type": response.scenario_type,
                },
            },
        }

    @staticmethod
    def _format_response_text(response: NL2SQLResponse) -> str:
        """Format the response as markdown text."""
        if response.needs_clarification and response.clarification:
            clarification = response.clarification
            lines = [f"**{clarification.prompt}**\n"]
            if clarification.allowed_values:
                lines.append("Valid options: " + ", ".join(clarification.allowed_values))
            return "\n".join(lines)

        if response.error:
            return f"**Error:** {response.error}"

        lines = []

        if response.defaults_used:
            descriptions = list(response.defaults_used.values())
            if len(descriptions) == 1:
                lines.append(f"*Using default: {descriptions[0]}*\n")
            else:
                lines.append(f"*Using defaults: {', '.join(descriptions)}*\n")

        lines.append(f"**Query Results** ({response.row_count} rows)\n")

        if response.columns and response.sql_response:
            lines.append("| " + " | ".join(response.columns) + " |")
            lines.append("| " + " | ".join(["---"] * len(response.columns)) + " |")

            for row in response.sql_response[:10]:
                values = [str(row.get(col, "")) for col in response.columns]
                lines.append("| " + " | ".join(values) + " |")

        if response.sql_query:
            lines.append(
                f"\n<details><summary>SQL Query</summary>\n\n```sql\n{response.sql_query}\n```\n</details>"
            )

        return "\n".join(lines)
