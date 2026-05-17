# Feature Specification: Foundry Agent Framework Upgrade & Portal Trace Correlation

**Feature Branch**: `007-foundry-framework-upgrade`
**Created**: 2026-05-15 (split 2026-05-17, rescoped 2026-05-17)
**Status**: Draft
**Input**: User description: "The current runtime works in production using the Foundry Responses protocol with `conversation_id` — that is the GA-recommended path and the Foundry portal Traces tab is populated correctly today. The actual work is (a) escape the `agent-framework` 1.0.0rc4 release-candidate by upgrading to the stable 1.4.x line, (b) move to the documented public client symbol from `agent_framework.foundry` so we are no longer dependent on internal package layout, and (c) attach an `agent_reference` (name + id) to responses calls so the portal can correlate traces with the orchestrator agent record — the prerequisite that the eval rewrite in feature 008 needs in order to light up the **Analyze Results** affordance. No change to the multi-turn continuity model (it is already correct), no change to the SSE protocol, no change to the frontend."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Framework upgrade with zero behavior change (Priority: P1)

A returning user resumes a Cadence conversation and asks follow-up questions ("show me last week instead", "filter to North America"). Each turn builds on the previous turn's context exactly as it does today. Under the hood, the chat path now runs on `agent-framework` 1.4.x using the documented `FoundryChatClient` symbol instead of the rc4 `AzureAIClient`. The continuity primitive remains the server-managed Foundry conversation, identified by `conversation_id` (the GA-recommended pattern, surfaced as the **Conversation ID** column in the Foundry portal Traces tab).

**Why this priority**: Escaping the release-candidate dependency is the only forcing function for this feature. The current code is functionally correct; what's at risk is forward compatibility (rc4 will eventually fail to install, lose security patches, and diverge from documented public symbols). This story brings the project onto a supported public surface with no user-visible change.

**Independent Test**: After the upgrade, drive 3+ sequential turns through the `/api/chat/stream` endpoint. All turns succeed; the second and third turns demonstrably use prior-turn context (e.g., resolve a pronoun like "those" to entities from turn 1); the `conversation_id` echoed back on each `done` event is unchanged in shape and round-trips correctly. `uv run poe check` passes.

**Acceptance Scenarios**:

1. **Given** a fresh checkout with `agent-framework>=1.4.0,<2` resolved, **When** the backend starts, **Then** it imports its chat client from a documented public path (e.g., `agent_framework.foundry`) and does not import from any rc4-only internal module.
2. **Given** a multi-turn chat session, **When** the user sends a refinement, **Then** the assistant resolves it against the previous turn's context using the server-managed conversation (no client-computed `previous_response_id` is sent to the provider).
3. **Given** the merged change, **When** `pyproject.toml` is read, **Then** the resolved `agent-framework` version satisfies `>=1.4.0,<2` and no `*rc*` version remains.

---

### User Story 2 - Foundry portal correlates traces to the orchestrator agent (Priority: P1)

When an operator opens the **Traces** tab for the Cadence project in the Foundry portal, every row produced by a chat turn is correlated to the orchestrator agent record (e.g., `cadence-data-assistant`). The agent column is populated, the **Conversation ID** column carries the same id the SSE stream returned, and selecting a row navigates into the conversation timeline. This correlation is what feature 008's trace-based evaluation requires to filter traces by `gen_ai.agent.id` and to enable the **Analyze Results** affordance.

**Why this priority**: Microsoft Learn explicitly documents that responses calls must include `agent_reference` (with `name` and `id`) for portal-side trace→agent correlation. A repo search confirms Cadence currently sets neither. This is the single concrete root cause of the "**Analyze Results** is greyed out" symptom in the existing `cadence-eval-v1` evaluation; feature 008 cannot succeed until this is fixed.

**Independent Test**: Drive one chat turn end-to-end. Within 5 minutes, the new row in the Foundry portal Traces tab shows a populated agent column matching the configured orchestrator id, and the row's Conversation ID column matches the `conversation_id` value the SSE `done` event returned to the browser.

**Acceptance Scenarios**:

1. **Given** the orchestrator agent has been provisioned and its id is configured, **When** a chat turn runs, **Then** the responses call to the provider includes the agent reference (name + id).
2. **Given** that turn completes, **When** the operator opens the Foundry portal Traces tab and filters to the configured agent name, **Then** the new row appears with the matching **Conversation ID** value.
3. **Given** the orchestrator agent id is not yet configured, **When** the backend starts, **Then** it either provisions one (idempotently, from the configured prompt + model deployment) and logs the resulting id, or fails fast with a clear error directing the operator to set the configuration value — whichever is chosen, no silent fallback to "no agent reference" is acceptable.

