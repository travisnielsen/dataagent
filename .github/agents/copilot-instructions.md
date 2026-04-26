# cadence Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-24

## Active Technologies
- Python 3.11+ (backend), TypeScript/React 19 + Next.js 16 (frontend) + FastAPI, Microsoft Agent Framework (`agent-framework`), Pydantic v2, `@assistant-ui/react`, existing tool-ui components (004-what-if-scenarios)
- Azure SQL (WideWorldImportersStd), Azure AI Search metadata indexes (004-what-if-scenarios)
- Python 3.11+ + `azure-ai-evaluation`, `azure-ai-projects` (via `agent-framework`), `azure-identity`, `azure-monitor-opentelemetry`, FastAPI, Pydantic v2 (005-foundry-evaluations)
- JSONL files (datasets), Application Insights (traces), Foundry project (cloud eval runs) (005-foundry-evaluations)

- Python 3.11+, TypeScript/Next.js + FastAPI, Microsoft Agent Framework (MAF), Pydantic, React, assistant-ui, Tailwind CSS (002-dynamic-query-enhancements)

## Project Structure

```text
infra/
	search-config/
	terraform/
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11+, TypeScript/Next.js: Follow standard conventions

## Recent Changes
- 005-foundry-evaluations: Added Python 3.11+ + `azure-ai-evaluation`, `azure-ai-projects` (via `agent-framework`), `azure-identity`, `azure-monitor-opentelemetry`, FastAPI, Pydantic v2
- 004-what-if-scenarios: Added Python 3.11+ (backend), TypeScript/React 19 + Next.js 16 (frontend) + FastAPI, Microsoft Agent Framework (`agent-framework`), Pydantic v2, `@assistant-ui/react`, existing tool-ui components

- 002-dynamic-query-enhancements: Added Python 3.11+, TypeScript/Next.js + FastAPI, Microsoft Agent Framework (MAF), Pydantic, React, assistant-ui, Tailwind CSS

<!-- MANUAL ADDITIONS START -->
- Branch operations must follow `CONTRIBUTING.md` branch naming policy.
- Use `<type>/<ticket>-<short-description>` for non-docs branches.
- `docs/<short-description>` is allowed for docs-only work.
- If a non-docs ticket is missing, ask the user before creating or renaming a branch.
<!-- MANUAL ADDITIONS END -->
