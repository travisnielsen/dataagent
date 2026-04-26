# Private Networking Terraform

This stack creates a private networking foundation plus private service deployment for Cadence under `infra/terraform`.

## What It Deploys

- Resource Group
- Virtual Network with 5 subnets:
  - `private-endpoints` (Private Endpoints for PaaS services)
  - `application` (general app-tier workloads)
  - `container-apps` (delegated to `Microsoft.App/environments` for Container Apps)
  - `ai-agent-services` (AI Foundry Agent Service network injection)
  - `data` (data-tier workloads)
- Network Security Groups for `application` and `data` subnets
- Private DNS zones and VNet links
- Optional private endpoints to existing Azure services
- AVM-based service deployment cloned from public networking with private access enforced:
  - Container Registry
  - Storage Account (NL2SQL blobs)
  - Cosmos DB
  - AI Search
  - AI Foundry (private endpoints enabled with public access also enabled)
  - SQL Server + database
  - Container Apps environment and API app (VNet injected)

## Inputs

1. Copy `terraform.tfvars.example` to `terraform.tfvars`.
2. Update required values:
   - `subscription_id`

- `frontend_app_client_id`

Commonly customized values:

- `region`, `region_aifoundry`, `region_search`
- `resource_group_name` (optional override)
- subnet CIDR variables (`*_subnet_cidr`)
- `acr_build_mode` (`public` default for cost optimization, `private` for network-isolated ACR builds)
- `tags`

### ACR Build Mode

`acr_build_mode` controls both ACR network exposure and ACR Tasks execution path:

- `public` (default):
  - ACR public network access is enabled
  - Private ACR agent pool is removed
  - New image builds use Azure-hosted ACR Tasks
- `private`:
  - ACR public network access is blocked
  - Private ACR agent pool is created/maintained
  - New image builds use `az acr build --agent-pool <pool>`

Use `public` unless you explicitly require private-network build execution.

For GitHub Actions OIDC/federated access (recommended for private runner and standard CI/CD):

1. Set `github_federated_principal_object_id` to your existing GitHub federated service principal object ID.
2. Set `github_federated_principal_client_id` to the same principal client ID (app ID).
3. Optionally set SQL Entra admin explicitly with `sql_azuread_admin_object_id` and `sql_azuread_admin_login_username`.

If you use an Entra group for SQL admin (recommended), ensure the federated principal is a member of that group.

### SQL Entra Admin Group Prerequisite

Before running `terraform apply`, create (or verify) an Entra group to act as the SQL
Entra administrator and include both:

- Your human admin user account
- The GitHub federated service principal used by workflows

Use the helper script:

```bash
bash ./infra/scripts/ensure-sql-admin-group.sh
```

The script is idempotent and prints the exact values to set in
`infra/terraform/terraform.tfvars`:

- `sql_azuread_admin_object_id`
- `sql_azuread_admin_login_username`

You can also pass explicit values:

```bash
bash ./infra/scripts/ensure-sql-admin-group.sh \
  cadence-sql-admins \
  <your-user-object-id> \
  <github-federated-principal-object-id-or-app-id>
```

Terraform grants this federated principal the required roles for this stack:

- `Storage Blob Data Contributor` on the NL2SQL storage account
- `Search Service Contributor` on AI Search
- `SQL DB Contributor` on SQL server
- `AcrPush` on Container Registry

Terraform also grants the AI Search managed identity the required roles for indexer embedding pipeline:

- `Storage Blob Data Reader` on storage
- `Cognitive Services OpenAI User` on AI Foundry account

AI Search is configured with controlled public access for operations/troubleshooting:

- `network_rule_bypass_option = "AzureServices"` (trusted Azure services)
- Public allowlist includes the deployer's current public IP address at apply time (`/32`)

This stack creates an AI Search Shared Private Link to Storage Blob for indexer
document ingestion when the storage account is private-only
(`public_network_access_enabled = false`).

