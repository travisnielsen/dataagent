#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$(cd -- "$SCRIPT_DIR/../terraform" && pwd)"

APPLY_MODE=false
REPO=""

usage() {
  cat <<'EOF'
Usage: update-github-vars-from-terraform.sh [--apply] [--repo owner/name]

Options:
  --apply            Actually write repository variables via gh CLI.
                     Default is dry-run (print what would change).
  --repo owner/name  Target repository. Defaults to the current gh repo.
  -h, --help         Show this help.

Reads Terraform outputs from infra/terraform and maps them to GitHub
repository variables.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY_MODE=true
      shift
      ;;
    --repo)
      REPO="${2:-}"
      if [[ -z "$REPO" ]]; then
        echo "--repo requires a value like owner/name"
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "$TERRAFORM_DIR/outputs.tf" ]]; then
  echo "Could not find Terraform directory: $TERRAFORM_DIR"
  exit 1
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is required but was not found in PATH"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required but was not found in PATH"
  exit 1
fi

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required but was not found in PATH"
  exit 1
fi

if [[ ! -d "$TERRAFORM_DIR/.terraform" ]]; then
  echo "Terraform is not initialized in $TERRAFORM_DIR"
  echo "Run: cd infra/terraform && terraform init"
  exit 1
fi

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [[ -z "$REPO" ]]; then
    echo "Could not determine repository from gh CLI. Use --repo owner/name."
    exit 1
  fi
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh CLI is not authenticated. Run: gh auth login"
  exit 1
fi

OUTPUTS_JSON="$(terraform -chdir="$TERRAFORM_DIR" output -json 2>/dev/null || true)"
if [[ -z "$OUTPUTS_JSON" ]]; then
  OUTPUTS_JSON="{}"
fi
export OUTPUTS_JSON

read_output() {
  local output_name="$1"
  "$PYTHON_BIN" - "$output_name" <<'PY'
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

set_repo_var() {
  local name="$1"
  local value="$2"

  if [[ "$APPLY_MODE" == true ]]; then
    gh variable set "$name" --repo "$REPO" --body "$value"
    echo "UPDATED $name"
  else
    echo "DRY-RUN $name=$value"
  fi
}

clear_repo_var() {
  local name="$1"

  if [[ "$APPLY_MODE" == true ]]; then
    if gh variable delete "$name" --repo "$REPO" >/dev/null 2>&1; then
      echo "DELETED $name"
    else
      echo "SKIP $name (not found)"
    fi
  else
    echo "DRY-RUN DELETE $name"
  fi
}

map_and_set() {
  local var_name="$1"
  local output_name="$2"
  local value

  value="$(read_output "$output_name")"
  if [[ -z "$value" ]]; then
    echo "SKIP $var_name (missing terraform output: $output_name)"
    return 0
  fi

  set_repo_var "$var_name" "$value"
}

echo "Target repository: $REPO"
if [[ "$APPLY_MODE" == true ]]; then
  echo "Mode: APPLY"
else
  echo "Mode: DRY-RUN (use --apply to write variables)"
fi

  acr_mode="$(read_output "acr_build_mode")"
  acr_pool="$(read_output "acr_agent_pool_name")"

  if [[ -z "$acr_mode" ]]; then
    if [[ -n "$acr_pool" ]]; then
      acr_mode="private"
    else
      acr_mode="public"
    fi
  fi

echo
map_and_set "AZURE_RESOURCE_GROUP" "resource_group_name"
map_and_set "AZURE_LOCATION" "azure_location"
map_and_set "AZURE_CONTAINER_REGISTRY" "container_registry_name"
  set_repo_var "ACR_BUILD_MODE" "$acr_mode"
  if [[ "$acr_mode" == "private" ]]; then
    map_and_set "AZURE_ACR_AGENT_POOL" "acr_agent_pool_name"
  else
    clear_repo_var "AZURE_ACR_AGENT_POOL"
  fi
map_and_set "AZURE_CONTAINER_APP_ENVIRONMENT" "container_app_environment_name"
map_and_set "AZURE_CONTAINER_APP_NAME" "container_app_name"
map_and_set "NEXT_PUBLIC_API_URL" "container_app_url"
map_and_set "AZURE_STATIC_WEB_APP_NAME" "static_web_app_name"
map_and_set "AZURE_STORAGE_ACCOUNT" "storage_account_name"
map_and_set "AZURE_SQL_SERVER_NAME" "sql_server_name"
map_and_set "AZURE_SQL_DATABASE_NAME" "sql_database_name"
map_and_set "AZURE_API_IDENTITY_NAME" "container_app_identity_name"
map_and_set "AZURE_SEARCH_SERVICE_NAME" "search_service_name"
map_and_set "AZURE_AI_FOUNDRY_ACCOUNT_NAME" "ai_foundry_account_name"
map_and_set "AZURE_AI_MODEL_DEPLOYMENT_NAME" "ai_model_deployment_name"
foundry_eval_name="$(read_output "foundry_eval_name")"
if [[ -z "$foundry_eval_name" ]]; then
  foundry_eval_name="cadence-eval-v1"
  echo "INFO AZURE_FOUNDRY_EVAL_NAME using default (missing terraform output: foundry_eval_name)"
fi
set_repo_var "AZURE_FOUNDRY_EVAL_NAME" "$foundry_eval_name"
foundry_dataset_name="$(read_output "foundry_dataset_name")"
if [[ -z "$foundry_dataset_name" ]]; then
  foundry_dataset_name="cadence-eval-gold"
  echo "INFO AZURE_FOUNDRY_DATASET_NAME using default (missing terraform output: foundry_dataset_name)"
fi
set_repo_var "AZURE_FOUNDRY_DATASET_NAME" "$foundry_dataset_name"
foundry_dataset_version="$(read_output "foundry_dataset_version")"
if [[ -z "$foundry_dataset_version" ]]; then
  foundry_dataset_version="v1"
  echo "INFO AZURE_FOUNDRY_DATASET_VERSION using default (missing terraform output: foundry_dataset_version)"
fi
set_repo_var "AZURE_FOUNDRY_DATASET_VERSION" "$foundry_dataset_version"
map_and_set "AZURE_AI_PROJECT_ENDPOINT" "ai_project_endpoint"
map_and_set "AZURE_GH_RUNNER_IDENTITY_NAME" "github_runner_identity_name"
map_and_set "AZURE_SUBSCRIPTION_ID" "azure_subscription_id"
map_and_set "AZURE_TENANT_ID" "azure_tenant_id"

echo
echo "Manual variables not managed by this script:"
echo "- AZURE_CLIENT_ID"
echo "- GH_RUNNER_APP_ID"
echo "- GH_RUNNER_INSTALLATION_ID"
echo "- TF_STATE_STORAGE_ACCOUNT (optional)"
echo "- GH_RUNNER_REPO_OWNER (optional)"
echo "- GH_RUNNER_REPO_NAME (optional)"
