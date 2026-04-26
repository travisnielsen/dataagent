#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$(cd -- "$SCRIPT_DIR/../terraform" && pwd)"

if [[ ! -f "$TERRAFORM_DIR/outputs.tf" ]]; then
  echo "Could not find Terraform directory: $TERRAFORM_DIR"
  exit 1
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is required but was not found in PATH"
  exit 1
fi

if [[ ! -d "$TERRAFORM_DIR/.terraform" ]]; then
  echo "Terraform is not initialized in $TERRAFORM_DIR"
  echo "Run: cd infra/terraform && terraform init"
  exit 1
fi

OUTPUTS_JSON="$(terraform -chdir="$TERRAFORM_DIR" output -json 2>/dev/null || true)"

if [[ -z "$OUTPUTS_JSON" ]]; then
  OUTPUTS_JSON="{}"
fi

export OUTPUTS_JSON

read_output() {
  local output_name="$1"
  python - "$output_name" <<'PY'
import json
import os
import sys

name = sys.argv[1]
raw = os.environ.get("OUTPUTS_JSON", "{}")

try:
    data = json.loads(raw)
except Exception:
    data = {}

entry = data.get(name)
if not entry:
    print("")
    raise SystemExit(0)

value = entry.get("value")
if value is None:
    print("")
elif isinstance(value, str):
    print(value)
else:
    print(json.dumps(value, separators=(",", ":"), ensure_ascii=True))
PY
}

print_assignment() {
  local var_name="$1"
  local output_name="$2"
  local value

  value="$(read_output "$output_name")"
  if [[ -n "$value" ]]; then
    printf '%s=%s\n' "$var_name" "$value"
  else
    printf '%s=<missing terraform output: %s>\n' "$var_name" "$output_name"
  fi
}

echo "# GitHub repository variables from Terraform outputs"
echo "# Source: infra/terraform"
echo

acr_mode="$(read_output "acr_build_mode")"
acr_pool="$(read_output "acr_agent_pool_name")"

if [[ -z "$acr_mode" ]]; then
  if [[ -n "$acr_pool" ]]; then
    acr_mode="private"
  else
    acr_mode="public"
  fi
fi

print_assignment "AZURE_RESOURCE_GROUP" "resource_group_name"
print_assignment "AZURE_LOCATION" "azure_location"
print_assignment "AZURE_CONTAINER_REGISTRY" "container_registry_name"
printf 'ACR_BUILD_MODE=%s\n' "$acr_mode"
if [[ "$acr_mode" == "private" ]]; then
  print_assignment "AZURE_ACR_AGENT_POOL" "acr_agent_pool_name"
else
  echo "AZURE_ACR_AGENT_POOL=<not required in public mode>"
fi
print_assignment "AZURE_CONTAINER_APP_ENVIRONMENT" "container_app_environment_name"
print_assignment "AZURE_CONTAINER_APP_NAME" "container_app_name"
print_assignment "NEXT_PUBLIC_API_URL" "container_app_url"
print_assignment "AZURE_STATIC_WEB_APP_NAME" "static_web_app_name"
print_assignment "AZURE_STORAGE_ACCOUNT" "storage_account_name"
print_assignment "AZURE_SQL_SERVER_NAME" "sql_server_name"
print_assignment "AZURE_SQL_DATABASE_NAME" "sql_database_name"
print_assignment "AZURE_API_IDENTITY_NAME" "container_app_identity_name"
print_assignment "AZURE_SEARCH_SERVICE_NAME" "search_service_name"
print_assignment "AZURE_AI_FOUNDRY_ACCOUNT_NAME" "ai_foundry_account_name"
print_assignment "AZURE_SUBSCRIPTION_ID" "azure_subscription_id"
print_assignment "AZURE_TENANT_ID" "azure_tenant_id"

echo
echo "# Not Terraform outputs (set manually):"
echo "AZURE_CLIENT_ID=<federated app/client id>"
echo "GH_RUNNER_APP_ID=<github app id>"
echo "GH_RUNNER_INSTALLATION_ID=<github app installation id>"
echo "TF_STATE_STORAGE_ACCOUNT=<tfstate storage account name, optional>"
echo "AZURE_GH_RUNNER_IDENTITY_NAME=<optional runner identity name>"
echo "GH_RUNNER_REPO_OWNER=<optional, defaults to repository owner>"
echo "GH_RUNNER_REPO_NAME=<optional, defaults to repository name>"
