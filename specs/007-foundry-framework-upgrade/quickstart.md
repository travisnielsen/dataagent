# Quickstart — Foundry Agent Framework Upgrade & Portal Trace Correlation

**Feature**: 007-foundry-framework-upgrade
**Date**: 2026-05-17 (rescoped)
**Updated**: 2026-05-17 (post-implementation correction; Step 1 rewritten, Step 4 commands corrected)

## Prereqs

```bash
git switch 007-foundry-framework-upgrade
uv sync                  # picks up the agent-framework>=1.4.0,<2 pin
uv run poe check         # must pass before you continue
```

`.env` (in `src/backend/`) must contain at least:

```bash
AZURE_AI_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4.1
APPLICATIONINSIGHTS_CONNECTION_STRING=<your connection string>   # already required for tracing

# Binds runtime FoundryAgent constructions to a hosted PromptAgent record by name.
# Default "DataAssistant" matches the record provisioned in the Foundry portal today.
AZURE_AI_ORCHESTRATOR_AGENT_NAME=DataAssistant
AZURE_AI_ORCHESTRATOR_AGENT_ID=                # optional; pins to a specific agent version
```

## Step 1 — Verify the hosted PromptAgent records exist

This feature binds to **two** hosted PromptAgent records that must already exist in the Foundry project: `DataAssistant` (orchestrator) and `query-builder-agent` (workflow). Records are created out-of-band via the Foundry portal; the runtime does not create them.

1. Open Microsoft Foundry → your project → **Agents**.
2. Confirm two records with `kind: prompt`:
   - `DataAssistant` — backing the chat orchestrator. Instructions field is overridden per-request by [src/backend/assistant/assistant_prompt.md](src/backend/assistant/assistant_prompt.md), so the portal copy is decorative.
   - `query-builder-agent` — backing dynamic SQL generation. Instructions overridden per-request by [src/backend/query_builder/prompt.md](src/backend/query_builder/prompt.md).
3. If a record is missing, create it via the portal ("+ Agent" → "Prompt agent"). Use the matching markdown file as the initial prompt body; the runtime will override it on every call.

> **Not provisioned today**: `parameter-extractor-agent`. The extractor still runs as `Agent(client=FoundryChatClient(...))` and spans flow to Application Insights, but the portal Traces tab has no per-agent view for it. Promoting it to `FoundryAgent` requires creating the record first — tracked as a follow-up under [tasks.md T032/T033](tasks.md#phase-7--post-implementation-correction-portal-trace-correlation).

## Step 2 — Verify multi-turn chat still works (manual, ≥ 3 turns)

```bash
uv run poe dev-api &                  # backend on :8000
cd src/frontend && pnpm dev &         # frontend on :3000
```

1. Open `http://localhost:3000`. Ask: *"Show me top customers last month."*
2. Wait for the response. Open browser DevTools → Network → the `chat/stream` request. Note the `conversation_id` value echoed on the `done` event.
3. Ask: *"Filter that to North America."*
4. Confirm in backend logs that the same `conversation_id` is reused.
5. Ask: *"Now break it out by month."*

Pass criteria:

- All three turns succeed and use the same `conversation_id` value (SC-002).
- The `conversation_id` value is unchanged in shape from the pre-upgrade baseline (still the server-managed Foundry conversation id).

## Step 3 — Verify the Foundry portal shows the agent correlation (the key SC for this feature)

Within ~1 minute of the chat turns above completing:

1. Open Microsoft Foundry → your project → **Agents** → `DataAssistant` → **Traces** tab.
2. In the row list, confirm:
   - One row per turn appears (turns share a single `conv_…` conversation_id; each turn gets a distinct trace_id).
   - The **Conversation ID** column matches the value the SSE `done` event returned.
   - The agent identification (record name + version) matches `AZURE_AI_ORCHESTRATOR_AGENT_NAME`.
3. Click into a row. Confirm the conversation timeline loads and shows the model + tool spans for that turn.
4. If the chat turn exercised dynamic SQL generation (no template match), navigate to Agents → `query-builder-agent` → Traces and confirm a corresponding row appears there for that turn.

This satisfies SC-003 / SC-006 and is the prerequisite for feature 008 to filter traces by agent.

> **Two trace IDs per conversation is correct.** Foundry creates a per-turn trace, not a per-conversation trace. A conversation with N turns produces N traces sharing one conversation_id.

## Step 4 — Verify the cleanup happened

```bash
# Zero rc4 internal-package imports anywhere in backend code
rg -n 'agent_framework_azure_ai' src/backend
# Expected: no matches.

# Resolved framework version is stable, not rc
uv pip show agent-framework | grep -i '^version:'
# Expected: 1.4.x or higher, no 'rc' suffix.

# FoundryAgent is the hosted-agent connector for orchestrator + query-builder
rg -n 'from agent_framework\.foundry import' src/backend
# Expected: matches in chat.py, query_builder/agent.py, workflow/clients.py (FoundryAgent),
# and parameter_extractor/agent.py (FoundryChatClient — the deferred case).

# inspect.isawaitable workaround removed
rg -n 'inspect\.isawaitable' src/backend/api/routers/chat.py
# Expected: no matches.
```

Note: `agent_reference` itself is set internally by the agent-framework SDK when a `FoundryAgent` is invoked — you will not find a literal `agent_reference` string in Cadence's source. To confirm it is actually being sent, the portal Traces tab populating (Step 3) is the definitive signal.

## Rollback

If the upgrade causes a regression:

1. `git revert <commit-sha>` on the deployment branch.
2. Redeploy the container.
3. Existing Foundry conversations and the provisioned orchestrator agent record remain in Foundry (unaffected by code rollback). The reverted code path continues to talk to the same project.
4. Feature 008, if it has shipped, will lose its agent-column correlation again (its trace-based eval will return empty until 007 is re-rolled-forward). This is expected and is the gating relationship between the two features.

## Known-good evidence to compare against after the upgrade

| Check | Pre-upgrade (rc4) | Post-upgrade (1.4.x) | Where to look |
|-------|------------------|----------------------|---------------|
| SSE `done` event field | `conversation_id` (32-char hex or `conv_…`) | `conversation_id` (unchanged shape; server-managed Foundry id) | Browser DevTools / curl on `/api/chat/stream` |
| Portal Traces tab (per-agent view) | n/a — not opened | populated for `DataAssistant` and `query-builder-agent` (one row per turn, conversation_id matches) | Foundry portal → Agents → <name> → Traces |
| Portal **Conversation ID** column | populated under the project-wide view | populated under per-agent views | same |
| `rg agent_framework_azure_ai src/backend` | 5+ matches | 0 matches | terminal |
| `rg FoundryAgent src/backend` | 0 matches | ≥ 4 matches (chat.py, query_builder/agent.py, workflow/clients.py, assistant/assistant.py type hint, pipeline.py type hint) | terminal |
