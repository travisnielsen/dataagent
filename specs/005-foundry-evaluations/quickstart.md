# Quickstart: Foundry Evaluations for NL2SQL

**Feature**: 005-foundry-evaluations
**Date**: 2026-03-24

## Prerequisites

- Python 3.11+ with `uv` package manager
- Azure AI Foundry project with deployed model (e.g., `gpt-4o`)
- `.env` file configured in `src/backend/` with:
  - `AZURE_AI_PROJECT_ENDPOINT` (required for trace harvesting and cloud evaluation)
  - `AZURE_AI_MODEL_DEPLOYMENT_NAME` (required)
  - Optional: `APPLICATIONINSIGHTS_CONNECTION_STRING` (for tracing, not required for evaluation)

## Local Development Setup

### 1. Create `.env` file

Copy the example and configure with your Foundry project:

```bash
cd src/backend
cp .env.example .env
# Edit .env and fill in:
#   - AZURE_AI_PROJECT_ENDPOINT
#   - AZURE_AI_MODEL_DEPLOYMENT_NAME
#   - AZURE_SQL_* (for query execution)
#   - AZURE_SEARCH_* (for template matching)
```

**Minimal `.env` for evaluation only** (no SQL or templates):

```bash
# Foundry project configuration
AZURE_AI_PROJECT_ENDPOINT=https://your-project.services.ai.azure.com/api/projects/your-project
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o

# For cloud evaluation only (optional)
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...
```

### 2. Run harvest locally

```bash
cd /path/to/cadence
uv sync --all-extras --dev

# Harvest Foundry traces and merge with gold dataset
uv run python -m evaluations harvest \
  --output .foundry/datasets \
  --gold src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --days 7 \
  --limit 100
```

### 3. Run evaluation locally

```bash
# Run on mixed dataset (from harvest above)
uv run python -m evaluations run \
  --dataset .foundry/datasets/cadence-eval-mixed-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,sql_safety \
  --trigger manual \
  --output .foundry/results
```

## GitHub Actions Configuration

### Required Secrets

The nightly and PR gate workflows require these secrets to be configured in your GitHub repository settings:

**Repository Settings → Secrets and variables → Actions**

| Secret Name | Purpose | Required | Example |
|-------------|---------|----------|---------|
| `AZURE_AI_PROJECT_ENDPOINT` | Foundry project endpoint | ✅ Yes | `https://your-project.services.ai.azure.com/api/projects/your-project` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model deployment name (judge model) | ✅ Yes | `gpt-4o` |
| `AZURE_SEARCH_ENDPOINT` | AI Search endpoint (for template matching) | ✅ Yes | `https://your-search.search.windows.net` |
| `AZURE_SEARCH_INDEX_TABLES` | AI Search index name for tables | ✅ Yes | `tables` |
| `AZURE_SQL_SERVER` | SQL database server | ✅ Yes | `your-server.database.windows.net` |
| `AZURE_SQL_DATABASE` | SQL database name | ✅ Yes | `WideWorldImportersStd` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights connection (for tracing) | ❌ No | `InstrumentationKey=...` |

**Note**: Authentication secrets (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID) are **NOT needed** — the runners use GitHub OIDC federated credentials (configured in Terraform).

### Setting Up Secrets

1. Go to your repository: **Settings → Secrets and variables → Actions**
2. Click **"New repository secret"** for each secret above

Copy from your local `.env` file:

```bash
# Get values from your local .env
grep "^AZURE_AI_PROJECT_ENDPOINT\|^AZURE_AI_MODEL_DEPLOYMENT_NAME\|^AZURE_SEARCH_ENDPOINT\|^AZURE_SQL_SERVER" \
  src/backend/.env
```

### Authentication & Authorization Architecture

**Key Distinction: Authentication vs Authorization**

The evaluation workflows use **two separate mechanisms**:

1. **Authentication** (Getting the token):
   - GitHub OIDC federated credential → Azure access token
   - Handled by `DefaultAzureCredential` in the SDKs
   - Already configured in Terraform infrastructure ✅

2. **Authorization** (What you can do with the token):
   - RBAC role assignments on Foundry project and related services
   - Each role defines what APIs the token can access
   - **Foundry RBAC assignment may need to be added manually** (see below)

**How It Works**:

```
GitHub Action runs
  ↓
GitHub OIDC provider issues token
  ↓
Token exchanged for Azure access token (via federated credential in Entra ID)  [AUTHENTICATION ✅]
  ↓
Token presented to Foundry API with permission check
  ↓
Foundry checks: Does this principal have data plane permission to run evaluations?  [AUTHORIZATION ⚠️]
  ↓
If Azure AI User role assigned: API call succeeds (data plane ✅)
If only Contributor role: API call fails with 403 Forbidden (control plane only ❌)
If NO RBAC role: API call fails with 403 Forbidden (no permissions ❌)
```

