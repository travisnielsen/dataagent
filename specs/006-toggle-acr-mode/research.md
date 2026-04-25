# Research: ACR Mode Toggle

**Feature**: 006-toggle-acr-mode  
**Date**: 2026-04-25

## R-001: Terraform conditional patterns for ACR public/private modes

- **Decision**:
  - Introduce `variable "acr_build_mode"` (`string`, default `"public"`, validation: `public|private`).
  - Drive ACR exposure with a derived local: `local.acr_is_private = var.acr_build_mode == "private"`.
  - Set `module.container_registry.public_network_access_enabled = !local.acr_is_private`.
  - Create the agent pool only in private mode using `count = local.acr_is_private ? 1 : 0`.
- **Rationale**:
  - One mode variable controls all mode-dependent behavior and avoids partial drift.
  - `count` cleanly models present/absent lifecycle in Terraform state.
  - Current code already has `public_network_access_enabled = true` and always-on agent pool; these are the exact toggle points.
- **Alternatives considered**:
  - `for_each` instead of `count`: rejected for single-instance resource because index-based `count` is simpler and sufficient.
  - Separate modules per mode: rejected as over-complex for one toggle and increases maintenance overhead.
  - Keep pool always present and just switch workflow behavior: rejected because it fails cost-optimization requirement.
- **Implementation notes (Phase 1/2)**:
  - **Phase 1**: document variable schema and mode truth table in `data-model.md`.
  - **Phase 2**:
    - Update `infra/private-networking/variables.tf`, `workload.tf`, `outputs.tf`, and `terraform.tfvars.example`.
    - Guard output: `acr_agent_pool_name = local.acr_is_private ? azurerm_container_registry_agent_pool.acr_tasks[0].name : null`.
    - Add README section mapping mode to ACR network posture + build path.

## R-002: Best practices for toggling private ACR agent pools

- **Decision**:
  - Treat agent pool as fully ephemeral when mode is `public` (destroy) and fully managed when mode is `private` (create).
  - Keep explicit dependency on ACR and network readiness (`depends_on`) for deterministic creation order.
  - Use clear operator guidance that private→public may take longer because pool deletion is asynchronous in Azure.
- **Rationale**:
  - Resource deletion is the only reliable way to stop ongoing pool cost.
  - ACR agent pool creation requires registry and network path availability; current `depends_on` pattern should be retained.
  - Documenting slower transitions reduces false-positive incident reports during apply.
- **Alternatives considered**:
  - Scale `instance_count` to zero: rejected because not all configurations support true zero-cost standby behavior; explicit delete is safer for cost goals.
  - `lifecycle { prevent_destroy = true }`: rejected because it blocks required public-mode convergence.
  - Manual pre-destroy step outside Terraform: rejected because FR-006 requires single-cycle convergence without manual cleanup.
- **Implementation notes (Phase 1/2)**:
  - **Phase 1**: define expected transition timings and failure modes (e.g., delete lag, retry guidance).
  - **Phase 2**:
    - Keep `depends_on = [module.container_registry, time_sleep.wait_for_network_ready]` on pool resource.
    - Add runbook checks: verify pool absence/presence with `az acr agentpool list` after apply.
    - Keep workflow preflight checks so builds fail fast if pool is missing in private mode.

## R-003: ACR Tasks (cloud) vs ACR agent pool (private) build execution

- **Decision**:
  - Use **public mode** for default cost-optimized builds via Azure-hosted ACR Tasks (no dedicated private pool).
  - Use **private mode** only when strict private build-plane/network isolation is required.
  - Keep a shared command surface (`az acr build`) and toggle only the `--agent-pool` usage.
- **Rationale**:
  - Azure-hosted tasks avoid persistent private pool cost and simplify operations.
  - Private agent pool is justified only when private subnet execution path is mandatory.
  - Same CLI command reduces workflow branching complexity and lowers regression risk.
- **Alternatives considered**:
  - Always use private pool: rejected due to unnecessary recurring cost in environments without isolation requirements.
  - Build locally in runner with Docker and push: rejected because it adds runner dependencies and inconsistent build environments.
  - Use external build service (e.g., separate CI builder): rejected as out of scope and higher operational complexity.
- **Implementation notes (Phase 1/2)**:
  - **Phase 1**: document decision matrix (cost vs isolation vs operational overhead).
  - **Phase 2**:
    - Private mode: run `az acr build --agent-pool "$AZURE_ACR_AGENT_POOL" ...`.
    - Public mode: run `az acr build` without `--agent-pool`.
    - Add workflow diagnostics step printing selected mode and selected build path.

## R-004: GitHub Actions conditional patterns for mode-based build logic

- **Decision**:
  - Add repo variable `ACR_BUILD_MODE` (values `public|private`) as workflow control input.
  - Add early validation step to enforce value domain and required mode-specific variables.
  - Split build into two explicit conditional steps:
    - `if: env.ACR_BUILD_MODE == 'private'` → require and use `AZURE_ACR_AGENT_POOL`.
    - `if: env.ACR_BUILD_MODE == 'public'` → run Azure-hosted ACR task path.
- **Rationale**:
  - Explicit step branching improves readability and auditability over inline shell `if` blocks.
  - Fail-fast validation prevents opaque Azure CLI errors later in the job.
  - Decouples deploy step from build-path internals; downstream steps consume the same image tag.
- **Alternatives considered**:
  - Single bash step with runtime branching: rejected for lower maintainability and weaker UI visibility in Actions logs.
  - Infer mode from whether `AZURE_ACR_AGENT_POOL` exists: rejected as ambiguous and prone to stale variable drift.
  - Separate workflows per mode: rejected due to duplication and higher maintenance burden.
- **Implementation notes (Phase 1/2)**:
  - **Phase 1**: define workflow input contract and error messages for mismatched prerequisites.
  - **Phase 2**:
    - Update `.github/workflows/cd-api.yml` and `.github/workflows/cd-frontend.yml` environment contracts.
    - Keep `runs-on` unchanged unless a later requirement introduces public-runner execution.
    - Extend `infra/scripts/print-github-vars-from-terraform.sh` and `update-github-vars-from-terraform.sh` to emit `ACR_BUILD_MODE` (and skip `AZURE_ACR_AGENT_POOL` updates when output is null).

## R-005: Terraform convergence and idempotency validation for mode switches

- **Decision**:
  - Validate convergence with a deterministic apply matrix:
    1. public baseline apply
    2. re-apply public (no-op)
    3. switch to private apply
    4. re-apply private (no-op)
    5. switch back to public apply
  - For each step, verify both Terraform plan emptiness and Azure runtime state.
- **Rationale**:
  - Covers both steady-state idempotency and bidirectional transitions required by FR-006/SC-003.
  - Runtime verification catches provider/API eventual-consistency gaps not visible from plan output alone.
- **Alternatives considered**:
  - Validate only Terraform plan output: rejected because plan-only checks can miss delayed Azure-side deletion.
  - Validate only one-way transition (public→private): rejected because private→public has the key cost-critical destroy path.
  - Manual spot checks: rejected due to low repeatability.
- **Implementation notes (Phase 1/2)**:
  - **Phase 1**: codify validation checklist and expected evidence artifacts (plan outputs + CLI checks).
  - **Phase 2**:
    - Use `terraform plan -detailed-exitcode` after each apply; require exit code `0` for no-op re-apply.
    - Validate ACR exposure via `az acr show --query publicNetworkAccess` and pool existence via `az acr agentpool list`.
    - Record workflow run IDs for both modes to prove end-to-end build path success.
