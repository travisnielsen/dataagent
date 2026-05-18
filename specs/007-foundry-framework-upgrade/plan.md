# Implementation Plan: Foundry Agent Framework Upgrade & Portal Trace Correlation

**Branch**: `007-foundry-framework-upgrade` | **Date**: 2026-05-17 (rescoped) | **Spec**: [spec.md](spec.md)
**Updated**: 2026-05-17 (post-implementation correction — see callout below)
**Input**: Feature specification from `/specs/007-foundry-framework-upgrade/spec.md`

> **Post-implementation correction (2026-05-17)** — sections "Project Structure" and "Tasks slice" below were drafted assuming a `provision.py` helper would create the orchestrator agent record at startup and that `FoundryChatClient(agent_reference=...)` would carry the portal correlation. Neither matched the actual agent-framework 1.4.x GA surface. The shipped implementation:
>
> - Uses `FoundryAgent` (the hosted-agent connector) for `DataAssistant` and `query-builder-agent`. The SDK sends `agent_reference` on the Responses API request body automatically when invoked via `FoundryAgent.run(...)`.
> - Keeps `parameter-extractor-agent` on `Agent(client=FoundryChatClient(...))` because no portal PromptAgent record exists for it yet (deferred — see [tasks.md T032/T033](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation)).
> - **Does not** create `src/backend/assistant/provision.py`. Hosted PromptAgent records are provisioned out-of-band via the Foundry portal.
> - **Does not** create the three new unit-test modules listed below; existing tests in [tests/unit/test_sse_endpoint.py](tests/unit/test_sse_endpoint.py) were updated to patch `FoundryAgent` + `AIProjectClient` instead.
> - Settings field `AZURE_AI_ORCHESTRATOR_AGENT_NAME` shipped with default `"DataAssistant"` (not `cadence-data-assistant`).
>
> See [research.md R3/R4/R7 post-implementation correction boxes](research.md), [tasks.md Phase 7](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation), and [data-model.md top callout](data-model.md) for the full picture. The rest of this plan is preserved for the historical record.

## Summary

The Cadence chat runtime is already on the GA-recommended Foundry path: it uses the **Responses protocol** with a server-managed **`conversation_id`** (the same identifier the Foundry portal Traces tab shows in its "Conversation ID" column). Multi-turn continuity works; portal tracing works. What does not work yet:

1. The framework version is `agent-framework==1.0.0rc4` — a release candidate. Cadence depends on `agent_framework_azure_ai.AzureAIClient`, an internal-package class from the rc layout.
2. Responses calls do not include `agent_reference` (name + id). Microsoft Learn documents this as required for the Foundry portal to correlate traces with the agent record. A repository grep confirms zero usages of `agent_reference` in `src/backend/`.

This plan addresses both: upgrade `agent-framework` to `>=1.4.0,<2`, move to the documented public chat-client symbol (target: `from agent_framework.foundry import FoundryChatClient` — exact path verified during R1), and attach `agent_reference` on every responses call. The continuity model, the SSE protocol, and the frontend are unchanged.

This feature does **not** migrate to the classic Foundry threads/runs model (`AzureAIAgentClient` / `thread_id` / `AIAgentConverter`). Research established that those are legacy artifacts from `azure.ai.agents<1.0.0b10` and Foundry-classic; the portal screenshots the user provided show the new portal, which is conversations + traces all the way down.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript 5.x / Next.js 14 (frontend — no changes in scope)
**Primary Dependencies**: Microsoft Agent Framework `>=1.4.0,<2` (`agent-framework` + its Foundry sub-package per the resolved 1.4.x layout), `azure-ai-projects` (one-time agent provisioning + project resource access), `azure-identity`, FastAPI, Pydantic, `pytest` / `pytest-asyncio`
**Storage**: No new persistent storage. The orchestrator agent record lives in Foundry (created via `azure-ai-projects`) and is referenced by id; the in-memory `_assistant_cache` in [src/backend/api/session_manager.py](src/backend/api/session_manager.py) continues to key `DataAssistant` instances by `conversation_id`.
**Testing**: `pytest` (`tests/unit/`, `tests/integration/`) via `uv run poe test`. Unit tests mock the new chat-client class. Integration tests reuse existing recorded fixtures under `tests/fixtures/`.
**Target Platform**: Azure Container Apps (backend), Static Web Apps / Container Apps (frontend).
**Project Type**: Web service + companion frontend (single repo, two deployables).
**Performance Goals**: Latency unchanged vs. pre-upgrade baseline (±10% on p95).
**Constraints**: `uv run poe check` (lint + typecheck + tests) must pass. Zero new persistent storage. No frontend protocol change. No regressions in existing SSE step events or scenario routing telemetry. Foundry portal Traces tab MUST continue to show rows after the upgrade.
**Scale/Scope**: ~10 backend files touched (chat router, workflow/clients, query_builder agent, parameter_extractor agent, dependencies, session_manager, assistant, settings, main, tests). `pyproject.toml` dep bump. 0 frontend files touched. 0 evaluation files touched (deferred to 008). One small new helper: `src/backend/assistant/provision.py` for one-time orchestrator agent creation.

## Constitution Check

Evaluated against [constitution v2.0.1](.specify/memory/constitution.md):