Important limitation: in this current Search service environment, shared private
links support only storage/sql/key vault target group IDs (`blob`, `table`, `dfs`,
`file`, `Sql`, `sqlServer`, `vault`). A shared private link from Search to
AI Foundry/OpenAI is not currently supported here.

If embedding skills call an OpenAI endpoint with public access disabled, indexer
skill execution can fail with 403. In that case, use one of these options:

- Allow trusted/public network path for the OpenAI endpoint used by Search skills.
- Precompute embeddings outside Search indexers and push vectors directly.

Note: shared private link creation can still require target-side approval depending on the
resource provider and policy in your tenant/subscription. After `terraform apply`, verify
the link status from Terraform outputs and approve any pending connection on the Storage
account private endpoint connections blade if required.

For remote Terraform state in Azure Storage (recommended for multi-machine workflows):

1. Copy `backend.hcl.example` to `backend.hcl`.
2. Set `storage_account_name` in `backend.hcl`.
3. Initialize with:

```bash
terraform init -reconfigure -backend-config=backend.hcl
```

Optionally add `private_endpoints` entries for existing resources.

If running from outside the private network, keep `enable_local_exec_provisioning = false`.
Set it to `true` only when Terraform execution has network access to private data-plane endpoints for SQL import and Search index/data provisioning.

## Phase 2: Private Runner Provisioning Model

This repo uses a two-phase deployment model for private data-plane work:

1. Phase 1 (environment provisioning): deploy infrastructure with Terraform.
2. Phase 2 (private data provisioning): run one-time seeding from a private GitHub runner.

### One-Time Workflows

- `.github/workflows/bootstrap-private-runner.yml`
  - Builds a hardened GitHub runner container image in ACR.
  - Creates a managed identity with `AcrPull`.
  - Creates an event-driven Azure Container Apps Job using the `github-runner` scaler.
  - Configures ephemeral runners (`--ephemeral`) with label-based routing.
- `.github/workflows/provision-private-data.yml`
  - Runs on the private self-hosted runner (`runs-on: [self-hosted, linux, x64, cadence-private]`).
  - Uses the same GitHub OIDC federated principal via `azure/login` (no extra runtime managed identity required for data-plane workflow auth).
  - Runs a variable presence audit and preflight role validation before mutating storage/search/sql resources.
  - Uploads NL2SQL data files to private blob storage.
  - Configures AI Search datasources, indexes, skillsets, and indexers via private data-plane calls.
  - Imports WideWorldImporters into private SQL.
  - Ensures SQL read access via Entra group for the API user-assigned identity via `setup-sql-reader-group.ps1`.

### Workflow Variable Audit Output

Both bootstrap and private data workflows print a presence-only variable audit step before execution.

- Output format is `PRESENT [required|optional|mode] VARIABLE_NAME` or `MISSING [...] VARIABLE_NAME`.
- Secrets and variable values are never printed.
- For bootstrap, auth mode is still selected by workflow logic after the audit.
- For private data provisioning, missing required variables fail fast in the validation step.

### Regular CI/CD

The API and frontend CD workflows run on the private self-hosted runner label set:

- `runs-on: [self-hosted, linux, x64, cadence-private]`

Frontend hosting is deployed to Azure Static Web Apps (SWA). This avoids relying on
Azure Storage static website public endpoint exposure.

### Required GitHub Configuration

`GH_RUNNER_PAT` is **not required** when GitHub App auth is configured.
Bootstrap selects auth in this order:

1. GitHub App mode when `GH_RUNNER_APP_ID`, `GH_RUNNER_INSTALLATION_ID`, and `GH_RUNNER_APP_PRIVATE_KEY` are set.
2. PAT mode only when GitHub App credentials are not set and `GH_RUNNER_PAT` is available.
3. Bootstrap fails fast if neither mode is fully configured.