**Critical: Control Plane vs Data Plane**

Azure RBAC roles split into two categories:

| Plane | Purpose | Examples | Good For | Evaluations? |
|-------|---------|----------|----------|-------------|
| **Control Plane** | Manage Azure resources (create, delete, modify) | Owner, Contributor, Reader | Infrastructure management | ❌ NO |
| **Data Plane** | Access and operate on resources at runtime | Azure AI User, Azure AI Project Manager | Development, running code | ✅ YES |

**`Contributor` role = Control plane only** → Cannot run evaluations ❌
**`Azure AI User` role = Data plane** → Can run evaluations ✅

**What's Already Set Up** (from existing Terraform):

The infrastructure automatically configures a GitHub federated identity with RBAC roles on these services:

| Resource | Role | Purpose | Assigned |
|----------|------|---------|----------|
| Storage Account | Storage Blob Data Contributor | Store evaluation artifacts | ✅ |
| AI Search | Search Service Contributor | Query templates & tables | ✅ |
| SQL Database | SQL DB Contributor | Query metadata, execute SELECT | ✅ |
| Container Registry | AcrPush | Push container images | ✅ |
| Resource Group | Contributor | Manage resources | ✅ |
| Resource Group | User Access Administrator | Manage RBAC | ✅ |
| **Foundry Project** | **Azure AI User** | **Run evaluations (data plane)** | ✅ **IMPLEMENTED** |

**✅ Foundry Project RBAC Role**

The GitHub federated principal **now has the `Azure AI User` RBAC role assigned on the Foundry project**. This enables:

- `harvest_foundry_traces()` — READ sessions and conversation history
- `run_cloud_evaluation()` — WRITE evaluation runs and submit datasets

**How to Deploy**:

This role assignment is defined in `infra/terraform/security.tf` and will be created automatically when you run `terraform apply`. If you need to deploy or update it:

**Deploy or Update Foundry RBAC Role**:

```terraform
resource "azurerm_role_assignment" "github_federated_ai_foundry_user" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI User"
  principal_id         = local.github_federated_rbac_principal_object_id
}
```

To deploy/update:

```bash
cd infra/terraform
terraform apply
```

**Option 2: Manual Assignment via Azure CLI** (if infrastructure code not available):

```bash
# Get your Foundry project resource ID
FOUNDRY_PROJECT_ID=$(az ai hub show \
  --resource-group <your-rg> \
  --name <your-foundry-hub> \
  --query id -o tsv)

# Get the GitHub federated principal object ID
GITHUB_PRINCIPAL_ID=$(az ad service-principal list \
  --display-name "github-federated" \
  --query "[0].id" -o tsv)

# Assign Azure AI User role on Foundry project (data plane permissions)
az role assignment create \
  --role "Azure AI User" \
  --assignee-object-id "$GITHUB_PRINCIPAL_ID" \
  --scope "$FOUNDRY_PROJECT_ID"
```

**To enable federated authentication**, provide these when running `terraform apply`:

```bash
terraform apply \
  -var "github_federated_principal_object_id=<object-id>" \
  -var "github_federated_principal_client_id=<client-id>"
```

Or configure via `terraform.tfvars`:

```hcl
github_federated_principal_object_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
github_federated_principal_client_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

**To get the GitHub federated principal IDs**:

```bash
# Use the Azure CLI to get the object ID of the GitHub federated principal
az ad service-principal list --display-name "github-federated" --query "[0].{id:id, appId:appId}" -o json

# Or use the GitHub Actions OIDC documentation
# See: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect
```

### What Secrets Are Actually Needed?

**Only configuration/endpoint secrets** — NOT authentication secrets:

| Secret | Used For | Type | Scope |
|--------|----------|------|-------|
| `AZURE_AI_PROJECT_ENDPOINT` | Harvest traces, publish runs | Endpoint URL | Foundry |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Judge model selection | Config value | Foundry |
| `AZURE_SEARCH_ENDPOINT` | Template search | Endpoint URL | AI Search |
| `AZURE_SEARCH_INDEX_TABLES` | Table metadata search | Index name | AI Search |
| `AZURE_SQL_SERVER` | Query execution, SQL safety eval | Server name | SQL |
| `AZURE_SQL_DATABASE` | Database name | Config value | SQL |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Tracing (optional) | Connection string | App Insights |

**NOT needed** (handled by federated credential + RBAC):

- ❌ `AZURE_CLIENT_ID`
- ❌ `AZURE_CLIENT_SECRET`
- ❌ `AZURE_TENANT_ID`

**Prerequisite for Foundry access:**

- ✅ **Federated credential must have `Azure AI User` role assigned on Foundry project** (see "Authentication & Authorization Architecture" section above)
- ⚠️ **Note**: `Contributor` role (control plane) is NOT sufficient — evaluations require `Azure AI User` (data plane)

### Data Flow & What Gets Pushed to Foundry

When workflows run with `--cloud` flag:

```
GitHub Actions Runner (federated credential → Azure token)
  ↓