| Principle | Compliance | Notes |
|-----------|-----------|-------|
| I. Async-First | ✅ | All chat-path code stays `async`. New `FoundryChatClient` surface is async. No blocking calls introduced. |
| II. Validated Data at Boundaries | ✅ | SSE payload shapes unchanged. No raw dicts introduced. |
| III. Fully Typed | ✅ | Every changed function carries explicit param + return types. `basedpyright` standard mode must pass. |
| IV. Single-Responsibility Executors | ✅ | `DataAssistant` keeps its single orchestrator role. New `provision.py` has one function. |
| V. Automated Quality Gates | ✅ | `uv run poe check` is the gate. |

**Gate result**: PASS. No deviations require justification.

## Project Structure

### Documentation (this feature)

```text
specs/007-foundry-framework-upgrade/
├── plan.md                                # this file
├── spec.md                                # rescoped: framework upgrade + portal correlation
├── research.md                            # Phase 0 — upgrade decisions + portal correlation evidence
├── data-model.md                          # Phase 1 — modified backend entities
├── quickstart.md                          # Phase 1 — repro + verification steps
├── contracts/
│   └── chat-sse-stream.md                 # backend↔frontend SSE event shape (unchanged)
├── checklists/
│   └── requirements.md                    # spec-quality checklist
└── tasks.md                               # produced by /speckit.tasks (later)
```

### Source Code (repository root)

```text
src/
├── backend/
│   ├── api/
│   │   ├── routers/chat.py                # swap AzureAIClient → FoundryChatClient; drop inspect.isawaitable workaround
│   │   ├── dependencies.py                # update comments referring to AzureAIClient; expose project client where needed
│   │   ├── session_manager.py             # no logic change (key remains conversation_id)
│   │   └── main.py                        # drop the rc4-specific AzureAIClient warning suppression
│   ├── assistant/
│   │   ├── assistant.py                   # construct chat client with agent_reference={name, id}
│   │   └── provision.py                   # NEW (small) — async helper to ensure orchestrator agent record exists; returns its id
│   ├── workflow/
│   │   └── clients.py                     # swap AzureAIClient → FoundryChatClient for extractor_llm and builder_llm
│   ├── query_builder/
│   │   └── agent.py                       # swap client class
│   ├── parameter_extractor/
│   │   └── agent.py                       # swap client class
│   ├── nl2sql_controller/
│   │   └── pipeline.py                    # no logic change; keep conversation_id flow
│   └── config/
│       └── settings.py                    # add AZURE_AI_ORCHESTRATOR_AGENT_ID setting (optional; provisioning fills it)
└── frontend/                              # NO CHANGES

tests/
└── unit/
    └── backend/
        ├── test_chat_router_upgrade.py            # NEW — agent_reference is set; conversation_id round-trips
        ├── test_assistant_agent_reference.py      # NEW — DataAssistant attaches the configured agent reference
        └── test_provision_orchestrator_agent.py   # NEW — idempotent get-or-create

pyproject.toml                             # bump agent-framework to >=1.4.0,<2; add the Foundry sub-package per resolved layout
```

**Structure Decision**: Existing repo layout is unchanged. ~10 backend files edited, 1 small new helper, 1 new settings field, 3 new unit test modules. No new top-level directories. Evaluations subpackage untouched (owned by 008).

## Complexity Tracking

No constitutional violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| _(none)_  | _(n/a)_    | _(n/a)_                              |

## Phase 0 — Outline & Research

See [research.md](research.md). Open questions resolved before planning continues:

1. The exact 1.4.x import path for the documented Foundry chat-client symbol (the public `FoundryChatClient`, target module `agent_framework.foundry`) — verified during the upgrade by inspecting the installed package metadata.
2. Whether `agent_reference` is the only attribute required for portal trace→agent correlation, or whether additional `gen_ai.*` OTel attributes must also be set — resolved from Microsoft Learn citations in research.
3. Whether the workflow LLM clients (extractor / builder) need their own agent records for trace correlation, or whether nesting them under the orchestrator conversation is sufficient — resolved: single orchestrator agent record is the documented pattern; workflow calls nest under that conversation.

## Phase 1 — Design & Contracts

See:

- [data-model.md](data-model.md) — modified entities (chat-client construction sites, `DataAssistant`, new `Settings.AZURE_AI_ORCHESTRATOR_AGENT_ID`, new `provision.py`); explicitly preserved fields (`conversation_id` and its plumbing).
- [contracts/chat-sse-stream.md](contracts/chat-sse-stream.md) — confirms the SSE `done` event's `conversation_id` field is unchanged in shape and meaning.
- [quickstart.md](quickstart.md) — local repro (multi-turn chat works, agent column populates in portal Traces tab), rollback path.

**Re-evaluation after Phase 1**: PASS — design introduces no new principles violations; all new code is `async`, single-concern.

## Next steps

`/speckit.tasks` to break this plan into the ordered checklist in `tasks.md`. Suggested task slices:

- T001–T005: `pyproject.toml` bump + `uv lock` + import inventory (record where `FoundryChatClient` actually lives in the resolved 1.4.x layout).
- T010: Add `AZURE_AI_ORCHESTRATOR_AGENT_ID` to `Settings` and `.env.example`.
- T011: Implement `provision.py` (idempotent get-or-create against `azure-ai-projects`).
- T020–T025: Swap chat client class in chat.py, assistant.py, workflow/clients.py, query_builder/agent.py, parameter_extractor/agent.py; attach `agent_reference` at construction.
- T030: Drop rc4 workarounds (`inspect.isawaitable` branch, warning suppression).
- T040: Unit tests (3 new modules) + run `uv run poe check`.
- T050: Manual end-to-end smoke — drive 3 turns, verify in the Foundry portal Traces tab that the agent column populates and the **Conversation ID** matches what the SSE stream returned. This satisfies SC-003 and is the gate that 008 needs.