---

### User Story 3 - Smaller, more idiomatic backend (Priority: P2)

A developer reading the chat router and the workflow client factory sees clearly that Cadence uses the documented Foundry public API: `from agent_framework.foundry import FoundryChatClient` (or the resolved 1.4.x equivalent), with `agent_reference` set on construction, and conversation continuity managed by the framework via `AgentSession`. No internal-package imports remain, and the chat router no longer carries a workaround for "client returned an awaitable from a synchronous method" because the 1.4.x client surface is consistently async.

**Why this priority**: Drops out of the upgrade. Listing it explicitly forces reviewers to verify the dead code (e.g., the `inspect.isawaitable(...)` workaround at [src/backend/api/routers/chat.py:294](src/backend/api/routers/chat.py)) actually goes away rather than being left as transitional scar tissue.

**Independent Test**: Repository search shows zero imports from `agent_framework_azure_ai` and zero references to the rc4-specific suppression in [src/backend/api/main.py:42](src/backend/api/main.py).

**Acceptance Scenarios**:

1. **Given** the merged change, **When** a developer greps the backend for `agent_framework_azure_ai`, **Then** no matches remain.
2. **Given** the upgrade is complete, **When** `uv run poe check` is executed, **Then** lint, typecheck, and tests all pass.

---

### Edge Cases

