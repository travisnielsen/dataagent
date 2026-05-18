# Phase 1 — Data Model

**Feature**: Foundry Agent Framework Upgrade & Portal Trace Correlation
**Date**: 2026-05-17 (rescoped)
**Updated**: 2026-05-17 (post-implementation correction — see callout below)

This feature is a framework upgrade plus one new correlation attribute (`agent_reference`). It is not a refactor of the continuity model. No new persisted entities are introduced. Existing `conversation_id` plumbing is **preserved**, not removed.

> **Post-implementation correction (2026-05-17)** — the tables below were written assuming `FoundryChatClient` could carry `agent_reference` at construction. In agent-framework 1.4.x GA the `agent_reference` body field is emitted **only** by `FoundryAgent` (the hosted-agent connector). The actual shipped code therefore uses **two** kinds of agent construction:
>
> | Agent | Class shipped | Portal record |
> |-------|---------------|---------------|
> | Orchestrator (`DataAssistant`) | `FoundryAgent(project_client=..., agent_name="DataAssistant", instructions=load_assistant_prompt())` | Exists |
> | `query-builder-agent` | `FoundryAgent(project_endpoint=..., agent_name="query-builder-agent", instructions=...)` via `create_query_builder_agent(...)` | Exists |
> | `parameter-extractor-agent` | `Agent(client=FoundryChatClient(...), id=..., name=...)` *(unchanged from T011)* | **Missing** — deferred |
>
> Consequence: the type at every call-site that accepts "either kind" is the union `Agent | FoundryAgent` (the two classes are sibling subclasses of `BaseAgent` with no shared public `.run()`-bearing ancestor). The `provision.py` helper described under "New helpers" was **not built** — records are provisioned manually via the Foundry portal today (see [tasks.md Phase 7, T032/T033 follow-ups](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation)). Settings field `AZURE_AI_ORCHESTRATOR_AGENT_NAME` shipped with default `"DataAssistant"` (not `cadence-data-assistant`); `AZURE_AI_ORCHESTRATOR_AGENT_ID` is retained as an optional pin to a specific agent version.
>
> The rest of this document is preserved as-is for the historical record; treat the callout above as authoritative where the two diverge.

## Modified entities

### Chat client (currently `AzureAIClient` from `agent_framework_azure_ai`)

| Aspect | Before | After |
|--------|--------|-------|
| Import | `from agent_framework_azure_ai import AzureAIClient` (5 call sites) | `from agent_framework.foundry import FoundryChatClient` (exact path verified during R1; pinned in plan) |
| Construction | `AzureAIClient(project_endpoint=..., credential=..., model_deployment_name=..., use_latest_version=True)` | `FoundryChatClient(project_endpoint=..., credential=..., model=..., agent_reference={"name": "<configured-name>", "id": "<configured-id>"})` (final keyword names confirmed from installed 1.4.x package) |
| `conversation_id` attribute | settable for response-chaining within the Responses protocol | **preserved** — same role, same semantics; this is the GA continuity primitive |
| `inspect.isawaitable(...)` branch | present at [src/backend/api/routers/chat.py:294-298](src/backend/api/routers/chat.py) (rc4 returned an awaitable from a synchronous-looking method) | **removed** if 1.4.x exposes a consistently async surface (verified during R1) |

**Call sites updated** (all swap the class only; `agent_reference` is set only at the orchestrator site):

- [src/backend/api/routers/chat.py:218](src/backend/api/routers/chat.py) — orchestrator construction; sets `agent_reference`
- [src/backend/assistant/assistant.py](src/backend/assistant/assistant.py) — `DataAssistant` consumes the constructed client
- [src/backend/workflow/clients.py:503,509](src/backend/workflow/clients.py) — `extractor_llm`, `builder_llm` (no `agent_reference`; nest under orchestrator)
- [src/backend/query_builder/agent.py:64](src/backend/query_builder/agent.py) — workflow LLM (no `agent_reference`)
- [src/backend/parameter_extractor/agent.py:64](src/backend/parameter_extractor/agent.py) — workflow LLM (no `agent_reference`)

### `DataAssistant` *(`src/backend/assistant/assistant.py`)*

| Field / accessor | Before | After |
|------------------|--------|-------|
| `__init__(agent, conversation_id)` | unchanged | unchanged |
| `_initial_conversation_id` | private holder for the seed id | unchanged |
| `conversation_id` (property) | reads `service_session_id` off the active `AgentSession`, falls back to the seed | **unchanged** — this is the right shape for the GA Responses protocol |
| Construction of the underlying chat client | uses `AzureAIClient` | uses `FoundryChatClient` (with `agent_reference` set) |

### `Settings` *(`src/backend/config/settings.py`)*

