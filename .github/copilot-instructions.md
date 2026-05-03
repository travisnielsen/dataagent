# Copilot Instructions for Cadence

Trust these instructions first; only search if information is incomplete or incorrect.

## Quick Reference

| What                | Where               |
| ------------------- | ------------------- |
| **Package manager** | `uv` (NOT pip)      |
| **Task runner**     | `uv run poe <task>` |
| **All checks**      | `uv run poe check`  |
| **Python version**  | 3.11+               |
| **Line length**     | 100 chars            |

## Essential Commands

```bash
uv run poe check     # Run ALL quality checks (required before commit)
uv run poe test      # Run tests
uv run poe lint      # Lint only
uv run poe format    # Format, lint, and type check
uv run poe dev-api   # Start FastAPI dev server
```

## Documentation (Read These)

| Document                                    | Purpose                             |
| ------------------------------------------- | ----------------------------------- |
| [CODING_STANDARD.md](../CODING_STANDARD.md) | Anti-slop rules, forbidden patterns |
| [DEV_SETUP.md](../DEV_SETUP.md)             | Environment setup, all poe tasks    |
| [AGENTS.md](../AGENTS.md)                   | AI agent quick reference            |
| [CONTRIBUTING.md](../CONTRIBUTING.md)       | Git conventions, PR guidelines      |

## Path-Specific Instructions

You **MUST** load these instructions when working on files that match the patterns below.

| File Pattern    | Instructions                                                  |
| --------------- | ------------------------------------------------------------- |
| `**/*.py`       | [python.instructions.md](instructions/python.instructions.md) |
| `**/*.agent.md` | [agents.instructions.md](instructions/agents.instructions.md) |
| Git files       | [git.instructions.md](instructions/git.instructions.md)       |

## Custom Agents

9 specialized role agents in `.github/agents/`. See [agents.instructions.md](instructions/agents.instructions.md) for usage.

Use `@agent-name` in Copilot Chat: `@orchestrator`, `@planner`, `@architect`, `@implementer`, `@tester`, `@reviewer`, `@security`, `@infrastructure`, `@docs`

## Spec Kit (Spec-Driven Development)

