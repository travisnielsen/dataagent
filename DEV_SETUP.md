# Dev Setup

How to set up your development environment for Cadence.

For coding standards, see [CODING_STANDARD.md](CODING_STANDARD.md).

## System Requirements

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) package manager
- [pnpm](https://pnpm.io/) (for frontend)

## Quick Setup

```bash
# One-command setup (installs Python, venv, deps, hooks)
./devsetup.sh

# Or with a specific Python version
./devsetup.sh 3.12
```

## Manual Setup

### Install uv

```bash
# Linux / macOS / WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Install Dependencies

```bash
# Install Python and create venv
uv python install 3.11
uv venv --python 3.11

# Install all dependencies
uv sync --all-extras --dev

# Install git hooks
uv run poe prek-install
```

### Frontend Setup

```bash
cd src/frontend
pnpm install
```

## Running the App

### Backend API

```bash
# Via poe task
uv run poe dev-api

# Or directly
cd src/backend && uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Requires `.env` file in `src/backend/` — copy from `src/backend/.env.example`.

### Frontend

```bash
cd src/frontend
pnpm run dev
```

### Evaluation Runner

Cadence evaluations live in [`src/evaluations/`](src/evaluations/) (a sibling
of the backend, so eval changes never trigger an API redeploy). See
[src/evaluations/README.md](src/evaluations/README.md) for the full design.

Evaluations are **Foundry trace-based**: we replay the gold dataset against
the deployed API to emit `invoke_agent` OTel spans, then submit a Foundry
trace eval that scores those spans. There is no local-SDK path.

**Quick start** (against the deployed environment):

```bash
set -a && source src/backend/.env && set +a

# 1. Replay the gold dataset through the deployed API to generate traces
PYTHONPATH=src \
CADENCE_API_BASE_URL="https://<api-fqdn>" \
AZURE_AD_CLIENT_ID="<api-app-registration-client-id>" \
uv run python -m evaluations replay \
  --dataset src/evaluations/datasets/cadence-eval-gold-v1.jsonl

# 2. Wait ~2-3 minutes for App Insights ingestion, then submit the trace eval
PYTHONPATH=src \
AZURE_AI_AGENT_ID="DataAssistant:1" \
uv run python -m evaluations run \
  --dataset src/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy \
  --trigger manual \
  --cloud
```

**Requirements**:

- `.env` configured in `src/backend/` with `AZURE_AI_PROJECT_ENDPOINT` and
  `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- Deployed Cadence API with Application Insights tracing enabled
  (`ENABLE_INSTRUMENTATION=true`) and an API app registration whose client id
  the runner can mint AAD tokens for (UAMI in CI, `az login` locally)
- Repo vars managed by Terraform: `NEXT_PUBLIC_API_URL`, `AZURE_AD_CLIENT_ID`,
  `AZURE_AI_AGENT_ID`. Run `infra/scripts/update-github-vars-from-terraform.sh
  --apply` to refresh them.

## VS Code Setup

Install the [Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python) and open the project folder.

The workspace is pre-configured (`.vscode/settings.json`) to use the root `.venv` and set `src/` as the Python path.

### Copilot Auto-Approve Commands

| Pattern            | Commands                                 | Purpose           |
| ------------------ | ---------------------------------------- | ------------------ |
| `/^uv run poe\\b/` | `uv run poe test`, `uv run poe lint`     | Poe task runner   |
| `/^uv sync\\b/`    | `uv sync`                                | Dependency sync   |
| `/^git status\\b/` | `git status`                             | Read-only git     |
| `/^git diff\\b/`   | `git diff`                               | Read-only git     |
| `/^git log\\b/`    | `git log`                                | Read-only git     |
| `/^pytest\\b/`     | `pytest`                                 | Test runs         |

## Available Poe Tasks

### Setup

| Task        | Command                  | Description                                |
| ----------- | ------------------------ | ------------------------------------------ |
| `bootstrap` | `uv run poe bootstrap`  | Full dev environment setup                 |
| `setup`     | `uv run poe setup`      | Quick sync (assumes venv exists)           |
| `install`   | `uv run poe install`    | Install all deps including updates         |

### Code Quality

| Task        | Command                  | Description                                |
| ----------- | ------------------------ | ------------------------------------------ |
| `check`     | `uv run poe check`      | Run ALL quality checks (required pre-commit)|
| `format`    | `uv run poe format`     | Format + lint + typecheck                  |
| `lint`      | `uv run poe lint`       | Ruff linting                               |
| `typecheck` | `uv run poe typecheck`  | basedpyright type checking                 |
| `quality`   | `uv run poe quality`    | Format + lint + typecheck + metrics        |
| `metrics`   | `uv run poe metrics`    | Complexity + dead code                     |

### Testing

| Task        | Command                  | Description                                |
| ----------- | ------------------------ | ------------------------------------------ |
| `test`      | `uv run poe test`       | Run tests with coverage                    |

### Development

| Task        | Command                  | Description                                |
| ----------- | ------------------------ | ------------------------------------------ |
| `dev-api`   | `uv run poe dev-api`    | Start FastAPI dev server                   |

## Environment Variables

The API requires a `.env` file in `src/backend/` with:

| Variable                              | Description                        | Required |
| ------------------------------------- | ---------------------------------- | -------- |
| `AZURE_AI_PROJECT_ENDPOINT`           | Foundry project endpoint           | Yes      |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME`      | Default model deployment           | Yes      |
| `AZURE_SEARCH_ENDPOINT`              | AI Search for query templates      | Yes      |
| `AZURE_SQL_SERVER`                    | SQL Server hostname                | Yes      |
| `AZURE_SQL_DATABASE`                  | Database name                      | Yes      |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | For tracing                      | No       |
| `ENABLE_INSTRUMENTATION`             | Enable Application Insights        | No       |

## See Also

- [CODING_STANDARD.md](CODING_STANDARD.md) - Code style and conventions
- [CONTRIBUTING.md](CONTRIBUTING.md) - Git conventions, PR guidelines
- [AGENTS.md](AGENTS.md) - AI agent quick reference