| Field | Before | After |
|-------|--------|-------|
| `AZURE_AI_ORCHESTRATOR_AGENT_ID` | not present | **added**; optional `str | None`. When unset at startup, `provision.py` creates the agent and the resolved id is logged. Operators persist the id back into the deployment env for stability. |
| `AZURE_AI_ORCHESTRATOR_AGENT_NAME` | not present | **added**; optional `str | None`. Defaults to `cadence-data-assistant` (matches the existing agent naming convention). |

### Chat router *(`src/backend/api/routers/chat.py`)*

| Construct | Before | After |
|-----------|--------|-------|
| `AzureAIClient(...)` construction (line 273) | rc4 client, no `agent_reference` | `FoundryChatClient(...)` with `agent_reference={"name": settings.AZURE_AI_ORCHESTRATOR_AGENT_NAME, "id": settings.AZURE_AI_ORCHESTRATOR_AGENT_ID}` |
| `openai_client.conversations.create()` block (lines ~283-308) | pre-creates a provider conversation when none was supplied | **preserved** — this is correct GA behavior (server-managed conversation must exist for `conversation_id` to be echoed on the first SSE `done` event) |
| `ai_client.conversation_id = ...` assignments (lines 298, 312) | sets the provider continuity id | **preserved** — required by the Responses protocol |
| `inspect.isawaitable(...)` branch (line 294) | rc4 workaround | **removed** when R1 confirms 1.4.x is consistently async |
| `from agent_framework_azure_ai import AzureAIClient` (line 218) | rc4 import | replaced with `from agent_framework.foundry import FoundryChatClient` |
| Warning suppression for `AzureAIClient` (referenced from [main.py:42](src/backend/api/main.py)) | present | **removed** |

### `PipelineClients` *(`src/backend/workflow/clients.py`)*

| Field / parameter | Before | After |
|-------------------|--------|-------|
| `extractor_llm` / `builder_llm` type | `AzureAIClient` | `FoundryChatClient` |
| `conversation_id` field on `PipelineClients` | **preserved if currently present** — it is part of the GA continuity model, not rc4 plumbing | unchanged |
| `extractor_llm.conversation_id = conversation_id` (line 517) | sets per-request continuity | **preserved** — workflow LLMs are scoped to the orchestrator's active conversation |

## New helpers (not entities)

### `provision_orchestrator_agent()` *(`src/backend/assistant/provision.py`)*

```python
async def provision_orchestrator_agent(
    settings: Settings,
    credential: AsyncTokenCredential,
) -> str:
    """Idempotent get-or-create for the orchestrator Foundry agent record.

    Returns the resolved agent id. If settings.AZURE_AI_ORCHESTRATOR_AGENT_ID
    is set and resolves, return it unchanged. Otherwise, look up by
    AZURE_AI_ORCHESTRATOR_AGENT_NAME (default cadence-data-assistant);
    if found, return its id; if not, create from assistant_prompt.md
    + AZURE_AI_MODEL_DEPLOYMENT_NAME and return the new id.
    """
```

Implemented against `AIProjectClient` from `azure-ai-projects`. Single async function with explicit return type. Not a Pydantic model.

## Explicitly preserved (callouts)

These existed in the rescoped-away version of this spec as "to be removed" — they are **kept** under the GA Responses model:

| Symbol | File | Why kept |
|--------|------|----------|
| `conversation_id` field on the SSE response | [src/frontend/lib/chatApi.ts](src/frontend/lib/chatApi.ts) | This is the GA primitive (the "Conversation ID" column in the Foundry portal). Renaming offers no value. |
| `service_session_id` accessor on `AgentSession` | `agent-framework` (upstream) | Documented framework API; Cadence reads it through `DataAssistant.conversation_id`. |
| `openai_client.conversations.create()` block | [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py) | Required so the first SSE `done` event can return the `conversation_id` to the browser. |
| `ai_client.conversation_id = ...` assignments | [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py), [src/backend/workflow/clients.py](src/backend/workflow/clients.py) | Required by the Responses protocol. |
| `PipelineClients.conversation_id` field and forwarding | [src/backend/workflow/clients.py](src/backend/workflow/clients.py) | Workflow LLMs operate within the orchestrator's conversation context. |

A successful merge MUST NOT delete any of these. If a reviewer suggests removing them on "cleanup" grounds, point them at this section.

## Relationships

```text
Foundry Project ──contains──> Orchestrator Agent Record (id, name)
                                     ▲
                                     │ agent_reference={name, id}
                                     │
DataAssistant ──holds──> FoundryChatClient ──issues──> responses.create()
                                                          │
                                                          ├──> conversation_id (server-managed; round-tripped on SSE)
                                                          └──> OTel span with gen_ai.agent.{id,name} → portal Traces tab
```

The combination of `(conversation_id, gen_ai.agent.id)` is what feature 008 keys its trace-based evaluation on (`data_source_type: azure_ai_traces`, agent-filter mode). Both are produced "for free" by this feature; no additional persistence or export from Cadence is required.
