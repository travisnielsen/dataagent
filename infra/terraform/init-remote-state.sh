#!/usr/bin/env bash
set -euo pipefail

STATE_ACCOUNT="${TF_STATE_STORAGE_ACCOUNT:-}"
if [[ $# -gt 0 ]]; then
  STATE_ACCOUNT="$1"
  shift
fi

if [[ -z "$STATE_ACCOUNT" ]]; then
  echo "Usage: $0 <storage-account-name> [additional terraform init args]"
  echo "Or set TF_STATE_STORAGE_ACCOUNT in the environment."
  exit 1
fi

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_CONFIG_FILE="$(mktemp)"
trap 'rm -f "$BACKEND_CONFIG_FILE"' EXIT

cat >"$BACKEND_CONFIG_FILE" <<EOF
resource_group_name  = "rg-terraform-state"
storage_account_name = "$STATE_ACCOUNT"
container_name       = "tfstate"
key                  = "cadence-private-networking.terraform.tfstate"
use_azuread_auth     = true
EOF

terraform -chdir="$WORKDIR" init -reconfigure -backend-config="$BACKEND_CONFIG_FILE" "$@"