harvest_foundry_traces() — Uses AIProjectClient to READ:
  • Foundry project sessions (last N days)
  • Conversation message history
  ↓
merge_datasets() — Local processing (no API calls)
  • Combines gold curated dataset with harvested traces
  • Outputs mixed JSONL to `.foundry/datasets/`
  ↓
run_cloud_evaluation() — Uses azure-ai-evaluation SDK to WRITE:
  • Submits dataset records to Foundry
  • Creates evaluation run in Foundry cloud
  • Executes evaluators in Foundry
  • Returns metrics and Studio URL
  ↓
persist_run_results() — Saves locally:
  • Summary JSON to `.foundry/results/`
  • Regression report to GitHub issue
```

**No sensitive data is pushed to Foundry** beyond the evaluation dataset records themselves. All trace harvesting and merging happens locally.

### Workflow Inheritance

Both `.github/workflows/eval-nightly.yml` and `.github/workflows/eval-pr-gate.yml` inherit endpoint/config secrets automatically:

```yaml
env:
  PYTHONPATH: src/backend

  # Endpoints and configuration (only these secrets needed)
  AZURE_AI_PROJECT_ENDPOINT: ${{ secrets.AZURE_AI_PROJECT_ENDPOINT }}
  AZURE_AI_MODEL_DEPLOYMENT_NAME: ${{ secrets.AZURE_AI_MODEL_DEPLOYMENT_NAME }}
  AZURE_SEARCH_ENDPOINT: ${{ secrets.AZURE_SEARCH_ENDPOINT }}
  AZURE_SEARCH_INDEX_TABLES: ${{ secrets.AZURE_SEARCH_INDEX_TABLES }}
  AZURE_SQL_SERVER: ${{ secrets.AZURE_SQL_SERVER }}
  AZURE_SQL_DATABASE: ${{ secrets.AZURE_SQL_DATABASE }}
  APPLICATIONINSIGHTS_CONNECTION_STRING: ${{ secrets.APPLICATIONINSIGHTS_CONNECTION_STRING }}

  # Authentication is handled by GitHub OIDC federated credential
  # (no secrets needed — DefaultAzureCredential auto-resolves the token)
```

**Note**: If you see `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, or `AZURE_TENANT_ID` in your workflows, you can remove them. These are not needed with federated credentials.
  APPLICATIONINSIGHTS_CONNECTION_STRING: ${{ secrets.APPLICATIONINSIGHTS_CONNECTION_STRING }}

```

**Note**: If using **Managed Identity on Azure compute**, you do NOT need to set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, or `AZURE_TENANT_ID` — they are auto-detected.

### Troubleshooting Workflow Failures

**Error: "AZURE_AI_PROJECT_ENDPOINT not set"**

- → Check that secret is defined in repository settings
- → Verify secret value is not empty
- → Try re-saving the secret (sometimes GitHub caches old values)

**Error: "403 Forbidden" or "Principal not authorized" when harvesting or running evaluations**

- → **Root cause**: GitHub federated credential lacks `Azure AI User` role (data plane permissions) on Foundry project
- → **Fix**: Assign `Azure AI User` role to the federated principal on Foundry (see "Authentication & Authorization Architecture" section)
- → **Note**: `Contributor` role (control plane) is NOT sufficient — evaluations require data plane permissions
- → Verify with: `az role assignment list --scope <foundry-project-id> --assignee-object-id <github-principal-id>`

**Error: "No Foundry traces found"**

- → Harvest succeeded but found no sessions (project may be new)
- → Try increasing `--days` lookback in workflow
- → Ensure `AZURE_AI_PROJECT_ENDPOINT` points to correct project

**Workflow times out (> 90 minutes)**

- → Reduce `--limit` in harvest step (default: 100)
- → Reduce evaluator count (start with Phase 1: 5 built-in evaluators)

## Install Dependencies

```bash
# Add evaluation SDK to project dependencies
uv add azure-ai-evaluation
uv sync --all-extras --dev
```

## Run a Local Evaluation (Manual)

```bash
# Run evaluation against gold dataset with built-in evaluators
uv run python -m evaluations \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack \
  --trigger manual

# Run with custom evaluators (Phase 2)
uv run python -m evaluations \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,sql_safety,param_extraction_correctness \
  --trigger manual
