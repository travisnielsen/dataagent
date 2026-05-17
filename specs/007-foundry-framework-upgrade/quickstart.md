# Quickstart — Foundry Agent Framework Upgrade & Portal Trace Correlation

**Feature**: 007-foundry-framework-upgrade
**Date**: 2026-05-17 (rescoped)

## Prereqs

```bash
git switch 007-foundry-framework-upgrade
uv sync                  # picks up the agent-framework>=1.4.0,<2 pin
uv run poe check         # must pass before you continue
```

`.env` (in `src/backend/`) must contain at least:

```bash
AZURE_AI_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o-mini
APPLICATIONINSIGHTS_CONNECTION_STRING=<your connection string>   # already required for tracing

# NEW — added by this feature; either set explicitly, or leave unset and let provision.py create one
AZURE_AI_ORCHESTRATOR_AGENT_NAME=cadence-data-assistant
AZURE_AI_ORCHESTRATOR_AGENT_ID=                # optional; logged on first boot if left empty
```

## Step 1 — Provision (or resolve) the orchestrator agent

The new `provision_orchestrator_agent()` helper runs at backend startup. To run it standalone (for example, to capture the id ahead of deployment):

```bash
uv run python -c "
import asyncio, os
from azure.identity.aio import DefaultAzureCredential
from config.settings import get_settings
from assistant.provision import provision_orchestrator_agent

async def main():
    async with DefaultAzureCredential() as cred:
        agent_id = await provision_orchestrator_agent(get_settings(), cred)
        print('AZURE_AI_ORCHESTRATOR_AGENT_ID=' + agent_id)

asyncio.run(main())
"
```

Persist the printed id in `.env` (and, for production, in the Container Apps env spec).

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

Within ~5 minutes of the chat turns above completing:

1. Open Microsoft Foundry → your project → **Agents** → **Traces** (the screenshot tab the user shared).
2. In the row list, confirm:
   - At least three new rows appear (one per turn).
   - The **Agent** column shows the configured orchestrator agent name (e.g., `cadence-data-assistant`).
   - The **Conversation ID** column matches the value the SSE `done` event returned.
3. Click into a row. Confirm the conversation timeline loads and shows the model + tool spans for that turn.

This satisfies SC-003 and is the prerequisite for feature 008 to filter traces by `gen_ai.agent.id`.

## Step 4 — Verify the cleanup happened

```bash
# Zero rc4 internal-package imports anywhere in backend code
rg -n 'agent_framework_azure_ai' src/backend
# Expected: no matches.

# Resolved framework version is stable, not rc
uv pip show agent-framework | grep -i '^version:'
# Expected: 1.4.x or higher, no 'rc' suffix.

# agent_reference is set at the orchestrator construction site
rg -n 'agent_reference' src/backend
# Expected: at least one match in src/backend/api/routers/chat.py (or wherever the orchestrator
# FoundryChatClient is constructed).
```

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
| Portal Traces tab columns | Trace ID populated, Agent column EMPTY | Trace ID populated, **Agent column populated** with configured name | Foundry portal → Agents → Traces |
| Portal **Conversation ID** column | populated (existing behavior) | populated (unchanged) | same |
| `rg agent_framework_azure_ai src/backend` | 5+ matches | 0 matches | terminal |
| `rg agent_reference src/backend` | 0 matches | ≥ 1 match | terminal |
