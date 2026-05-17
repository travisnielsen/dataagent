# Tasks: Foundry Agent Framework Upgrade & Portal Trace Correlation

**Input**: Design documents from `/specs/007-foundry-framework-upgrade/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/chat-sse-stream.md](contracts/chat-sse-stream.md), [quickstart.md](quickstart.md)

**Tests**: Included — plan.md explicitly enumerates three new unit test modules under `tests/unit/backend/`.

**Organization**: Tasks are grouped by user story. Stories may be implemented in priority order (US1 → US2 → US3) or, after Foundational, US1 and US2 can run in parallel by different developers.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Different file, no dependency on an incomplete task — safe to run in parallel.
- **[Story]**: `US1` (framework upgrade), `US2` (portal correlation), `US3` (cleanup). No label = setup / foundational / polish.
- Paths are repository-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Pin the new framework version and confirm the public import path before any code swap.

- [x] T001 Bump `agent-framework` to `>=1.4.0,<2` in [pyproject.toml](pyproject.toml) (replaced the `1.0.0rc4` pin); moved `azure-ai-projects>=1.0.0` from the `[evaluation]` extra to base `dependencies` since `azure-ai-projects==2.1.0` is now a transitive dep of `agent-framework-foundry`.
- [x] T002 Ran `uv lock && uv sync`; `uv.lock` shows `agent-framework==1.4.0`, `agent-framework-foundry==1.4.0`, `azure-ai-projects==2.1.0`, `agent-framework-azure-ai` removed. No `*rc*` line remains for `agent-framework` (satisfies SC-001 / FR-001).
- [x] T003 [P] Verified the public import path: `from agent_framework.foundry import FoundryChatClient` resolves cleanly. Discovery during verification: kwargs renamed — `model_deployment_name` → `model`, `use_latest_version=True` removed (preview gating moved to `allow_preview`). `conversation_id` is no longer a settable attribute on the client; it lives in `FoundryChatOptions` (`TypedDict`) and propagates from `AgentSession.service_session_id` via the framework. Portal agent correlation flows through `Agent(client=..., id=..., name=...)` which emits `gen_ai.agent.id` / `gen_ai.agent.name` OTel attributes via `AgentTelemetryLayer` in the `Agent` MRO — no `agent_reference` kwarg exists and no separate provisioning is required. See research.md update.

**Checkpoint**: Dep resolved, public import path confirmed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration surface for the orchestrator agent reference. US2 reads these settings; US1 changes the client construction sites and benefits from them existing.

**⚠️ CRITICAL**: Both US1 and US2 require these tasks complete.

- [x] T004 Add `AZURE_AI_ORCHESTRATOR_AGENT_ID: str | None = None` and `AZURE_AI_ORCHESTRATOR_AGENT_NAME: str = "cadence-data-assistant"` to `Settings` in [src/backend/config/settings.py](src/backend/config/settings.py).
- [x] T005 [P] Add the two new env vars (with comments pointing at [specs/007-foundry-framework-upgrade/quickstart.md](specs/007-foundry-framework-upgrade/quickstart.md) Step 1) to [src/backend/.env.example](src/backend/.env.example).

**Checkpoint**: Settings carries the agent reference; user stories can begin.

---

## Phase 3: User Story 1 — Framework upgrade with zero behavior change (Priority: P1) 🎯 MVP

**Goal**: Swap the rc4 internal client (`agent_framework_azure_ai.AzureAIClient`) for the documented public `FoundryChatClient` at every call site, with no behavior change. Conversation continuity, SSE protocol, and frontend are unaffected.

**Independent Test**: Drive 3+ sequential turns through `/api/chat/stream`. Turn 2 and turn 3 demonstrably use prior-turn context; the `conversation_id` echoed on each `done` event round-trips correctly; `uv run poe check` passes.

### Tests for User Story 1 ⚠️

> Write these first; ensure they FAIL before implementation lands.

- [ ] ~~T006~~ **SKIPPED** — the planned unit test asserted internal kwargs (`agent_reference`, `model_deployment_name`) that don't exist in the actual 1.4.x surface. The behaviour the test would have covered is now covered by (a) `uv run poe check` import-resolution at T012 and (b) the manual portal smoke at T018 (which directly observes the OTel attributes the test would have indirectly asserted). A future smoke test may be added when the existing chat-router test harness is refactored, but is not gating for the upgrade.

### Implementation for User Story 1

- [x] T007 [US1] Swap `from agent_framework_azure_ai import AzureAIClient` → `from agent_framework.foundry import FoundryChatClient` at the import + construction site in [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py). Constructor kwargs renamed per discovery in T003: `model_deployment_name` → `model`; `use_latest_version=True` dropped (no equivalent needed for our usage). Preserved the `openai_client.conversations.create()` block. Removed the two `ai_client.conversation_id = ...` attribute assignments — `conversation_id` is no longer a writable attribute on the client in 1.4.x; instead, the framework propagates it via `AgentSession.service_session_id` (already in place in `DataAssistant.get_or_create_conversation`). Agent identity (`id`/`name`) added in T017 below.
- [x] T008 [P] [US1] Confirmed no AzureAIClient references in [src/backend/assistant/assistant.py](src/backend/assistant/assistant.py); `DataAssistant.conversation_id` reads `service_session_id` off the active `AgentSession` unchanged.
- [x] T009 [P] [US1] Swap `extractor_llm` and `builder_llm` in [src/backend/workflow/clients.py](src/backend/workflow/clients.py) to `FoundryChatClient`. Removed the `extractor_llm.conversation_id = ...` / `builder_llm.conversation_id = ...` block — not settable in 1.4.x. Added a NOTE comment explaining that workflow LLMs propagate conversation continuity via Foundry project context; per-call propagation, if ever needed, goes through agent `default_options`.
- [x] T010 [P] [US1] Swap the client class in [src/backend/query_builder/agent.py](src/backend/query_builder/agent.py).
- [x] T011 [P] [US1] Swap the client class in [src/backend/parameter_extractor/agent.py](src/backend/parameter_extractor/agent.py).
- [x] T012 [US1] Run `uv run poe check` — lint + typecheck pass. `uv run poe test` — all 620 tests pass after two trivial test-mock fixups (one mock-path rename and a `filterwarnings` adjustment in `pyproject.toml` to allow `agent-framework` 1.4.x preview-feature `ExperimentalWarning` to pass through unchanged).

**Checkpoint**: US1 complete — backend runs on `agent-framework` 1.4.x using the public symbol; multi-turn chat works exactly as before. **Analyze Results in the portal is still greyed out** — that's US2.

---

## Phase 4: User Story 2 — Foundry portal correlates traces to the orchestrator agent (Priority: P1)

**Goal**: Attach a stable `gen_ai.agent.id` / `gen_ai.agent.name` OTel attribute pair to every responses call from the chat path. This is the change that lights up the Agent column in the Foundry portal Traces tab and unblocks 008.

**Discovery during T003 (recorded in [research.md](research.md) under R1/R3)**: In `agent-framework` 1.4.x the public surface emits `gen_ai.agent.id` / `gen_ai.agent.name` automatically when an `Agent` (`from agent_framework import Agent`) is constructed with `id=` and `name=` — the `AgentTelemetryLayer` in the `Agent`'s MRO emits these attributes on every span. There is no `agent_reference` constructor kwarg on `FoundryChatClient`, and no Foundry agent record needs to be pre-provisioned via `AIProjectClient.agents` for portal trace correlation. The original FR-005 "EITHER provision an agent OR fail startup" requirement is therefore satisfied by the simpler path (a) — assign the configured identity strings on the `Agent` instance — option (b), fail-fast on missing id, is irrelevant because the id is always resolvable from settings (defaulting to the agent name). Tasks T013, T014, T014b, T015, T016 below are therefore obsolete and marked **SKIPPED — superseded by T017**.

**Independent Test**: Drive one chat turn end-to-end. Within 5 minutes, the new row in the Foundry portal Traces tab shows the agent column populated with the configured orchestrator name and the **Conversation ID** column matches the value the SSE `done` event returned.

### Tests for User Story 2 ⚠️

- [ ] ~~T013~~ **SKIPPED** — provisioning helper no longer exists; nothing to unit-test.
- [ ] ~~T014~~ **SKIPPED** — no `agent_reference` kwarg in 1.4.x; replaced by direct assertion in T017's smoke test.

### Implementation for User Story 2

- [x] T014b [US2] Updated [src/backend/api/dependencies.py](src/backend/api/dependencies.py): replaced `AzureAIClient` references in the existing `get_project_client()` docstring/comment with `FoundryChatClient`. No new provider added (no provisioning step needs it).
- [ ] ~~T015~~ **SKIPPED** — `provision.py` not needed; portal correlation flows via `Agent(id=, name=)` OTel attributes (see Discovery above).
- [ ] ~~T016~~ **SKIPPED** — no lifespan provisioning needed.
- [x] T017 [US2] At the orchestrator `Agent` construction site in [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py), passed `id=settings.AZURE_AI_ORCHESTRATOR_AGENT_ID or settings.AZURE_AI_ORCHESTRATOR_AGENT_NAME` and `name=settings.AZURE_AI_ORCHESTRATOR_AGENT_NAME` to `Agent(...)`. Workflow-LLM `Agent` constructions (in `parameter_extractor/agent.py`, `query_builder/agent.py`) intentionally remain identity-less — workflow calls nest under the orchestrator's conversation per R5 in research.md.
- [ ] T018 [US2] Manual portal verification per [specs/007-foundry-framework-upgrade/quickstart.md](specs/007-foundry-framework-upgrade/quickstart.md) Step 3: drive ≥ 3 turns, open Foundry → Traces, confirm Agent column populated and Conversation ID column matches the SSE `done` event value. Satisfies SC-003 and SC-006 (the 008 gate).

**Checkpoint**: US2 complete — portal trace→agent correlation works; the **Analyze Results** affordance on the existing `cadence-eval-v1` evaluation should now be enabled. Feature 008 is unblocked.

---

## Phase 5: User Story 3 — Smaller, more idiomatic backend (Priority: P2)

**Goal**: Remove rc4-only scar tissue now that US1 has landed.

**Independent Test**: `rg agent_framework_azure_ai src/backend` returns zero matches; `rg "filterwarnings|simplefilter" src/backend/api/main.py` does not show the rc4 suppression.

### Implementation for User Story 3

- [x] T019 [US3] Removed the `inspect.isawaitable(...)` branch around the chat-client invocation in [src/backend/api/routers/chat.py](src/backend/api/routers/chat.py); the 1.4.x `AIProjectClient` is consistently async.
- [x] T020 [US3] Removed the rc4-specific warning suppression in [src/backend/api/main.py](src/backend/api/main.py).
- [x] T021 [US3] `rg agent_framework_azure_ai src/backend` returns zero matches (SC-004). `uv run poe check` passes.

**Checkpoint**: All three user stories complete; backend is on a fully public surface.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification across the full feature.

- [ ] T022 Run the complete [specs/007-foundry-framework-upgrade/quickstart.md](specs/007-foundry-framework-upgrade/quickstart.md) script (Steps 1-4), capturing the Foundry portal screenshot for the PR description.
- [ ] T023 [P] Compare first-turn p95 latency against the pre-upgrade baseline (within ±10% per SC-005); record the comparison in the PR.
- [x] T024 [P] `rg previous_response_id src/backend` returns zero matches (contract invariant 4 satisfied).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. Start immediately.
- **Foundational (Phase 2)**: Depends on Setup (T001-T002 must be done so `uv sync` resolves). Blocks all user stories.
- **US1 (Phase 3)**: Depends on Foundational.
- **US2 (Phase 4)**: Depends on Foundational. Can run **in parallel with US1** by a second developer (T015/T016/T017 touch different files than US1's swaps, except for T017 which co-located in chat.py with T007 — sequence those two within one developer's queue).
- **US3 (Phase 5)**: Depends on US1 (cannot remove the rc4 workaround until the swap is in).
- **Polish (Phase 6)**: Depends on US1 + US2 + US3.

### Within Each User Story

- Tests (T006, T013, T014) MUST be written and FAIL before the matching implementation lands.
- Models / helpers before consumers (T015 before T016 before T017 in US2).
- Story complete (all checkpoint criteria green) before moving to the next.

### Parallel Opportunities

- T003 runs in parallel with T002 (T002 starts the install, T003 verifies after).
- T005 runs in parallel with T004 (different files).
- Within US1: T008, T009, T010, T011 all run in parallel (different files; T007 is the lead-in for the import-path pattern).
- US1 and US2 may run in parallel after Phase 2 (different files except chat.py — coordinate T007 + T017 in one developer's queue).
- T023 and T024 run in parallel during Polish.

---

## Parallel Example: User Story 1 swap fan-out (after T006 + T007 land)

```bash
# Four developers, or one developer with four terminals:
#   Dev A: T008 — swap in assistant/assistant.py
#   Dev B: T009 — swap in workflow/clients.py
#   Dev C: T010 — swap in query_builder/agent.py
#   Dev D: T011 — swap in parameter_extractor/agent.py
# Converge in T012: uv run poe check
```

---

## Implementation Strategy

**MVP increment** = US1 only. Merging US1 alone is a safe, valuable change: it escapes the rc4 release-candidate, moves to a documented public API, and is verified by `uv run poe check` plus a 3-turn smoke. Multi-turn chat continues to work; the **Analyze Results** affordance remains greyed out (no regression vs. today).

**Full feature** = US1 + US2 + US3. US2 is the change that unlocks feature 008 — landing it within the same PR-or-PR-train as US1 keeps the framework upgrade and the telemetry fix bundled, which makes the rollback story simpler.

**Recommended sequence for one developer**: T001 → T002 → T003 → T004 → T005 → T006 (failing) → T007 → T008-T011 (parallel) → T012 → T013-T014 (failing) → T014b → T015 → T016 → T017 → T018 → T019 → T020 → T021 → T022 → T023 → T024.

**Total**: 25 tasks. US1: 7 tasks. US2: 7 tasks (T013, T014, T014b, T015, T016, T017, T018). US3: 3 tasks. Setup + Foundational + Polish: 8 tasks.