Repository variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINER_REGISTRY`
- `AZURE_CONTAINER_APP_ENVIRONMENT`
- `AZURE_STATIC_WEB_APP_NAME`
- `AZURE_STORAGE_ACCOUNT`
- `AZURE_SQL_SERVER_NAME` (server name without `.database.windows.net`)
- Optional: `AZURE_API_IDENTITY_NAME` (API user-assigned identity name; auto-discovery fallback is used when omitted)
- Optional: `AZURE_SQL_READER_GROUP_NAME` (defaults to `cadence-sql-readers`)
- `AZURE_SEARCH_SERVICE_NAME`
- `AZURE_AI_FOUNDRY_ACCOUNT_NAME`
- Optional: `TF_STATE_STORAGE_ACCOUNT` (used only when `run_terraform_init=true`)
- Optional: `AZURE_SQL_DATABASE_NAME`
- Optional: `AZURE_GH_RUNNER_IDENTITY_NAME`
- Optional: `GH_RUNNER_REPO_OWNER`
- Optional: `GH_RUNNER_REPO_NAME`
- Required for GitHub App auth: `GH_RUNNER_APP_ID`
- Required for GitHub App auth: `GH_RUNNER_INSTALLATION_ID`

Repository secrets:

- Required for GitHub App auth: `GH_RUNNER_APP_PRIVATE_KEY` (GitHub App PEM private key)
- Required only for PAT fallback: `GH_RUNNER_PAT` (fine-grained PAT for runner registration and scaler polling)

Mode-specific minimum inputs for bootstrap:

- GitHub App mode (recommended):
  - Variables: `GH_RUNNER_APP_ID`, `GH_RUNNER_INSTALLATION_ID`
  - Secrets: `GH_RUNNER_APP_PRIVATE_KEY`
- PAT fallback mode:
  - Secrets: `GH_RUNNER_PAT`

`bootstrap-private-runner.yml` automatically prefers GitHub App auth when
`GH_RUNNER_APP_ID`, `GH_RUNNER_INSTALLATION_ID`, and `GH_RUNNER_APP_PRIVATE_KEY`
are present. If not set, it falls back to PAT mode.

### GitHub App Prerequisites (Recommended)

Use this setup when possible. It provides tighter security and better API rate limits than PAT mode.

1. Create a GitHub App in GitHub Settings → Developer settings → GitHub Apps.

1. Disable webhooks for this app (not required for this scaler pattern).

1. Configure repository permissions on the app.

- Actions: Read-only
- Administration: Read and write
- Metadata: Read-only

1. If using organization scope runners, also configure organization-level self-hosted runner permissions.

1. Install the app to the target repository (or organization if using org scope).

1. Save the App ID as repo variable `GH_RUNNER_APP_ID`.

1. Save the Installation ID as repo variable `GH_RUNNER_INSTALLATION_ID`.

- UI method: open your app installation page and copy the numeric ID from the URL, for example `https://github.com/settings/installations/12345678`.
- API method: `GET https://api.github.com/repos/<owner>/<repo>/installation` and use the `id` field from the response.

1. Generate a private key in the GitHub App and store the PEM content as repo secret `GH_RUNNER_APP_PRIVATE_KEY`.

### PAT Fallback Prerequisites (Fallback Only)

If GitHub App setup is not ready, use a fine-grained PAT in `GH_RUNNER_PAT` with:

- Repository access limited to the target repo(s).
- Repository permission Actions: Read-only
- Repository permission Administration: Read and write
- Repository permission Metadata: Read-only

Rotate this token regularly.

### Auth Mode Selection Behavior

In `.github/workflows/bootstrap-private-runner.yml`:

1. GitHub App mode is selected when all of the following exist:

- `GH_RUNNER_APP_ID`
- `GH_RUNNER_INSTALLATION_ID`
- `GH_RUNNER_APP_PRIVATE_KEY`

1. Otherwise, PAT mode is selected when `GH_RUNNER_PAT` exists.

1. If neither is configured, bootstrap fails fast.

The workflow prints the selected mode and emits a warning when PAT fallback is used.

## Commands

```bash
cd infra/terraform
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```

### Populate Repository Variables From Terraform Outputs

After provisioning, you can use Terraform outputs from this stack to populate GitHub repository variables.

