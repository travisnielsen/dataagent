# Infrastructure & Deployment Guide

This guide covers Azure infrastructure deployment, local development setup, and production deployment for the Enterprise Data Agent.

## Prerequisites

- **Azure Access**: Administrative permissions to an Azure subscription and ability to register applications in Entra ID
- **Development Tools**:
  - Python 3.12+
  - [uv](https://github.com/astral-sh/uv) (Python package manager)
  - Node.js 20+
  - [pnpm](https://pnpm.io/) (Node.js package manager)
  - Azure CLI (`az`)
  - Terraform
  - .NET 8 runtime (for `sqlpackage` — auto-installed by import script on Linux)

## Azure Infrastructure

This repo includes Infrastructure-as-Code (IaC) that deploys a baseline set of services for supporting RAG and NL2SQL scenarios. These services are summarized as follows:

| Service | Module/Resource | Purpose |
| ------- | --------------- | ------- |
| Log Analytics Workspace | `log_analytics` | Centralized logging and monitoring backend for Application Insights and diagnostics |
| Application Insights | `application_insights` | Application performance monitoring, tracing, and telemetry for AI agents |
| Container Registry | `container_registry` | Private container image registry for deploying custom applications |
| Key Vault | `ai_keyvault` | Secure storage for secrets, keys, and certificates used by AI Foundry |
| Storage Account | `ai_storage` | Blob storage for AI Foundry agent file storage and artifacts |
| Cosmos DB | `ai_cosmosdb` | NoSQL database for storing AI agent conversation and session history |
| AI Search | `ai_search` | Vector search service for RAG patterns and semantic search capabilities |
| Microsoft Foundry | `ai_foundry` | Azure AI Foundry hub and project with model deployments (GPT-5, embeddings) |
| Azure SQL Database | `sql_server` | SQL database with Wide World Importers sample data for NL2SQL scenarios |

The IaC is based on Terraform and uses [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/).

> [!IMPORTANT]
> Currently, this repo assumes you have permissions to create resources in an Azure subscription and can configure RBAC roles.

### AI Search Configuration

The Terraform deployment automatically configures AI Search with vector indexes for NL2SQL scenarios:

| Component | Name | Description |
| --------- | ---- | ----------- |
| **Data Sources** | `agentic-tables`, `agentic-query-templates` | Connect to blob storage containers for table schemas and query templates |
| **Indexes** | `tables`, `query_templates` | Vector-enabled indexes with 3072-dimension embeddings using HNSW algorithm |
| **Skillsets** | `table-embed-skill`, `query-template-embed-skill` | Generate embeddings via `text-embedding-3-large` model |
| **Indexers** | `indexer-tables`, `indexer-query-templates` | Process JSON documents and populate vector indexes |

The Search service uses managed identity authentication to access storage and AI Foundry for embedding generation. Sample data is uploaded from the `infra/search-config/` folder during deployment.

### Deploy Infrastructure

#### Entra ID App Registration

This repo supports user-level authentication to the agent API, which supports enterprise security as well as documenting user feedback. The application can be created using: [create-chat-app.ps1](scripts/create-chat-app.ps1). Be sure to sign-into your Entra ID tenant using `az login` first.

#### Azure Services

In the sub-folder you are working from, create a new `terraform.tfvars` file and populate the following variables:

```terraform
subscription_id             = "<your_subscription_id>"
region                      = "<azure_region_name>"
region_aifoundry            = "<azure_region_name>"
frontend_app_client_id      = "<client_id_of_app_registration>"
```

The region you input will depend on model and other resource availabilty. Deployments have been successfully tested in `westus3` and `eastus2`. At the time of this writing, `gpt-5.2-chat` is available for use in `eastus2`.

Open a terminal session and authenticate to your Azure environment via `az login`. Once completed, you can run the following commands to deploy the infrastructure

```terraform
# Download and initialize dependencies
terraform init

# Execute the deployment plan
terraform plan

# Deploy resources
terraform apply
```

#### Private Networking Services

Use the private networking stack to deploy a dedicated VNet foundation, subnets,
private DNS zones, and optional private endpoints to existing Azure resources.

From the repo root:

```bash
cd infra/terraform

# Create local variables file from starter template (or edit directly)
cp terraform.tfvars.example terraform.tfvars

# Download and initialize dependencies
terraform init

# Validate configuration before planning
terraform validate

# Execute the deployment plan
terraform plan

# Deploy resources
terraform apply
```

At minimum, set these values in `infra/terraform/terraform.tfvars`:

```terraform
subscription_id          = "<your_subscription_id>"
region                   = "<azure_region_name>"
region_aifoundry         = "<azure_region_name>"
region_search            = "<azure_region_name>"
frontend_app_client_id   = "<client_id_of_app_registration>"
enable_local_exec_provisioning = false
```

Starter values are available in `infra/terraform/terraform.tfvars` and
`infra/terraform/terraform.tfvars.example`. The `terraform.tfvars` file
is intentionally ignored by git for environment-specific and potentially sensitive values.

> **Note:** `private_endpoints` are optional. You can deploy only network and DNS
> foundation first, then add private endpoint entries later once target resource IDs are known.

For private data-plane seeding and hardened GitHub runner authentication prerequisites
(GitHub App recommended, PAT fallback), see
`infra/terraform/README.md` under "Phase 2: Private Runner Provisioning Model".
That Phase 2 flow now includes private AI Search data-plane configuration in addition
to storage uploads and SQL import.

### SQL Database Import

After deploying infrastructure, import the Wide World Importers sample data into Azure SQL. The import script automatically installs required dependencies (`sqlpackage`, `.NET 8 runtime`) if they are missing.

```powershell
cd scripts

# Get the SQL server name and resource group from Terraform
$SqlServer = (cd ../infra/terraform && terraform output -raw sql_server_name)
$RG = (cd ../infra/terraform && terraform output -raw resource_group_name)

# Import sample data (5-10 minutes)
./import-wideworldimporters.ps1 -SqlServerName $SqlServer -ResourceGroup $RG
```

Or with explicit values:

```powershell
./import-wideworldimporters.ps1 -SqlServerName "ay2q3p-sql" -ResourceGroup "cadence-ay2q3p"
```

> **Note:** Requires `az login` with a user that has SQL Server admin permissions. On Linux, the script may prompt for `sudo` to install the .NET 8 runtime if not present.

### SQL Database User Setup

After deploying the infrastructure, create a contained database user in SQL Server to allow the API's managed identity to authenticate. This is a one-time setup step.

Run the setup script (PowerShell):

```powershell
cd scripts

# Get the values from Terraform state
$SqlServer = (cd ../infra/terraform && terraform output -raw sql_server_name)
$IdentityName = (cd ../infra/terraform && terraform output -raw container_app_identity_name)

# Run the PowerShell script
./setup-sql-user.ps1 -SqlServerName $SqlServer -DatabaseName "WideWorldImportersStd" -IdentityName $IdentityName
```

Or with explicit values:

```powershell
cd scripts
./setup-sql-user.ps1 -SqlServerName "ay2q3p-sql" -DatabaseName "WideWorldImportersStd" -IdentityName "ay2q3p-api-identity"
```

The script creates the managed identity as a database user and grants **db_datareader** and **db_datawriter** roles.

> **Note:** You must be logged in with `az login` as the SQL Server Entra ID admin. The script requires the PowerShell `SqlServer` module (auto-installs if missing).

## Local Development

### Install Dependencies

Install frontend dependencies:

```bash
cd src/frontend
pnpm install
```

Install backend dependencies:

```bash
./devsetup.sh
```

### Environment Variables - API

Create an `.env` file inside `src/backend/`. Copy the contents of [.env.example](../src/backend/.env.example) into `.env` and update the values to match your environment.

> [!IMPORTANT]
> The Entra ID section is optional. When these environment variables are set, the API will require a valid token issued by one of the approved source tenants with the correct target scope. If you don't require user-level authorization to the API, you can omit these.

### Environment Variables - Frontend

Create a `.env.local` file within `src/frontend/`. Use [.env.example](../src/frontend/.env.example) as a reference:

```env
NEXT_PUBLIC_AZURE_AD_CLIENT_ID=your-client-id-here
NEXT_PUBLIC_AZURE_AD_TENANT_IDS=tenant-id-1,tenant-id-2
```

### Start Development Server

From the `src/frontend/` directory:

```bash
cd src/frontend
pnpm dev
```

This starts both the UI and the FastAPI backend concurrently.

### Available Scripts

| Script | Description |
| ------ | ----------- |
| `dev` | Starts both UI and agent servers in development mode |
| `dev:debug` | Starts development servers with debug logging enabled |
| `dev:ui` | Starts only the Next.js UI server |
| `dev:agent` | Starts only the Microsoft Agent Framework server |
| `build` | Builds the Next.js application for production |
| `start` | Starts the production server |
| `lint` | Runs ESLint for code linting |
| `install:agent` | Installs Python dependencies for the agent |

### DevUI for Testing

The Microsoft Agent Framework includes a development UI for testing and debugging agents and workflows:

```bash
cd src/backend
source .venv/bin/activate
devui ./entities
```

DevUI auto-discovers agents and workflows in the `entities` directory, providing an interactive interface for testing individual components (`nl2sql_controller`, `orchestrator`, `parameter_extractor`) or the full `workflow`.

### Telemetry

The application supports OpenTelemetry for observability. Add these environment variables to your `backend/.env` file:

```env
# Enable OpenTelemetry instrumentation
ENABLE_INSTRUMENTATION=true

# Option 1: Azure Monitor (production)
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...

# Option 2: OTLP exporters (local development with Aspire Dashboard, Jaeger, etc.)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Optional: Enable console output for debugging
ENABLE_CONSOLE_EXPORTERS=true

# Optional: Log prompts and responses (use with caution - contains sensitive data)
ENABLE_SENSITIVE_DATA=true
```

Install the required telemetry packages:

```bash
cd src/backend
uv pip install -e ".[observability]"
```

## Continuous Deployment - GitHub Actions

The frontend and API are automatically deployed via GitHub Actions when changes are pushed to the `main` branch:

- **Frontend**: Changes to `src/frontend/` trigger deployment to Azure Static Web Apps (SWA)
- **API**: Changes to `src/backend/` trigger a Docker image build, push to Azure Container Registry, and deployment to Azure Container Apps

### CD Prerequisites

To enable continueous deployment, run [setup-github-actions.ps1](/scripts/setup-github-actions.ps1) to configure an Entra ID App Registration with federated credentials with GitHub and grant deployment permissions to the resource group.

```powershell
./setup-github-actions.ps1
```

Document the App ID provided at the end of the script. You will need to set this value in the GitHub Actions variable: `AZURE_CLIENT_ID`.

Next, navigate to your repository's **Settings → Secrets and variables → Actions → Variables** and add the following:

| Variable | Description |
| -------- | ----------- |
| `AZURE_CLIENT_ID` | App registration client ID from step 1 |
| `AZURE_TENANT_ID` | Your Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |
| `AZURE_STATIC_WEB_APP_NAME` | Static Web App resource name (run `terraform output static_web_app_name`) |
| `AZURE_STORAGE_ACCOUNT` | Storage account name for private data-plane assets (run `terraform output storage_account_name`) |
| `AZURE_CONTAINER_REGISTRY` | Container Registry name without `.azurecr.io` (run `terraform output container_registry_login_server`) |
| `AZURE_CONTAINER_APP_NAME` | Container App name (run `terraform output container_app_url` to identify) |
| `AZURE_RESOURCE_GROUP` | Resource group name containing the Container App |
| `NEXT_PUBLIC_API_URL` | Backend API URL (e.g., `https://[your_instance].eastus2.azurecontainerapps.io`) |
| `NEXT_PUBLIC_AZURE_AD_CLIENT_ID` | Frontend app registration client ID for authentication |
| `NEXT_PUBLIC_AZURE_AD_TENANT_IDS` | Comma-separated Azure AD tenant IDs approved for authentication |

### Workflow Trigger

The GitHub Actions workflow (`.github/workflows/cd-frontend.yml`) triggers on:

- Push to `main` branch with changes in `frontend/**`
- Manual dispatch via GitHub Actions UI

### Redirect URI Configuration

>[!IMPORTANT]
>Regardless of how the frontend is deployed, once you have the frontend URL, you will need to update the app registration to include the URL as a Redirect URI. In Entra ID, this is done in the **Authentication** section of the App Registration.

## Manual Deployment

### Chat Client Deployment to Azure Static Webapp

To manually deploy the frontend:

```bash
cd src/frontend

# Install dependencies and build
pnpm install
pnpm build

# Deploy to Azure Static Web Apps
az staticwebapp secrets list \
  --name <STATIC_WEB_APP_NAME> \
  --resource-group <RESOURCE_GROUP>

# Then deploy using the GitHub workflow or Azure Static Web Apps deploy action.
```

### FastAPI Deployment to Azure Container Apps

#### Build the Container Image for local testing (optional)

From the repo root, build the Docker image:

```bash
# Build the image (context is repo root)
docker build -f src/backend/Dockerfile -t cadence-api .
```

To test the container locally before pushing:

```bash
# Run with environment variables from .env file
docker run -p 8000:8000 --env-file src/backend/.env cadence-api
```

The API will be available at `http://localhost:8000`. Verify it's running by checking the health endpoint: `http://localhost:8000/health`

#### Build and Push to Azure Container Registry

Follow these steps to build and push the container image to Azure.

>[!NOTE]
>Using `az acr build` builds the image on Azure's infrastructure, avoiding architecture mismatches that can occur when building locally on ARM-based machines (e.g., Apple Silicon, Windows Arm, etc...).

1. **Get the ACR name from Terraform:**

```bash
cd infra/terraform
terraform output container_registry_login_server
```

1. **Build and push using ACR Build:**

```bash
# Build in Azure and push to ACR (replace <acr_name> with your registry name)
az acr build --registry <acr_name> --image cadence-api:latest --platform linux/amd64 -f src/backend/Dockerfile .
```

### Example

```bash
# Full example with actual registry name
az acr build --registry ay2q3pacr --image cadence-api:latest --platform linux/amd64 -f src/backend/Dockerfile .
```

After the image is updated and you are using the `latest` tag, you can update the Container App by running:

```bash
az containerapp update --name [container_app_name] --resource-group [resource_group_name] --image [container_registry_name].azurecr.io/cadence-api:latest
```
