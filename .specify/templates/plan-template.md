# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, Microsoft Agent Framework (MAF GA 1.4.x), Pydantic
**Frontend**: Next.js, React, assistant-ui, Tailwind CSS
**AI Platform**: Azure AI Foundry, Azure OpenAI
**Storage**: Azure SQL, Azure AI Search
**Auth**: Azure AD via MSAL (optional)
**IaC**: Terraform
**Testing**: pytest, pytest-asyncio (`uv run poe test`)
**Target Platform**: Linux containers (Azure Container Apps)
**Project Type**: Multi-agent NL2SQL web service (FastAPI backend + Next.js frontend)
**Package Manager**: uv (NOT pip)
**Quality Gate**: `uv run poe check` (required before commit)
**Performance Goals**: [NEEDS CLARIFICATION — feature-specific]
**Constraints**: [NEEDS CLARIFICATION — feature-specific]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

[Gates determined based on constitution file]

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── backend/
│   ├── api/               # FastAPI application (routers/, middleware/, step_events.py)
│   ├── assistant/         # DataAssistant (session management, intent classification)
│   ├── entities/          # Agent executors (one folder per executor)
│   │   ├── nl2sql_controller/
│   │   ├── parameter_extractor/
│   │   ├── parameter_validator/
│   │   ├── query_builder/
│   │   ├── query_validator/
│   │   ├── shared/        # Shared utilities (search_client, etc.)
│   │   └── workflow/      # MAF workflow definition
│   └── models/            # Pydantic models (schema, extraction, generation, execution)
├── frontend/              # Next.js + assistant-ui + Tailwind CSS
│   ├── app/               # App Router pages
│   ├── components/        # UI components (assistant-ui/)
│   └── lib/               # Utilities, MSAL config
└── evaluations/           # Foundry evaluation datasets and scripts

infra/
├── terraform/             # Terraform root module (networking, compute, security, etc.)
├── data/                  # Query templates and table metadata
├── search-config/         # AI Search index schemas
└── scripts/               # Deployment and setup scripts

tests/
├── unit/
└── integration/
```

**Structure Decision**: This project uses a monorepo with `src/backend/` (Python/FastAPI),
`src/frontend/` (Next.js), and `infra/terraform/` (IaC). New backend features add
entities in `src/backend/entities/` following the executor pattern (executor.py, prompt.md, tools/).
New models go in `src/backend/models/` with re-export from `__init__.py`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
