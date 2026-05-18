# Phase 0 — Research

**Feature**: Foundry Agent Framework Upgrade & Portal Trace Correlation
**Date**: 2026-05-17 (rescoped after MCP + Microsoft Learn research)
**Updated**:

- 2026-05-19 (T003 verification revised R1, R3 — see first "Discovery" boxes below)
- 2026-05-17 *(post-implementation correction)* — the 2026-05-19 R3 conclusion turned out to be wrong in practice. The portal Traces tab stayed empty even after `Agent(id=, name=)` was wired up; see the **Post-implementation correction** box under R3 for the actual mechanism and the **R7** entry for the resulting deferral.

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

> **Post-implementation correction (2026-05-17)** — the 2026-05-19 discovery was **partially wrong**. After US1 + US2 landed on the feature branch, the portal **Traces** tab for `DataAssistant` loaded cleanly but stayed empty across multiple multi-turn smokes, even though the OTel attributes `gen_ai.agent.id` / `gen_ai.agent.name` were being emitted as expected and the spans were arriving in Application Insights. Root cause: the Foundry portal Traces tab filters by `agent_reference` **on the Responses API request body**, not by OTel span attributes alone. `AgentTelemetryLayer` only enriches client-side spans; it does not modify the outgoing request payload.
>
> The `agent_reference` field is added by `RawFoundryAgentChatClient` (in `agent_framework_foundry/_agent.py`) at the line `extra_body.setdefault("agent_reference", _build_agent_reference(self.agent_name, self.agent_version))`. That code path **only runs when the agent was constructed as a `FoundryAgent`** (the hosted-agent connector class), not when it was constructed as `Agent(client=FoundryChatClient)` (the chat-completions class). The rc4-era `AzureAIClient` implicitly did the hosted-agent binding; agent-framework 1.4.x split that into two distinct client classes — `FoundryChatClient` (no binding) and `FoundryAgent` (sends `agent_reference` on every Responses API call, bound to a hosted **PromptAgent** record in the project).
>
> **Resolution**: replaced `Agent(client=FoundryChatClient(...), id=..., name=...)` with `FoundryAgent(project_endpoint=..., agent_name=..., instructions=...)` for the two agents that have hosted PromptAgent records in the portal (`DataAssistant`, `query-builder-agent`). The `instructions=` kwarg is sent as a per-request system message via `RawFoundryAgentChatClient._prepare_messages_for_azure_ai` (line 276), so the local `prompt.md` files remain the runtime source of truth even when the portal-stored prompt diverges. **Verified**: portal Traces tab populates within ~1 minute of a turn, agent column shows `DataAssistant`, conversation_id round-trips correctly.
>
> See [tasks.md → Phase 7](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation) for the actual commits.

**Rationale**: [Add client-side tracing to Foundry agents (preview)](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-client-side) says explicitly: *"To correlate traces with a specific agent in the Foundry portal, include the `agent_reference` with both `name` and `id` in your `responses.create()` call."* [Register and manage custom agents](https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent) confirms the underlying mechanic: the portal correlates by the OTel attributes `gen_ai.agent.id` (or `gen_ai.agent.name`). A repository grep of `src/backend/**/*.py` for `agent_reference` returns zero matches — Cadence has never set it. This is the single most likely root cause of the user's report that "**Analyze Results** is not working" in the existing `cadence-eval-v1` evaluation, because feature 008's trace-based eval filters traces by `gen_ai.agent.id` and finds no matching spans.

**Alternatives considered**:

- Set `gen_ai.agent.id` directly via the OpenTelemetry SDK on every call site. Rejected: brittle (every new call site must remember to do it); MAF's `FoundryChatClient` is the right abstraction layer for this.
- Skip the orchestrator agent record and rely on the project/deployment name being captured automatically. Rejected: confirmed by the documentation excerpt above that this is insufficient; the portal needs `agent_reference` to populate the agent column.

## R4 — Orchestrator agent provisioning

> **Discovery during T003 (2026-05-19) — R4 SUPERSEDED**: The provisioning helper described below is no longer needed. Portal trace correlation flows through the `Agent(id=, name=)` instance attributes (see R3 update), not through a pre-provisioned Foundry agent record. `AZURE_AI_ORCHESTRATOR_AGENT_ID` is retained as configuration but defaults to the agent name; operators no longer need to round-trip a generated id back into the deployment env. The settings, identity strings, and OTel attribute emission together satisfy SC-003 / SC-006 without any `AIProjectClient.agents.create_version(...)` call.

> **Post-implementation correction (2026-05-17) — R4 PARTIALLY UN-SUPERSEDED**: The R3 post-implementation correction (`FoundryAgent` instead of `Agent`) reintroduces a hard dependency on **hosted PromptAgent records existing in the project**. `FoundryAgent(agent_name="DataAssistant")` resolves the agent by name against the project; if the record is missing the SDK raises a 404. The records `DataAssistant` and `query-builder-agent` were created manually via the Foundry portal during the initial framework rollout and continue to be the canonical references. A programmatic upsert helper (originally R4) would still be useful but is **deferred** to a follow-up:
>
> - Today: operators provision records by hand (or via the Foundry portal MCP `agent_update` tool). The runtime never creates them.
> - Follow-up: the helper sketched below, but using the **Foundry REST API** (`AIProjectClient.send_request` against the `/agents` resource with `kind: "prompt"`) rather than `AIProjectClient.agents.create_version`, which does not exist on `azure-ai-projects==2.1.0`. The Foundry MCP `agent_update` command documents the exact JSON shape; `agent_definition_schema_get` returns the `promptAgentDefinition` schema. See [tasks.md → Phase 7](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation) for the deferred work item.
>
> The implication for FR-005: the requirement is partially satisfied. The runtime does not silently proceed without an `agent_reference` — if the configured record is missing, `FoundryAgent.run()` raises a clear `404 "Resource not found"` on the first turn, surfaced through the SSE error path. Operators get the same actionable signal the original "fail fast" branch promised, just via the SDK's own error rather than a startup check.

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

