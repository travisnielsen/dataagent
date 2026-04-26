#!/usr/bin/env bash
set -euo pipefail

GROUP_DISPLAY_NAME="${1:-cadence-sql-admins}"
ADMIN_USER_OBJECT_ID="${2:-}"
GH_PRINCIPAL_ID_OR_APPID="${3:-}"

usage() {
  cat <<'USAGE'
Usage:
  ensure-sql-admin-group.sh [group_display_name] [admin_user_object_id] [gh_principal_object_id_or_app_id]

Arguments:
  group_display_name               Optional. Entra group display name. Default: cadence-sql-admins
  admin_user_object_id             Optional. Entra object ID of the human admin user. If omitted, uses signed-in user.
  gh_principal_object_id_or_app_id Optional. GitHub federated principal object ID or app ID.
                                   If omitted, script tries terraform.tfvars github_federated_principal_client_id.

Examples:
  ./ensure-sql-admin-group.sh
  ./ensure-sql-admin-group.sh cadence-sql-admins ee7213d2-4308-4fbd-b304-537e4a6b266b 079f9848-944a-4ce8-b73a-643feaa1a9bf
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
TFVARS_PATH="${REPO_ROOT}/terraform/terraform.tfvars"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1"
    exit 1
  fi
}

require_cmd az
require_cmd tr

if [[ -z "$ADMIN_USER_OBJECT_ID" ]]; then
  ADMIN_USER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"
fi

if [[ -z "$GH_PRINCIPAL_ID_OR_APPID" && -f "$TFVARS_PATH" ]]; then
  GH_PRINCIPAL_ID_OR_APPID="$(sed -nE 's/^github_federated_principal_client_id\s*=\s*"([^"]+)"/\1/p' "$TFVARS_PATH" | head -n1)"
fi

if [[ -z "$ADMIN_USER_OBJECT_ID" ]]; then
  echo "Unable to determine admin user object ID. Pass it as argument 2."
  exit 1
fi

if [[ -z "$GH_PRINCIPAL_ID_OR_APPID" ]]; then
  echo "Unable to determine GitHub federated principal ID/app ID. Pass it as argument 3."
  exit 1
fi

resolve_group_id() {
  az ad group list --filter "displayName eq '$GROUP_DISPLAY_NAME'" --query "[0].id" -o tsv
}

GROUP_ID="$(resolve_group_id)"
if [[ -z "$GROUP_ID" ]]; then
  MAIL_NICKNAME="$(echo "$GROUP_DISPLAY_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  GROUP_ID="$(az ad group create --display-name "$GROUP_DISPLAY_NAME" --mail-nickname "$MAIL_NICKNAME" --query id -o tsv)"
  echo "Created Entra group '$GROUP_DISPLAY_NAME' ($GROUP_ID)"
else
  echo "Entra group already exists: '$GROUP_DISPLAY_NAME' ($GROUP_ID)"
fi

resolve_sp_object_id() {
  local provided="$1"
  # Azure CLI accepts either appId or objectId for --id.
  az ad sp show --id "$provided" --query id -o tsv
}

GH_SP_OBJECT_ID="$(resolve_sp_object_id "$GH_PRINCIPAL_ID_OR_APPID")"
if [[ -z "$GH_SP_OBJECT_ID" ]]; then
  echo "Failed to resolve GitHub federated principal from: $GH_PRINCIPAL_ID_OR_APPID"
  exit 1
fi

ensure_member() {
  local member_id="$1"
  local label="$2"

  local exists
  exists="$(az ad group member check --group "$GROUP_ID" --member-id "$member_id" --query value -o tsv)"
  if [[ "$exists" == "true" ]]; then
    echo "$label already in group"
    return
  fi

  az ad group member add --group "$GROUP_ID" --member-id "$member_id"
  echo "Added $label to group"
}

ensure_member "$ADMIN_USER_OBJECT_ID" "admin user ($ADMIN_USER_OBJECT_ID)"
ensure_member "$GH_SP_OBJECT_ID" "GitHub principal ($GH_SP_OBJECT_ID)"

echo

echo "Use these values in infra/terraform/terraform.tfvars:"
echo "sql_azuread_admin_object_id      = \"$GROUP_ID\""
echo "sql_azuread_admin_login_username = \"$GROUP_DISPLAY_NAME\""
