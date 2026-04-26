# Quickstart: ACR Mode Toggle

**Feature**: 006-toggle-acr-mode
**Audience**: Platform operators and infrastructure engineers
**Time to Read**: 5 minutes

---

## Quick Answer

**Q: Should I use public or private ACR build mode?**

| Use Case | Mode | Why |
|----------|------|-----|
| **Default (recommended)** | `public` | Lower cost, simpler operations, cloud-hosted builds |
| **Strict network isolation required** | `private` | Builds stay within private VNET, compliant with zero-trust policies |
| **Budget-constrained environment** | `public` | Eliminates ~$200/month private agent pool cost |
| **Private cloud or hybrid environment** | `private` | Supports restricted network egress policies |

**Default**: `public` (set automatically in `terraform.tfvars.example`)

---

## Configuration: 5 Steps

### Step 1: Open Terraform Variables

Edit or create `infra/terraform/terraform.tfvars`:

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars  # (if starting fresh)
nano terraform.tfvars  # or your preferred editor
```

### Step 2: Set ACR Mode

Add or modify this line in `terraform.tfvars`:

```hcl
# Cost-optimized (recommended default):
acr_build_mode = "public"

# OR, for network isolation:
acr_build_mode = "private"
```

### Step 3: Validate Configuration

```bash
terraform validate
```

Expected output:
```
Success! The configuration is valid.
```

### Step 4: Plan Deployment

```bash
terraform plan -out=tfplan
```

Review the plan output. Look for:
- **Public mode**: plan shows agent pool `will be destroyed` (or `no change` if already absent)
- **Private mode**: plan shows agent pool `will be created` (or `no change` if already present)

### Step 5: Apply

```bash
terraform apply tfplan
```

Expected output:
```
Apply complete! Resources: [X added/changed/destroyed].
```

**Note**: If switching from private→public, pool deletion may show as "pending" for 5–15 minutes in Azure.

---

## What Happens in Each Mode

### Public Mode (Cost-Optimized) ✅

```
infrastructure:
  acr_build_mode = "public"
  ├─ Container Registry created
  ├─ Public network access: ENABLED
  └─ Agent pool: ABSENT (cost savings!)

ci_pipeline:
  ├─ Build path: Azure-hosted ACR Tasks
  ├─ Command: az acr build (no --agent-pool)
  └─ Network: Cloud-hosted build plane

monthly_cost:
  ├─ Agent pool baseline: $0 (eliminated)
  ├─ Per-build ACR task: ~$0.01–0.05
  └─ Total: ~$15–50/month (depending on build frequency)
```

**Use when**: Budget-conscious, no strict network isolation required, or builds tolerate minor cloud infrastructure latency.

---

### Private Mode (Network Isolation) 🔒

```
infrastructure:
  acr_build_mode = "private"
  ├─ Container Registry created
  ├─ Public network access: BLOCKED
  └─ Agent pool: PRESENT (S2 tier, 2 instances)

ci_pipeline:
  ├─ Build path: Private agent pool in VNET
  ├─ Command: az acr build --agent-pool cadencepool
  └─ Network: Build executes in private VNET subnet

monthly_cost:
  ├─ Agent pool baseline: ~$150/month
  ├─ Per-build compute: marginal (included in pool)
  └─ Total: ~$150–200/month (recurring)
```

**Use when**: Strict network isolation required, compliance mandates private build plane, or zero-trust networking policies active.

---

## Verify Your Deployment

### Check ACR Access State

```bash
az acr show \
  --resource-group $(terraform output -raw resource_group_name) \
  --name $(terraform output -raw container_registry_name) \
  --query publicNetworkAccess
```

Expected output:
- Public mode: `"Enabled"`
- Private mode: `"Disabled"`

### Check Agent Pool Status

```bash
az acr agentpool list \
  --registry $(terraform output -raw container_registry_name) \
  --resource-group $(terraform output -raw resource_group_name)
```

Expected output:
- Public mode: `[]` (empty list)
- Private mode: `[{ "name": "cadencepool", "tier": "S2", "instanceCount": 2 }]`

### Export GitHub Actions Variables

After deployment, update GitHub Actions with mode-aware variables:

```bash
bash infra/scripts/update-github-vars-from-terraform.sh
```

This script will:
- Read Terraform outputs
- Emit `ACR_BUILD_MODE` (public or private)
- Emit `AZURE_ACR_AGENT_POOL` only if mode is private (null otherwise)
- Update GitHub repository variables/secrets

Verify in GitHub:
1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Confirm `ACR_BUILD_MODE` is set to your chosen mode
3. Confirm `AZURE_ACR_AGENT_POOL` is set (private) or empty/absent (public)

---

## Switching Modes (Mode Changes)

### Change from Public to Private

```bash
# Edit tfvars:
sed -i 's/acr_build_mode = "public"/acr_build_mode = "private"/' terraform.tfvars

