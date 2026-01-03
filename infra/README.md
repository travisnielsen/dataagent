# Azure Infrastructure Deployment

This repo includes Infrastructure-as-Code (IaC) that deploys a baseline set of services for supporting RAG and NL2SQL scenarios. These services are summarized as follows:

| Service | Module/Resource | Purpose |
|---------|-----------------|---------|
| Log Analytics Workspace | `log_analytics` | Centralized logging and monitoring backend for Application Insights and diagnostics |
| Application Insights | `application_insights` | Application performance monitoring, tracing, and telemetry for AI agents |
| Container Registry | `container_registry` | Private container image registry for deploying custom applications |
| Key Vault | `ai_keyvault` | Secure storage for secrets, keys, and certificates used by AI Foundry |
| Storage Account | `ai_storage` | Blob storage for AI Foundry agent file storage and artifacts |
| Cosmos DB | `ai_cosmosdb` | NoSQL database for storing AI agent threads and conversation history |
| AI Search | `ai_search` | Vector search service for RAG patterns and semantic search capabilities |
| Microsoft Foundry | `ai_foundry` | Azure AI Foundry hub and project with model deployments (GPT-5, embeddings) |
| Azure SQL Database | `sql_server` | SQL database with Wide World Importers sample data for NL2SQL scenarios |

The IaC is based on Terraform and uses [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/).

> [!IMPORTANT]
> Currently, this repo assumes you have permissiones to create resources in an Azure subscription and can configure RBAC roles.

## AI Search Configuration

The Terraform deployment automatically configures AI Search with vector indexes for NL2SQL scenarios:

| Component | Name | Description |
|-----------|------|-------------|
| **Data Sources** | `agentic-queries`, `agentic-tables` | Connect to blob storage containers for query examples and table schemas |
| **Indexes** | `queries`, `tables` | Vector-enabled indexes with 3072-dimension embeddings using HNSW algorithm |
| **Skillsets** | `query-embed-skill`, `table-embed-skill` | Generate embeddings via `text-embedding-3-large` model |
| **Indexers** | `indexer-queries`, `indexer-tables` | Process JSON documents and populate vector indexes |

The Search service uses managed identity authentication to access storage and AI Foundry for embedding generation. Sample data is uploaded from the `search-config/` folder during deployment.

## Deployment (Infrastructure)

In the sub-folder you are working from, create a new `terraform.tfvars` file and populate the following variables:

```terraform
subscription_id     = "<your_subscription_id>"
region              = "<azure_region_name>"
region_aifoundry    = "<azure_region_name>"
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

## Deployment - Frontend

The frontend is automatically deployed to Azure Static Website (blob storage) via GitHub Actions when changes are pushed to the `main` branch affecting the `frontend/` folder.

### Prerequisites

1. **Create Azure AD App Registration for GitHub Actions OIDC:**

```bash
# Create app registration
az ad app create --display-name "github-actions-dataagent"

# Get the app ID
APP_ID=$(az ad app list --display-name "github-actions-dataagent" --query "[0].appId" -o tsv)

# Create service principal
az ad sp create --id $APP_ID

# Create federated credential for GitHub Actions
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-main-branch",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:travisnielsen/dataagent:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Grant Storage Blob Data Contributor role to the storage account
az role assignment create \
  --assignee $APP_ID \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.Storage/storageAccounts/<STORAGE_ACCOUNT>"

# Grant Storage Account Contributor role (required to modify network rules during deployment)
az role assignment create \
  --assignee $APP_ID \
  --role "Storage Account Contributor" \
  --scope "/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.Storage/storageAccounts/<STORAGE_ACCOUNT>"
```

2. **Configure GitHub Repository Variables:**

Navigate to your repository's **Settings → Secrets and variables → Actions → Variables** and add the following:

| Variable | Description |
|----------|-------------|
| `AZURE_CLIENT_ID` | App registration client ID from step 1 |
| `AZURE_TENANT_ID` | Your Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |
| `AZURE_STORAGE_ACCOUNT` | Storage account name (run `terraform output static_website_url` to get the account name) |
| `NEXT_PUBLIC_API_URL` | Backend API URL (e.g., `https://your-api.azurewebsites.net`) |
| `NEXT_PUBLIC_AZURE_AD_CLIENT_ID` | Frontend app registration client ID for authentication |
| `NEXT_PUBLIC_AZURE_AD_TENANT_ID` | Azure AD tenant ID for authentication |

### Manual Deployment

To manually deploy the frontend:

```bash
cd frontend

# Install dependencies and build
pnpm install
pnpm build

# Deploy to Azure Static Website
az storage blob upload-batch \
  --account-name <STORAGE_ACCOUNT> \
  --destination '$web' \
  --source out/ \
  --overwrite \
  --auth-mode login
```

### Workflow Trigger

The GitHub Actions workflow (`.github/workflows/deploy-frontend.yml`) triggers on:
- Push to `main` branch with changes in `frontend/**`
- Manual dispatch via GitHub Actions UI

## Deployment - API

The API is containerized and deployed to Azure Container Apps. Follow these steps to build and push the container image.

### Prerequisites

- Docker installed and running
- Azure CLI authenticated (`az login`)
- Access to the Azure Container Registry deployed by Terraform

### Build the Container Image

From the `api/` directory, build the Docker image:

```bash
cd api

# Build the image
docker build -t dataagent-api .
```

### Run Locally (Optional)

To test the container locally before pushing:

```bash
# Run with environment variables from .env file
docker run -p 8000:8000 --env-file .env dataagent-api
```

The API will be available at `http://localhost:8000`. Verify it's running by checking the health endpoint: `http://localhost:8000/health`

### Build and Push to Azure Container Registry

Use `az acr build` to build the container image directly in Azure. This ensures the image is built for the correct platform (linux/amd64) regardless of your local machine's architecture.

1. **Get the ACR name from Terraform:**

```bash
cd infra/public-networking
terraform output container_registry_login_server
```

2. **Build and push using ACR Build:**

```bash
cd api

# Build in Azure and push to ACR (replace <acr_name> with your registry name)
az acr build --registry <acr_name> --image dataagent-api:latest --platform linux/amd64 .
```

### Example

```bash
# Full example with actual registry name
cd api
az acr build --registry ay2q3pacr --image dataagent-api:latest --platform linux/amd64 .
```

> **Note:** Using `az acr build` builds the image on Azure's infrastructure, avoiding architecture mismatches that can occur when building locally on ARM-based machines (e.g., Apple Silicon Macs).

