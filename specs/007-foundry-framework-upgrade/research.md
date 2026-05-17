# Phase 0 — Research

**Feature**: Foundry Agent Framework Upgrade & Portal Trace Correlation
**Date**: 2026-05-17 (rescoped after MCP + Microsoft Learn research)
**Updated**: 2026-05-19 (T003 verification revised R1, R3 — see "Discoveries" boxes below)

## R1 — `agent-framework` 1.4.x package layout and chat-client symbol

**Decision**: Pin `agent-framework>=1.4.0,<2`. Import the Foundry chat client from its documented public path. The 1.4.x line's documented symbol is `FoundryChatClient` in the `agent_framework.foundry` namespace ([Microsoft Agent Framework — agent types](https://learn.microsoft.com/agent-framework/agents/), [Get Started — Multi-Turn Conversations](https://learn.microsoft.com/agent-framework/get-started/multi-turn)). The exact installed path is verified after `uv lock` by inspecting `.venv/lib/python3.11/site-packages/agent_framework*/__init__.py`; if a separate distribution (e.g., `agent-framework-foundry`) is required to make that import resolve, it is added to `pyproject.toml` at the same time.

> **Discovery during T003 (2026-05-19)**: Verified — `from agent_framework.foundry import FoundryChatClient` resolves cleanly after `uv lock`. The package `agent-framework-foundry==1.4.0` (transitive via `agent-framework`) provides the implementation. The rc4-era `agent-framework-azure-ai` distribution is **gone** in 1.4.x. `azure-ai-projects` is auto-pulled to `2.1.0` as a transitive dep (formerly was `2.0.1` under rc4). Constructor kwargs renamed in the GA surface: `model_deployment_name` → `model`, `use_latest_version=True` removed (preview gating is now `allow_preview: bool | None`). The client no longer exposes a writable `conversation_id` attribute — the rc4 pattern `ai_client.conversation_id = X` is invalid. `conversation_id` lives on `FoundryChatOptions` (a `TypedDict`) and propagates from `AgentSession.service_session_id` automatically via the framework.

**Rationale**: Today Cadence imports `from agent_framework_azure_ai import AzureAIClient` (rc4 internal package, `_client.py` line 1216). The 1.4.x line consolidates the documented surface under `agent_framework.foundry.FoundryChatClient`. Moving to the documented import path removes a class of forward-compatibility risk and aligns with sample code on Microsoft Learn.

**Alternatives considered**:

- Stay on `1.0.0rc4`. Rejected: it is a release candidate, blocks security/patch uptake, and binds Cadence to internal-package symbols.
- Pin an exact version (e.g., `==1.4.0`). Rejected: removes patch uptake; range is safer.
- Skip the framework and call `azure-ai-projects` directly. Rejected: re-implements the tool-dispatch / streaming loop that MAF already maintains, and contradicts the spec's "no new custom plumbing" goal.

**Evidence**:

- Installed rc4 layout confirmed by `ls .venv/lib/python3.11/site-packages/ | grep agent_framework` — Cadence's current import resolves to `agent_framework_azure_ai/_client.py` (an internal underscore-prefixed module).
- Repository grep confirms five usages of `from agent_framework_azure_ai import AzureAIClient` across `src/backend/api/routers/chat.py`, `src/backend/workflow/clients.py`, `src/backend/query_builder/agent.py`, `src/backend/parameter_extractor/agent.py`, plus a docstring reference in `src/backend/api/dependencies.py`.

## R2 — Multi-turn continuity (already correct; preserved as-is)

**Decision**: Keep the existing continuity model. Cadence already uses the Foundry **Responses protocol** with a server-managed `conversation_id`. The chat router pre-creates a provider conversation via `openai_client.conversations.create()` when the client doesn't supply one, then sets `ai_client.conversation_id` so subsequent turns reuse it. `AgentSession.service_session_id` exposes that id back through `DataAssistant.conversation_id` to the SSE stream. **This is the GA-recommended pattern** and matches the "Conversation ID" column the user sees in the Foundry portal Traces tab today. No changes are required here other than swapping the client class.