- The 1.4.x line moves the chat client symbol again or renames it between minor versions. Pinning to `>=1.4.0,<2` accepts that risk in exchange for patch uptake; if the import path changes mid-minor, treat that as a bug-fix follow-up, not a re-spec.
- The orchestrator agent id stored in configuration refers to an agent that has been deleted in Foundry. The next backend boot must surface a clear error pointing at the configuration value rather than silently failing portal correlation.
- The provider returns a transient error mid-conversation (e.g., 429). The client must retry without losing the existing conversation association.
- A `conversation_id` supplied by the client is no longer recognized by the provider (e.g., expired, from a different project). The next turn must surface a sanitized error and proceed by starting a fresh conversation on retry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The project's `agent-framework` dependency MUST be upgraded to a stable release line (target range `>=1.4.0,<2`). No `*rc*` version of `agent-framework` MAY remain in the resolved lockfile.
- **FR-002**: The chat backend MUST import its Foundry chat client from a documented public module path (the resolved 1.4.x equivalent of `agent_framework.foundry`). It MUST NOT import from any rc4-only or otherwise internal package path that the 1.4.x line replaced.
- **FR-003**: The runtime MUST continue to use the Foundry Responses protocol for multi-turn continuity. The server-managed conversation identifier MUST continue to flow through the existing `conversation_id` field of the SSE `done` event.
- **FR-004**: Every responses call issued by the chat path MUST include an agent reference (name + id) identifying the orchestrator agent record in the Foundry project, so the portal Traces tab can correlate the resulting trace to that agent.
- **FR-005**: The orchestrator agent's id MUST be resolvable from configuration (e.g., a settings field such as `AZURE_AI_ORCHESTRATOR_AGENT_ID`). When the configured id is absent or unknown to Foundry, the backend MUST EITHER (a) provision an agent from the existing `assistant_prompt.md` and configured model deployment and log the new id, OR (b) fail startup with a clear, actionable error — but MUST NOT silently proceed without the reference set.
- **FR-006**: Existing telemetry (SSE step events, scenario routing events, OpenTelemetry traces) MUST continue to function. Trace correlation to Application Insights and to the Foundry portal Traces tab MUST be preserved or improved (FR-004 is the only addition required for the portal to populate the agent column).
- **FR-007**: The migration MUST NOT introduce any new persistent storage. The orchestrator agent record itself is created in Foundry (not in Cadence's own datastores) and is referenced by id.
- **FR-008**: Workarounds in the current code that exist only because of rc4 surface quirks (notably the `inspect.isawaitable(...)` branch in [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py) and the rc4 warning suppression in [src/backend/api/main.py](src/backend/api/main.py)) MUST be removed if and only if the 1.4.x client surface makes them unnecessary.
- **FR-009**: The frontend SSE protocol MUST NOT change. `useChatApi.ts` and `chatApi.ts` continue to round-trip the same `conversation_id` field with the same opaque-string semantics.

### Key Entities

- **Foundry Conversation**: The server-managed conversation context provided by the Foundry Responses protocol. Identified by `conversation_id`. **This is the GA primitive** the Foundry portal Traces tab uses (as the "Conversation ID" column). Already in use today; preserved by this feature, not introduced by it.
- **Orchestrator Agent (Foundry record)**: A single Foundry agent record per deployment representing the Cadence DataAssistant. Identified by an `agent_id` (and a stable name such as `cadence-data-assistant`). Created once via the project SDK; referenced from every responses call so the portal can correlate traces to this agent. New configuration surface (`AZURE_AI_ORCHESTRATOR_AGENT_ID`) introduced by this feature.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After release, `uv pip show agent-framework` reports a version satisfying `>=1.4.0,<2`. The resolved lockfile contains no `*rc*` line for `agent-framework`.
- **SC-002**: After release, multi-turn chat behavior is unchanged from baseline: the success rate of "second turn references entity from first turn" on a smoke set of representative refinements is at parity with the pre-upgrade run.
- **SC-003**: Within 5 minutes of a chat turn completing, the corresponding row in the Foundry portal Traces tab shows (a) a populated agent column matching the configured orchestrator agent name and (b) a **Conversation ID** value matching the `conversation_id` echoed by the SSE stream.
- **SC-004**: After release, a backend repository search returns zero matches for `agent_framework_azure_ai` and zero matches for the rc4-specific warning suppression text in [src/backend/api/main.py](src/backend/api/main.py).
- **SC-005**: First-turn p95 latency is within ±10% of the pre-upgrade baseline; refinement-turn p95 latency is equal or better (any change is incidental — the request shape is unchanged).
- **SC-006**: Every chat turn after release produces a trace visible to feature 008's trace-based evaluation, filterable in Application Insights by `gen_ai.agent.id = "<configured-name>:<version>"`. This is the gate feature 008 needs to begin.

## Assumptions

- The `agent-framework` 1.4.x release line exposes a documented Foundry chat client whose surface is compatible with the project's current usage of `AzureAIClient` (instructions + tools + streaming). The exact module/class name is verified during the upgrade and pinned in plan/research.
- The Foundry project that Cadence targets supports the Responses protocol and the agent-reference pattern documented at [Microsoft Foundry — Build with agents, conversations, and responses](https://learn.microsoft.com/azure/foundry/agents/concepts/runtime-components) and at [Add client-side tracing to Foundry agents (preview)](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-client-side). The user has confirmed visually that the Traces tab is populated today; that flow continues to work after the upgrade.
- A single orchestrator agent record per deployment is sufficient for portal correlation. Workflow agents (parameter extractor, query builder) are intentionally NOT registered as Foundry agent records — they remain in-process LLM calls and their traces nest under the orchestrator agent's conversation.

## Dependencies

- Upgrade of `agent-framework` from `1.0.0rc4` to `>=1.4.0,<2`, including any peer packages required by the 1.4.x layout for the Foundry chat client.
- `azure-ai-projects` (already a dependency) for the one-time orchestrator agent provisioning helper.
- Continued access to the existing Foundry project endpoint (`AZURE_AI_PROJECT_ENDPOINT`) and orchestrator model deployment.
- Continued connection from the Foundry project to Application Insights (already configured; this feature does not touch the connection).

## Out of Scope

- **Nightly evaluation rewrite** — owned by [008-foundry-native-evaluations](../008-foundry-native-evaluations/spec.md). 008 depends on this feature's FR-004 (`agent_reference` set on responses calls) to enable trace-based evaluation in the Foundry portal.
- Migrating to the **classic Foundry threads/runs model** (`thread_id`/`run_id`/`AIAgentConverter` from `azure.ai.evaluation`). That is the legacy path and is explicitly NOT the direction. The Foundry portal already shows our work using Conversation IDs and Trace IDs — those are the GA primitives.
- Frontend protocol changes (SSE field renames, new endpoints, new transports).
- Introducing distributed session storage to replace the in-memory `DataAssistant` cache.
- Migrating workflow LLM clients (parameter extractor, query builder) to be hosted Foundry agent records. They remain in-process; only the chat-path orchestrator carries the `agent_reference`.
- Removing the existing `conversation_id` plumbing or `service_session_id` field — these reflect the GA continuity model and are kept.
