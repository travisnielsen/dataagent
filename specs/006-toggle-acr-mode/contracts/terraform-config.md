# Terraform Configuration Contract

**Feature**: 006-toggle-acr-mode
**Applies To**: `infra/private-networking/`
**Version**: 1.0

## Variable Input Contract

### Required Changes to `variables.tf`

```hcl
variable "acr_build_mode" {
  type        = string
  default     = "public"
  description = "ACR build execution mode: 'public' (cloud-hosted ACR Tasks, cost-optimized default) or 'private' (private agent pool with network isolation)."

  validation {
    condition     = contains(["public", "private"], var.acr_build_mode)
    error_message = "acr_build_mode must be either 'public' or 'private'."
  }
}
```

---

## Resource Configuration Contract

### ACR Module Update in `workload.tf`

**Before**:
```hcl
module "container_registry" {
  source                        = "Azure/avm-res-containerregistry-registry/azurerm"
  # ... other fields ...
  public_network_access_enabled = true  # ← HARDCODED
  # ... other fields ...
}
```

**After**:
```hcl
locals {
  acr_is_private = var.acr_build_mode == "private"
}

module "container_registry" {
  source                        = "Azure/avm-res-containerregistry-registry/azurerm"
  # ... other fields ...
  public_network_access_enabled = !local.acr_is_private  # ← CONDITIONAL
  # ... other fields ...
}
```

---

### Agent Pool Resource Update in `workload.tf`

**Before**:
```hcl
resource "azurerm_container_registry_agent_pool" "acr_tasks" {
  name                      = "${local.identifier}pool"
  container_registry_name   = element(reverse(split("/", module.container_registry.resource_id)), 0)
  # ... all other fields unchanged ...
}
```

**After**:
```hcl
resource "azurerm_container_registry_agent_pool" "acr_tasks" {
  count = local.acr_is_private ? 1 : 0  # ← ADD COUNT

  name                      = "${local.identifier}pool"
  container_registry_name   = element(reverse(split("/", module.container_registry.resource_id)), 0)
  # ... all other fields unchanged ...

  depends_on = [
    module.container_registry,
    time_sleep.wait_for_network_ready
  ]
}
```

---

### Output Update in `outputs.tf`

**Before**:
```hcl
output "acr_agent_pool_name" {
  value = azurerm_container_registry_agent_pool.acr_tasks.name
}
```

**After**:
```hcl
output "acr_agent_pool_name" {
  description = "Name of the ACR private agent pool, or null when in public mode."
  value       = local.acr_is_private ? azurerm_container_registry_agent_pool.acr_tasks[0].name : null
}
```

---

## Configuration File Contract

### `terraform.tfvars.example` Addition

Add line with comment:

```hcl
# ACR build mode: "public" (cost-optimized, cloud-hosted ACR Tasks, default)
#                 "private" (private agent pool in VNET, requires network isolation)
# Default: "public" to minimize recurring costs. Change to "private" if strict build-plane
# isolation is required (e.g., due to compliance, data residency, or custom networking).
acr_build_mode = "public"
```

---

## Apply Output Contract

### Expected `terraform plan` Output (Public Mode - Default)

```
Terraform will perform the following actions:

  # module.container_registry will be created
  + resource "azurerm_container_registry" "registry" {
      + ...
      + public_network_access_enabled = true
      + ...
    }

  # azurerm_container_registry_agent_pool.acr_tasks will be destroyed
  - resource "azurerm_container_registry_agent_pool" "acr_tasks" {
      - name = "cadencepool"
      - ...
    }

Plan: 1 to add, 0 to change, 1 to destroy.
```

### Expected `terraform plan` Output (Private Mode)

```
Terraform will perform the following actions:

  # module.container_registry will be updated in-place
  ~ resource "azurerm_container_registry" "registry" {
      ~ public_network_access_enabled = true -> false
      # ... other fields unchanged ...
    }

  # azurerm_container_registry_agent_pool.acr_tasks will be created
  + resource "azurerm_container_registry_agent_pool" "acr_tasks" {
      + name                 = "cadencepool"
      + tier                 = "S2"
      + instance_count       = 2
      + virtual_network_subnet_id = azurerm_subnet.application.id
      + ...
    }

Plan: 1 to add, 1 to change, 0 to destroy.
```

---

## State Transition Contract

### Idempotency Validation (No-op Re-apply)

After any `terraform apply` with consistent `acr_build_mode` value:

```bash
$ terraform plan -detailed-exitcode
Exit code: 0  # ← Indicates no changes required (idempotent)
```

Repeated applies produce plan output:
```
No changes. Infrastructure is up-to-date.
```

---

## Validation Checklist (Phase 2 Testing)

- [ ] `terraform validate` passes with new `acr_build_mode` variable and validation rule
- [ ] `terraform plan` with `acr_build_mode = "public"` shows pool destruction, public access enabled
- [ ] `terraform plan` with `acr_build_mode = "private"` shows pool creation, public access disabled
- [ ] First `apply` with public mode creates ACR and skips pool (count = 0)
- [ ] Re-apply with same public mode produces zero-change plan (idempotent)
- [ ] Switch to private mode and apply creates pool and disables public access
- [ ] Re-apply with private mode produces zero-change plan (idempotent)
- [ ] Switch back to public mode destroys pool and re-enables public access
- [ ] Verify Azure portal reflects correct ACR access state after each apply
- [ ] Verify `acr_agent_pool_name` output is null in public mode, has value in private mode