# Plan and apply:
terraform plan -out=tfplan
terraform apply tfplan

# Update GitHub Actions:
bash infra/scripts/update-github-vars-from-terraform.sh
```

Expected side effects:
- First build after switch may take 2–3 minutes (pool spin-up)
- ACR blocks public access (verified via `az acr show`)
- Subsequent builds run on private pool (faster, isolated network)

### Change from Private to Public

```bash
# Edit tfvars:
sed -i 's/acr_build_mode = "private"/acr_build_mode = "public"/' terraform.tfvars

# Plan and apply:
terraform plan -out=tfplan
terraform apply tfplan

# Wait for pool deletion (5–15 minutes):
watch -n 10 'az acr agentpool list --registry $(terraform output -raw container_registry_name)'
# (Ctrl+C to exit when list is empty)

# Update GitHub Actions:
bash infra/scripts/update-github-vars-from-terraform.sh
```

Expected side effects:
- Terraform apply completes immediately
- Pool deletion proceeds asynchronously in Azure (5–15 min)
- ACR enables public access immediately
- First build after switch uses public ACR Tasks
- Cost drops by ~$150–200/month once pool deletion completes

---

## Troubleshooting

### Q: Terraform validate fails with "acr_build_mode must be 'public' or 'private'"

**A**: You have a typo in `terraform.tfvars`. Valid values are exactly `"public"` or `"private"` (lowercase, double-quoted).

```bash
# ✅ Correct:
acr_build_mode = "public"

# ❌ Incorrect:
acr_build_mode = Public      # Not quoted
acr_build_mode = "PUBLIC"    # Uppercase
acr_build_mode = "pub"       # Incomplete
```

---

### Q: Build fails with "agent pool not found"

**A**: Your GitHub Actions workflow is configured for private mode, but the agent pool hasn't been created yet.

**Fix**:
1. Verify Terraform apply succeeded: `az acr agentpool list`
2. If pool missing, re-run: `terraform apply -var acr_build_mode=private`
3. Wait 2–5 minutes for pool creation
4. Re-run the build job

Alternatively, temporarily switch workflow to public mode to unblock deployments:
```bash
gh variable set ACR_BUILD_MODE --body "public"
```

---

### Q: After switching to private mode, where is my build pool?

**A**: Pool creation is asynchronous and takes 2–5 minutes. Check status:

```bash
# Watch pool creation in real-time:
watch -n 5 'az acr agentpool list --registry $(terraform output -raw container_registry_name) --output json | jq ".[0] | {name, tier, status: .status // \"Creating...\"}"'
```

When status shows `Running`, builds can use the pool.

---

### Q: I switched from private to public. Why is my old pool still showing in the portal?

**A**: Azure deletes the pool asynchronously. This is expected.

```bash
# Check deletion status:
az acr agentpool list --registry $(terraform output -raw container_registry_name)

# If still showing after 20 minutes, manually verify deletion request:
az acr agentpool delete --name cadencepool --registry $(terraform output -raw container_registry_name)
```

---

### Q: How do I roll back to the previous mode?

**A**: Edit `terraform.tfvars` back to the previous mode and re-apply:

```bash
# Undo your change in terraform.tfvars, then:
terraform plan -out=tfplan
terraform apply tfplan
```

Terraform state ensures the previous mode's resources are restored (or destroyed as needed).

---

## Cost Reference

### Monthly Cost Estimate

| Item | Public Mode | Private Mode |
|------|-------------|--------------|
| **Agent Pool Baseline** | $0 | ~$150 |
| **Per-Build Compute** | ~$0.01–0.05 per build | Included in baseline |
| **ACR Data Storage** | Same (shared registry) | Same (shared registry) |
| **Network (egress)** | Cloud-hosted rates | Private VNET rates |
| **Total (typical)** | **~$15–50/month** | **~$150–200/month** |

**Payoff scenario**: If you require strict network isolation or zero-trust compliance, the $150/month is justified. Otherwise, public mode saves cost.

---

## Next Steps

- **Monitor builds**: Check GitHub Actions logs to confirm mode is working as expected
- **Set alerts**: Configure Azure Monitor alerts for ACR access/pool anomalies
- **Document decision**: Add a note to your runbook explaining why you chose public/private mode for future maintainers

---

## Questions?

Refer to:
- Feature spec: `specs/006-toggle-acr-mode/spec.md`
- Technical details: `specs/006-toggle-acr-mode/data-model.md`
- Workflow contract: `specs/006-toggle-acr-mode/contracts/workflow-build-path.md`
- Terraform contract: `specs/006-toggle-acr-mode/contracts/terraform-config.md`