> **Post-implementation correction (2026-05-17) — partial revision**: After the R3 post-implementation correction, this decision was **split** along the line of "does a hosted PromptAgent record exist for this LLM?":
>
> - `query-builder-agent` **does** have a portal record (created during the initial framework rollout). It was promoted to `FoundryAgent` so its spans now carry `agent_reference` and show up under their own portal Traces page. Useful for cost attribution and prompt-version review.
> - `parameter-extractor-agent` does **not** have a portal record. It stays on `Agent(client=FoundryChatClient(...))` for now — spans still flow to Application Insights with the right `gen_ai.*` attributes, but the portal Traces tab does not have a per-agent view for it. Promoting it requires either (a) operator-managed record creation via the portal/MCP, or (b) the deferred programmatic upsert helper (see R4 post-implementation correction). Tracked under [tasks.md → Phase 7](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation).
>
> The original rationale ("do not register workflow LLMs as agent records") was the right default; the relaxation is opportunistic and reversible. Specifically, the `query-builder-agent` portal record gives the prompt engineer a place to inspect runtime traffic; nothing prevents future contributors from removing the record and reverting to `Agent(client=FoundryChatClient)` if that proves to be noise.

## R6 — Frontend protocol

**Decision**: No frontend changes. The SSE `done` event continues to carry `conversation_id` with the same opaque-string semantics. `useChatApi.ts` and `chatApi.ts` already round-trip it correctly.

**Rationale**: The continuity model is unchanged. The field's value is unchanged in shape (still a server-managed conversation id from the Responses protocol). Renaming or refactoring the field would force coordinated frontend work for zero user-visible benefit.

**Alternatives considered**:

- Rename to a generic `session_id`. Rejected: not in scope; the field already matches the column name in the Foundry portal.

## R7 — `FoundryAgent` vs `Agent(client=FoundryChatClient)` (added 2026-05-17, post-implementation)

**Decision**: For any agent that needs to appear in the Foundry portal **Traces** tab grouped by agent name, construct it as `FoundryAgent(project_endpoint=..., agent_name=..., instructions=...)` and ensure a hosted PromptAgent record with that name exists in the project. For purely-internal LLM calls that only need to show up in Application Insights, the simpler `Agent(client=FoundryChatClient(...))` form is sufficient and avoids the portal-record coupling.

**Rationale**: The portal Traces tab filters by `agent_reference` on the Responses API request body. `agent_reference` is only emitted on outgoing requests by `RawFoundryAgentChatClient`, which is the chat-client backing `FoundryAgent`. `FoundryChatClient` (the standalone chat-completions client) does not emit it \u2014 OTel attribute enrichment via `AgentTelemetryLayer` is client-side only and does not reach the portal's server-side correlation index.

**Type-system consequence**: `FoundryAgent` and `Agent` are sibling subclasses of `BaseAgent`; they do **not** share a public `.run()`-bearing ancestor (the relevant intermediate `RawAgent` is private). Any code path that accepts \"either kind of agent\" must use a union: `Agent | FoundryAgent`. This affects `DataAssistant.__init__`, `PipelineClients.query_builder_agent`, `build_query(agent=...)`, and `_build_agent_session(agent=...)`. The widening is acceptable \u2014 both sides expose the same async `.run(...)` shape \u2014 but it is the reason the upgrade diff includes type annotations sprinkled across four files rather than one.

**Instructions handling**: `FoundryAgent(instructions=\"...\")` does **not** push the instructions to the hosted PromptAgent record. Instead the SDK sends them as a per-request `instructions` field on each Responses API call (see `RawFoundryAgentChatClient._prepare_messages_for_azure_ai`, lines 276\u2013289 of `agent_framework_foundry/_agent.py`). The portal-stored prompt is **not** the source of truth at runtime \u2014 `src/backend/assistant/assistant_prompt.md` and `src/backend/query_builder/prompt.md` are. The portal copy exists for human review and for the (future) trace-replay / evaluation tooling under feature 008. Operators editing prompts should change the markdown files in the repo; portal edits are decorative until/unless a sync helper is built.

**Alternatives considered**:

- Keep everything on `Agent(client=FoundryChatClient)` and find another way to populate `agent_reference`. Rejected: the field is set inside the SDK's private chat-client method; there is no public override hook short of subclassing internal classes.\n- Promote `parameter-extractor-agent` immediately too. Deferred: no portal record exists; creating one programmatically requires either the Foundry MCP (blocked from the local network in dev) or a yet-to-be-written REST-based upsert. Lower priority than US1/US2 which are now both verified working.