**Rationale**: Microsoft Learn's [Build with agents, conversations, and responses](https://learn.microsoft.com/azure/foundry/agents/concepts/runtime-components) explicitly documents conversations as the GA primitive: *"A Conversation is the persistent context of an end-to-end dialogue history between a user and an agent."* The portal's documented behavior ([Set up tracing in Microsoft Foundry — View conversation results](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-setup#view-and-analyze-traces)) keys on `Conversation ID` and `Trace ID`. There is no thread/run model in the new Foundry surface; the `thread_id`/`run_id`/`AIAgentConverter` symbols belong to `azure.ai.agents<1.0.0b10` and the Foundry-classic SDK and are not the direction.

**Alternatives considered**:

- Migrate to `AzureAIAgentClient` (the classic threads-based client also present in `agent_framework_azure_ai`). Rejected: that is the legacy `azure.ai.agents` path; the Foundry portal columns the user cares about are not populated from `thread_id`.
- Stop pre-creating the conversation and rely on the framework to start one implicitly. Rejected: the explicit pre-create gives Cadence the `conversation_id` value it needs to return on the *first* SSE `done` event (the client cannot send what it does not know on turn 1). This is a deliberate, working pattern, not a workaround.

## R3 — Portal trace → agent correlation via `agent_reference`

**Decision**: Every responses call made by the orchestrator chat path must carry an agent reference (name + id) identifying the orchestrator agent record. In the 1.4.x `FoundryChatClient` surface, this is configured at client construction (or per `responses.create()` call) so it propagates into the OpenTelemetry GenAI span attributes (`gen_ai.agent.name`, `gen_ai.agent.id`) that the Foundry portal reads.

> **Discovery during T003 (2026-05-19)**: The mechanism is correct (OTel attributes `gen_ai.agent.id` / `gen_ai.agent.name` flow into the portal) but the API surface is not what we assumed. There is **no** `agent_reference={"id": ..., "name": ...}` constructor kwarg on `FoundryChatClient` in 1.4.x. Inspecting the GA source (`agent_framework_openai/_chat_client.py` and `agent_framework_foundry/_chat_client.py`) shows that the OTel attributes are emitted by `AgentTelemetryLayer` (which sits in the MRO of `agent_framework.Agent`) by reading the `Agent`'s `id` and `name` attributes — the values supplied to `Agent(client=..., id=..., name=..., instructions=...)`. The correlation therefore flows through the **`Agent` instance**, not through the chat-client construction kwargs. Equivalent options would be passing `default_options={"conversation_id": ...}` to `as_agent(...)` or setting `agent_metadata` on a per-call basis, but the documented and simplest path is the `Agent(id=, name=)` constructor kwargs. As a side effect, the spec's FR-005 "EITHER provision a Foundry agent record OR fail fast" requirement is over-specified: no Foundry agent record needs to be pre-provisioned via `AIProjectClient.agents` for portal trace correlation. R4 (provisioning helper) is therefore **superseded** — see R4 box below.

**Rationale**: [Add client-side tracing to Foundry agents (preview)](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-client-side) says explicitly: *"To correlate traces with a specific agent in the Foundry portal, include the `agent_reference` with both `name` and `id` in your `responses.create()` call."* [Register and manage custom agents](https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent) confirms the underlying mechanic: the portal correlates by the OTel attributes `gen_ai.agent.id` (or `gen_ai.agent.name`). A repository grep of `src/backend/**/*.py` for `agent_reference` returns zero matches — Cadence has never set it. This is the single most likely root cause of the user's report that "**Analyze Results** is not working" in the existing `cadence-eval-v1` evaluation, because feature 008's trace-based eval filters traces by `gen_ai.agent.id` and finds no matching spans.

**Alternatives considered**:

- Set `gen_ai.agent.id` directly via the OpenTelemetry SDK on every call site. Rejected: brittle (every new call site must remember to do it); MAF's `FoundryChatClient` is the right abstraction layer for this.
- Skip the orchestrator agent record and rely on the project/deployment name being captured automatically. Rejected: confirmed by the documentation excerpt above that this is insufficient; the portal needs `agent_reference` to populate the agent column.

## R4 — Orchestrator agent provisioning

> **Discovery during T003 (2026-05-19) — R4 SUPERSEDED**: The provisioning helper described below is no longer needed. Portal trace correlation flows through the `Agent(id=, name=)` instance attributes (see R3 update), not through a pre-provisioned Foundry agent record. `AZURE_AI_ORCHESTRATOR_AGENT_ID` is retained as configuration but defaults to the agent name; operators no longer need to round-trip a generated id back into the deployment env. The settings, identity strings, and OTel attribute emission together satisfy SC-003 / SC-006 without any `AIProjectClient.agents.create_version(...)` call.

**Decision (original, retained for history)**: Provide a single `provision_orchestrator_agent()` async helper in `src/backend/assistant/provision.py` that uses `AIProjectClient.agents` to:

1. If `AZURE_AI_ORCHESTRATOR_AGENT_ID` is configured and resolves, return that id.
2. Otherwise create (or get-or-create by name) an agent record from `src/backend/assistant/assistant_prompt.md` + `AZURE_AI_MODEL_DEPLOYMENT_NAME`, then return the new id.

Run it at startup. Log the resolved id. Operators are expected to persist the id back into the deployment's environment for stability (avoids accidental re-creation when the helper's name-matching logic ever changes).

**Rationale**: Agent records are deployment-scoped; per-request creation is an anti-pattern. A small idempotent boot-time step keeps the runtime stateless and avoids drift between Cadence's `assistant_prompt.md` and the Foundry agent record.

**Alternatives considered**:

- Hard-require operators to provision agents out-of-band before deploying. Rejected: high friction for local dev and PR previews; the helper is < 50 lines and Microsoft samples do this routinely.
- Provision an agent per `DataAssistant` instance. Rejected: produces a noisy Foundry catalog of one-off agent records, defeats portal aggregation.

## R5 — Workflow LLMs (parameter extractor, query builder)

**Decision**: Swap their client class (`AzureAIClient` → `FoundryChatClient`) for consistency with the orchestrator, but **do not** register them as Foundry agent records. They remain in-process LLM calls. Their traces continue to nest under the orchestrator agent's conversation, because the Foundry tracing integration in MAF preserves the active OTel span context across in-process calls.

**Rationale**: Microsoft Learn's guidance on portal correlation talks about agent records as user-facing entities (visible in the Foundry portal Agents list). The workflow LLMs are implementation details; surfacing each as a separate agent record would pollute the portal and offer no additional correlation value — the orchestrator conversation already groups every call made during a turn.

**Alternatives considered**:

- Register every workflow agent as a Foundry agent record. Rejected as above.
- Leave the workflow LLMs on the rc4 `AzureAIClient` to minimize the diff. Rejected: violates FR-002 (zero `agent_framework_azure_ai` imports after the upgrade) and would force keeping the rc4 dependency live.

## R6 — Frontend protocol

**Decision**: No frontend changes. The SSE `done` event continues to carry `conversation_id` with the same opaque-string semantics. `useChatApi.ts` and `chatApi.ts` already round-trip it correctly.

**Rationale**: The continuity model is unchanged. The field's value is unchanged in shape (still a server-managed conversation id from the Responses protocol). Renaming or refactoring the field would force coordinated frontend work for zero user-visible benefit.

**Alternatives considered**:

- Rename to a generic `session_id`. Rejected: not in scope; the field already matches the column name in the Foundry portal.