This project uses [github/spec-kit](https://github.com/github/spec-kit) for structured feature planning. Spec Kit provides slash commands that generate design artifacts before implementation begins.

### Planning Flow

```
/speckit.specify "feature description"   → specs/<feature>/spec.md
/speckit.plan                            → specs/<feature>/plan.md
/speckit.tasks                           → specs/<feature>/tasks.md
```

Optional steps: `/speckit.clarify` (refine spec), `/speckit.analyze` (consistency check), `/speckit.checklist` (generate review checklists).

### Integration with Role Agents

Spec Kit handles **planning**. Our role agents handle **execution**. The `@orchestrator` bridges them:

```
Spec Kit Planning:  /speckit.specify → /speckit.plan → /speckit.tasks
Bridge:             @orchestrator reads tasks.md → delegates to role agents
Execution:          @implementer → @tester → @reviewer → @security
```

**Key rule:** Skip `/speckit.implement` — use `@implementer` and other role agents instead.

### Task Tracking

All task tracking uses **Spec Kit `tasks.md` files** in `specs/<feature>/tasks.md`. This is the single source of truth for work items.

- Tasks use checkbox format: `- [ ] [T001] [P?] [Story?] Description`
- `[P]` marks parallelizable tasks (different files, no dependencies)
- Agents mark tasks complete by changing `- [ ]` to `- [x]`
- Run `/speckit.analyze` to check consistency between spec, plan, and tasks

### Spec Kit Artifacts

| Directory / File            | Purpose                           |
| --------------------------- | --------------------------------- |
| `.specify/`                 | Spec Kit config, scripts, memory  |
| `.github/prompts/`          | Prompt files for slash commands   |
| `specs/<feature>/spec.md`   | Requirements and user stories     |
| `specs/<feature>/plan.md`   | Architecture and file layout      |
| `specs/<feature>/tasks.md`  | Ordered implementation checklist  |

## Non-Negotiable Rules

1. **Async-first**: Use `async def` for I/O, never block the event loop
2. **Type hints** on all parameters and returns
3. **Pydantic models** for all I/O (no raw dicts)
4. **`uv run poe check`** must pass before commit
5. **Conventional Commits**: `type(scope): description`
6. **Branch naming compliance**: Before any `git switch -c` or `git branch -m`, enforce [CONTRIBUTING.md](../CONTRIBUTING.md) branch policy (`<type>/<ticket>-<short-description>`; docs-only shortcut `docs/<short-description>`). If required ticket is missing, ask the user before proceeding.


## Architecture Overview

This is a **multi-agent NL2SQL application** using Microsoft Agent Framework (MAF) with a FastAPI backend and Next.js/assistant-ui frontend. Communication happens via SSE streaming, with multi-turn continuity anchored to the provider conversation ID and propagated through `AgentSession.service_session_id`.

### Architecture Components
1. **DataAssistant** (`src/backend/assistant/`) - Manages chat sessions, classifies intent (data query vs conversation), handles refinements, invokes NL2SQL pipeline
2. **NL2SQLController** (`src/backend/entities/nl2sql_controller/`) - Orchestrates query flow, searches templates via Azure AI Search, executes SQL via `execute_sql` tool
3. **ParameterExtractor** (`src/backend/entities/parameter_extractor/`) - Extracts parameter values from natural language to fill SQL template tokens
4. **ParameterValidator** (`src/backend/entities/parameter_validator/`) - Non-LLM validation of extracted parameters (type, range, regex, allowed values)
5. **QueryValidator** (`src/backend/entities/query_validator/`) - Validates SQL syntax, table allowlist, and security before execution
6. **QueryBuilder** (`src/backend/entities/query_builder/`) - Generates dynamic SQL from table metadata when no template matches

### Architecture Flow
```
User → DataAssistant (intent classification)
     → NL2SQL Workflow: NL2SQLController → ParameterExtractor/QueryBuilder → Validators → execute_sql
     → DataAssistant (renders response) → User
```

The DataAssistant lives **outside** the NL2SQL pipeline function flow. It manages the application session (`AgentSession`) and invokes `process_query()` for data queries. This separation allows:
- Session-level conversation context for refinements
- Intent classification before invoking the workflow
- Cleaner separation of concerns

The NL2SQL pipeline is implemented in `src/backend/nl2sql_controller/pipeline.py` and is called per request with injected clients.

## Key Patterns

### Agent Structure
Each workflow executor follows a consistent pattern in its folder:
- `executor.py` - Workflow integration with `@handler` decorators
- `prompt.md` - Agent instructions (loaded at runtime via `load_prompt()`)
- `tools/` - AI function tools decorated with `@tool`

The assistant folder contains:
- `assistant.py` - DataAssistant class (not a MAF executor)
- `assistant_prompt.md` - Intent classification prompt

### Models Structure
Shared models are in `src/backend/models/` with functional grouping:
- `schema.py` - AI Search index models (`QueryTemplate`, `ParameterDefinition`, `TableMetadata`)
- `extraction.py` - Parameter extraction workflow (`ParameterExtractionRequest`, `MissingParameter`)
- `generation.py` - SQL generation (`SQLDraft`, `SQLDraftMessage`, `QueryBuilderRequest`)
- `execution.py` - Query results (`NL2SQLResponse`)

All models are re-exported from `src/backend/models/__init__.py` for backward compatibility.

### Import Pattern
With `src/backend/` on the Python path, all imports use the package directly:
```python
from models import QueryTemplate
from entities.shared.search_client import AzureSearchClient
from api.step_events import emit_step_start
```

### SQLDraft Message Flow
The `SQLDraft` model carries SQL through the validation pipeline. The `SQLDraftMessage` wrapper includes a `source` field to track which executor sent it:
- `source="param_extractor"` - Fresh extraction from template
- `source="param_validator"` - After parameter validation (check `params_validated` flag)
- `source="query_validator"` - After query validation (check `query_validated` flag)
- `source="query_builder"` - Dynamic SQL generation

NL2SQLController routes based on these flags to prevent infinite loops.

### Query Templates
SQL queries are parameterized templates stored in `infra/data/query_templates/` and indexed in Azure AI Search. Parameters use `%{{name}}%` token syntax with validation rules. See `src/backend/models/schema.py` for `QueryTemplate` and `ParameterDefinition` schemas.

### SSE Streaming & Step Events
Step events provide real-time progress to the UI. Emit from tools using:
```python
from api.step_events import emit_step_start, emit_step_end
emit_step_start("Executing SQL query...")
# ... do work ...
emit_step_end("Executing SQL query...")
```

## Environment Configuration

API requires `.env` in `src/backend/` folder with:
- `AZURE_AI_PROJECT_ENDPOINT` - Foundry project endpoint (required)
- `AZURE_AI_MODEL_DEPLOYMENT_NAME` - Default model deployment
- `AZURE_SEARCH_ENDPOINT` - AI Search for query templates
- `AZURE_SQL_*` - Database connection settings
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - For tracing (optional)
- `ENABLE_INSTRUMENTATION=true` - Enable Application Insights tracing

## Frontend Integration

- Uses `assistant-ui` library with `ExternalStoreRuntime` for SSE
- Session continuity uses provider `conversation_id` as the canonical key and passes it into `AgentSession.service_session_id` (fallback to local `session_id` when provider ID is unavailable)
- Tool results render via generative UI components in `src/frontend/components/assistant-ui/`
- Auth via MSAL with optional Azure AD (`src/frontend/lib/msalConfig.ts`)

## Project Structure

```
cadence/
├── src/                        # All application code
│   ├── backend/               # Python backend
│   │   ├── __init__.py
│   │   ├── Dockerfile
│   │   ├── api/               # FastAPI application
│   │   │   ├── main.py        # App entrypoint
│   │   │   ├── middleware/    # Auth middleware
│   │   │   ├── routers/       # API routes
│   │   │   └── step_events.py # SSE step event helpers
│   │   ├── entities/          # Agent executors
│   │   │   ├── orchestrator/
│   │   │   ├── nl2sql_controller/
│   │   │   ├── parameter_extractor/
│   │   │   ├── parameter_validator/
│   │   │   ├── query_builder/
│   │   │   ├── query_validator/
│   │   │   ├── shared/        # Shared utilities (search_client)
│   │   │   └── workflow/
│   │   └── models/            # Pydantic models
│   └── frontend/              # Next.js + assistant-ui
├── infra/                      # Infrastructure, deployment, and IaC assets
│   ├── data/                  # Query templates and table metadata source files
│   ├── search-config/         # AI Search index and schema configuration
│   ├── scripts/               # Deployment and setup scripts
│   └── terraform/             # Terraform root module (private networking stack)
│       ├── ai-platform.tf     # AI platform resources (Foundry, Search, SQL, etc.)
│       ├── networking.tf      # VNet, subnets, NSGs, private DNS/endpoints
│       ├── compute.tf         # Container Apps and related compute resources
│       ├── security.tf        # RBAC, identity, and security controls
│       ├── observability.tf   # Monitoring and diagnostics resources
│       ├── data.tf            # Data plane related resources and wiring
│       ├── providers.tf       # Provider configuration
│       ├── variables.tf       # Input variable definitions
│       ├── outputs.tf         # Output values for downstream automation
│       └── *.tfvars*          # Environment-specific variable files
├── tests/                     # Test suite
│   ├── unit/
│   └── integration/
├── pyproject.toml             # Python config (deps, tools, linting)
├── devsetup.sh                # One-command dev setup
└── .github/                   # CI/CD, agents, instructions
```

## Testing Strategy

Tests use `pytest` with `pytest-asyncio`. Key test scenarios to cover:

1. **Empty Results** - Queries that succeed but return no rows (e.g., "show orders from 2050")
2. **Invalid Queries** - SQL execution failures, malformed input, permission errors
3. **Clarification Prompts** - When ParameterExtractor can't infer values and `ask_if_missing=true`
4. **Parameter Validation** - Invalid parameter types, out-of-range values, regex failures
5. **Query Validation** - Disallowed tables, SQL injection patterns, non-SELECT statements

Run tests: `uv run poe test`

## Adding New Capabilities

When adding a new tool to an agent:
1. Create function in agent's `tools/` folder with `@tool` decorator
2. Import and add to agent's `tools=[]` list in `executor.py`
3. Update the agent's `prompt.md` to describe when/how to use it
4. For progress feedback, emit step events from within the tool

When modifying workflow routing:
- ChatAgent's `prompt.md` controls the triage logic (JSON routing vs direct response)
- DataAgent's `handle_sql_draft` routes based on `SQLDraft` flags (`params_validated`, `query_validated`)
- Add new executors in `workflow.py` with bidirectional edges to DataAgent

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
