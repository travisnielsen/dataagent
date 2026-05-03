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

See [Foundry Evaluations Quickstart](specs/005-foundry-evaluations/quickstart.md) for full evaluation setup.

**Quick start** (local evaluation):

```bash
# Harvest Foundry traces and merge with gold dataset
uv run python -m evaluations harvest \
  --output .foundry/datasets \
  --gold src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --days 7

# Run evaluation on mixed dataset
uv run python -m evaluations run \
  --dataset .foundry/datasets/cadence-eval-mixed-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,sql_safety \
  --trigger manual
```

**Requirements**:
- `.env` configured in `src/backend/` with `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- Azure AI Foundry project with deployed model (e.g., gpt-4o)
- For SQL safety evaluation: `AZURE_SQL_*` and `AZURE_SEARCH_*` environment variables

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