Suggested mapping:

- `AZURE_RESOURCE_GROUP` <- output `resource_group_name`
- `AZURE_LOCATION` <- output `azure_location`
- `AZURE_CONTAINER_REGISTRY` <- output `container_registry_name`
- `ACR_BUILD_MODE` <- output `acr_build_mode`
- `AZURE_ACR_AGENT_POOL` <- output `acr_agent_pool_name` (private mode only)
- `AZURE_CONTAINER_APP_ENVIRONMENT` <- output `container_app_environment_name`
- `AZURE_CONTAINER_APP_NAME` <- output `container_app_name`
- `NEXT_PUBLIC_API_URL` <- output `container_app_url`
- `AZURE_STATIC_WEB_APP_NAME` <- output `static_web_app_name`
- `AZURE_STORAGE_ACCOUNT` <- output `storage_account_name`
- `AZURE_SQL_SERVER_NAME` <- output `sql_server_name`
- `AZURE_SQL_DATABASE_NAME` <- output `sql_database_name`
- `AZURE_API_IDENTITY_NAME` <- output `container_app_identity_name`
- `AZURE_SEARCH_SERVICE_NAME` <- output `search_service_name`
- `AZURE_AI_FOUNDRY_ACCOUNT_NAME` <- output `ai_foundry_account_name`
- `AZURE_SUBSCRIPTION_ID` <- output `azure_subscription_id`
- `AZURE_TENANT_ID` <- output `azure_tenant_id`

Note: `AZURE_CLIENT_ID`, `GH_RUNNER_APP_ID`, and `GH_RUNNER_INSTALLATION_ID` are identity/app registration values and are not Terraform resource outputs.

Print helper command:

```bash
bash ./infra/scripts/print-github-vars-from-terraform.sh
```

This prints `KEY=value` lines for repository variables sourced from Terraform outputs, plus placeholders for values that must be set manually.
In `public` mode, it reports `AZURE_ACR_AGENT_POOL` as not required.

To update repository variables directly via `gh` CLI (safe dry-run by default):

```bash
bash ./infra/scripts/update-github-vars-from-terraform.sh
```

Apply changes to the current repository:

```bash
bash ./infra/scripts/update-github-vars-from-terraform.sh --apply
```

In `public` mode, this script removes `AZURE_ACR_AGENT_POOL` from repository variables to prevent stale private-mode configuration.

Apply changes to a specific repository:

```bash
bash ./infra/scripts/update-github-vars-from-terraform.sh --repo <owner>/<repo> --apply
```

Prerequisites:

- `gh auth login` has been completed.
- Terraform is initialized in `infra/terraform` (`terraform init`).
- Manual variables remain required: `AZURE_CLIENT_ID`, `GH_RUNNER_APP_ID`, and `GH_RUNNER_INSTALLATION_ID`.

### Non-Interactive Init for GitHub Runner / CI

Use the helper script to avoid interactive prompts when running on a private runner:

```bash
cd infra/terraform
TF_STATE_STORAGE_ACCOUNT=<your_tf_state_storage_account> ./init-remote-state.sh
```

You can also pass the state account as an argument and include extra init flags:

```bash
./init-remote-state.sh <your_tf_state_storage_account> -migrate-state
```

For GitHub Actions, add a repo variable like `TF_STATE_STORAGE_ACCOUNT` and run:

```bash
terraform -chdir=infra/terraform init -reconfigure \
  -backend-config="resource_group_name=rg-terraform-state" \
  -backend-config="storage_account_name=${TF_STATE_STORAGE_ACCOUNT}" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=cadence-private-networking.terraform.tfstate" \
  -backend-config="use_azuread_auth=true"
```

In `.github/workflows/provision-private-data.yml`, Terraform backend init is optional and controlled by workflow input `run_terraform_init`.

## Notes

- Private endpoints are optional. If `private_endpoints` is empty, only network and DNS foundations are created.
- Each private endpoint `dns_zone_name` must be present in `private_dns_zone_names`.