```

## Run a Foundry-Native Evaluation

```bash
uv run python -m evaluations \
  --dataset src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack,sql_safety,param_extraction_correctness \
  --trigger nightly \
  --cloud
```

Expected output includes a Foundry Studio link when cloud publish succeeds.

## Run Evaluation in CI (PR Gate)

The PR gate runs automatically via GitHub Actions on pull requests that modify `src/backend/`:

```bash
# Equivalent manual command for the P0 subset
uv run python -m evaluations \
  --dataset src/backend/evaluations/datasets/cadence-eval-p0-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,tool_call_accuracy,indirect_attack \
  --trigger ci_pr \
  --gate
```

The `--gate` flag enforces P0 thresholds and exits non-zero on regression.

## Analyze Failures

```bash
# Generate failure cluster report from the last run
uv run python -m evaluations.analysis \
  --run-id <run-id> \
  --output specs/005-foundry-evaluations/results/
```

## Compare Runs (Delta Report)

```bash
# Compare before/after remediation
uv run python -m evaluations.analysis \
  --compare <before-run-id> <after-run-id> \
  --dataset-version v1
```

## Notes on Cloud Mode

1. Nightly workflow uses `--cloud` so runs can land in Foundry Evaluations.
2. Local summary artifacts are still written to `.foundry/results/` for CI reporting.
3. If cloud publish fails, the runner currently falls back to local evaluation for resiliency.

## Harvest Traces from Foundry and Merge with Gold Dataset

Trace harvesting from Foundry's native traces uses the `harvest` subcommand to automatically query recent sessions and merge results with the gold dataset:

```bash
# Harvest traces from last 7 days and merge with gold dataset
uv run python -m evaluations harvest \
  --output .foundry/datasets \
  --gold src/backend/evaluations/datasets/cadence-eval-gold-v1.jsonl \
  --days 7 \
  --limit 100

# Output: .foundry/datasets/cadence-eval-mixed-v1.jsonl (or incremented version)

# View the merged dataset
cat .foundry/datasets/cadence-eval-mixed-v1.jsonl | jq '.query' | head -10
```

The `harvest` command:

1. Queries Foundry project sessions using `AIProjectClient` (last N days)
2. Extracts user/assistant message pairs from conversation history
3. Applies data sanitization (redacts email, phone, SSN, credit card patterns)
4. Merges with gold dataset using `merge_datasets()` (optional deduplication by query)
5. Produces versioned output: `cadence-eval-mixed-v1.jsonl`, `cadence-eval-mixed-v2.jsonl`, etc.

This mixed dataset strategy combines guaranteed coverage (gold) with production usage patterns (harvested traces), ensuring representative evaluation datasets.

## Run Evaluation on Mixed Dataset

```bash
# Use the merged dataset from above
uv run python -m evaluations run \
  --dataset .foundry/datasets/cadence-eval-mixed-v1.jsonl \
  --evaluators intent_resolution,task_adherence,relevance,sql_safety,param_extraction_correctness \
  --trigger nightly \
  --cloud
```

```bash
# Harvest error traces from last 7 days
uv run python -m evaluations.harvest \
  --type errors \
  --days 7 \
  --output .foundry/datasets/cadence-traces-v1.jsonl
```

## Project Layout

```text
src/backend/
├── evaluations/                      # NEW: Evaluation package
│   ├── __init__.py
│   ├── runner.py                     # Orchestration: dataset load, evaluator init, run, report
│   ├── analysis.py                   # Failure clustering and delta comparison
│   ├── harvest.py                    # Trace-to-dataset pipeline
│   ├── config.py                     # EvaluationConfig, ThresholdRule models
│   ├── models.py                     # EvaluationRun, RunSummary, etc.
│   ├── evaluators/                   # Custom evaluator implementations
│   │   ├── __init__.py
│   │   ├── sql_safety.py             # SQL safety policy evaluator (code-based)
│   │   └── param_extraction.py       # Parameter extraction correctness (code-based)
│   └── datasets/                     # Gold evaluation datasets (source-controlled)
│       └── cadence-eval-gold-v1.jsonl

.github/workflows/
├── eval-pr-gate.yml                  # PR gate: P0 subset evaluation
└── eval-nightly.yml                  # Nightly: full suite evaluation

.foundry/                             # Foundry workspace state (gitignored except metadata)
├── agent-metadata.yaml
├── datasets/                         # Trace-harvested datasets (local cache)
├── evaluators/                       # Evaluator definitions (local cache)
└── results/                          # Run results (local cache)

tests/unit/
├── test_eval_runner.py               # Evaluation runner tests
├── test_eval_sql_safety.py           # SQL safety evaluator tests
├── test_eval_param_extraction.py     # Parameter extraction evaluator tests
└── test_eval_analysis.py             # Failure clustering tests
```
