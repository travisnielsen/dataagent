# Data Model: ACR Mode Toggle

**Feature**: 006-toggle-acr-mode
**Phase**: 1 (Design & Contracts)
**Date**: 2026-04-25

## Terraform Variables

### Core Variable

#### `acr_build_mode`

```hcl
variable "acr_build_mode" {
  type        = string
  default     = "public"
  description = "ACR build execution mode: 'public' (cloud-hosted ACR Tasks, default for cost optimization) or 'private' (private agent pool, requires network isolation)."

  validation {
    condition     = contains(["public", "private"], var.acr_build_mode)
    error_message = "acr_build_mode must be 'public' or 'private'."
  }
}
```

**Attributes**:
- **Type**: `string` (enum-like via validation rule)
- **Default**: `"public"` (cost optimization, minimal operational overhead)
- **Allowed Values**: `public` | `private`
- **Scope**: `infra/private-networking/` only; not used in public-networking
- **Validation**: Hard constraint; invalid values are rejected during `terraform validate`

---

## Derived Locals

### Mode-Control Local

```hcl
locals {
  acr_is_private = var.acr_build_mode == "private"
}
```

**Purpose**: Single source of truth for mode-dependent branching throughout Terraform module.
**Usage**: All conditional resource creation, output nullability, and Azure configuration decisions derive from this local.

---

## Resource Conditionals

### ACR Module Configuration

**Attribute**: `public_network_access_enabled`
**Derivation**: `!local.acr_is_private`
**Behavior**:
- When mode is `private`: ACR blocks public network access (`false`)
- When mode is `public`: ACR allows public network access (`true`)

### Agent Pool Resource

**Resource**: `azurerm_container_registry_agent_pool.acr_tasks`
**Count Condition**: `local.acr_is_private ? 1 : 0`
**Behavior**:
- When mode is `private`: pool is created and managed
- When mode is `public`: pool is destroyed and absent from state

**Dependencies**: `[module.container_registry, time_sleep.wait_for_network_ready]` (unchanged)

---

## Outputs

### Conditional Output: ACR Agent Pool Name

```hcl
output "acr_agent_pool_name" {
  description = "Name of the ACR private agent pool, or null when in public mode."
  value       = local.acr_is_private ? azurerm_container_registry_agent_pool.acr_tasks[0].name : null
}
```

**Semantics**:
- Returns pool name when mode is `private`
- Returns `null` when mode is `public` (used to signal workflow that private build path is unavailable)
- Consumers (GitHub Actions scripts) MUST check for null and adjust behavior accordingly

### Unchanged Output: ACR Endpoint

```hcl
output "acr_endpoint" {
  description = "Azure Container Registry endpoint (independent of build mode)."
  value       = module.container_registry.endpoint
}
```

**Note**: ACR endpoint is mode-independent; all images are stored in same registry regardless of build path.

---

## GitHub Repository Variables & Secrets

### New Variable: `ACR_BUILD_MODE`

**Type**: Repository secret / environment variable
**Values**: `public` | `private`
**Source**: Derived from Terraform output in deployment helper scripts
**Purpose**: Drive workflow build-path selection in CI/CD pipelines
**Mapping**:
- Set by `infra/scripts/print-github-vars-from-terraform.sh` and `update-github-vars-from-terraform.sh`
- When `acr_agent_pool_name` output is null → emit `ACR_BUILD_MODE=public`
- When `acr_agent_pool_name` output has value → emit `ACR_BUILD_MODE=private`

### Existing Variable: `AZURE_ACR_AGENT_POOL`

**Lifecycle**:
- In `private` mode: variable is set and required by workflows
- In `public` mode: variable is NOT set (or set to empty string); workflows skip agent-pool build step

---

## Mode Truth Table

| Configuration | ACR Public Access | Agent Pool Exists | Build Path | Cost Profile |
|---------------|-------------------|-------------------|------------|--------------|
| `acr_build_mode = "public"` (default) | ✅ Enabled | ❌ No | Azure-hosted ACR Tasks | Minimal (no dedicated pool) |
| `acr_build_mode = "private"` | ❌ Blocked | ✅ Yes | Private agent pool (S2, 2 instances) | Recurring (pool compute + networking) |

---

## State Transition Validation

### Public → Private Transition

1. **Prerequisites**: `acr_build_mode` changed from `public` to `private` in `terraform.tfvars`
2. **Expected Actions**:
   - ACR `public_network_access_enabled` flips to `false`
   - Agent pool resource moves from count(0) to count(1), triggering creation
   - Pool becomes available in Azure within ~2–5 minutes
   - `acr_agent_pool_name` output now contains pool name (non-null)
3. **Convergence**: Single `terraform apply` cycle completes; subsequent `terraform plan` shows no changes
4. **Workflow Impact**: Next build job uses private agent pool (`az acr build --agent-pool ...`)

### Private → Public Transition

1. **Prerequisites**: `acr_build_mode` changed from `private` to `public` in `terraform.tfvars`
2. **Expected Actions**:
   - ACR `public_network_access_enabled` flips to `true`
   - Agent pool resource moves from count(1) to count(0), triggering deletion
   - Pool deletion is asynchronous and may take 5–15 minutes to complete in Azure
   - `acr_agent_pool_name` output now returns `null`
3. **Convergence**: Single `terraform apply` cycle completes; may see "deletion pending" in Azure portal briefly; subsequent `terraform plan` shows no changes
4. **Workflow Impact**: Next build job uses public ACR Tasks (`az acr build` without `--agent-pool`)

### Idempotency Validation

- Repeated `terraform apply` with same `acr_build_mode` value produces zero-change plan (`No changes. Infrastructure is up-to-date.`)
- Terraform state remains consistent across re-apply cycles
- Azure resource state (ACR access + pool presence) matches Terraform intent

---

## Error Scenarios & Mitigations

| Scenario | Detection | Mitigation |
|----------|-----------|-----------|
| Invalid `acr_build_mode` value in tfvars | `terraform validate` fails immediately | Fix tfvars to use `public` or `private` |
| Workflow configured for private but agent pool missing | Build fails with "agent pool not found" error | Verify `ACR_BUILD_MODE` matches infrastructure; re-run `update-github-vars-from-terraform.sh` |
| Workflow configured for public but agent pool still being deleted | Build completes successfully (uses public path) | No action required; private pool cleanup continues asynchronously |
| Pool deletion delayed beyond apply window | Terraform apply succeeds; pool still visible in Azure | Expected behavior; pool deletion is asynchronous; verify with `az acr agentpool list` after 10–15 minutes |

---

## Implementation Checklist (Phase 2)

- [ ] Add `acr_build_mode` variable to `infra/private-networking/variables.tf` with validation
- [ ] Add `local.acr_is_private` derived local
- [ ] Update ACR module invocation: set `public_network_access_enabled = !local.acr_is_private`
- [ ] Update agent pool resource: add `count = local.acr_is_private ? 1 : 0`
- [ ] Update `acr_agent_pool_name` output: add conditional null logic
- [ ] Update `terraform.tfvars.example` with `acr_build_mode = "public"` and cost guidance comment
- [ ] Update `infra/private-networking/README.md` with mode selection guide and build-path implications
- [ ] Update `infra/scripts/print-github-vars-from-terraform.sh` to emit `ACR_BUILD_MODE` based on pool name output nullability
- [ ] Update `infra/scripts/update-github-vars-from-terraform.sh` to handle null `AZURE_ACR_AGENT_POOL` gracefully
- [ ] Validate idempotency with test matrix: public (apply, reapply) → private (apply, reapply) → public (apply, reapply)
