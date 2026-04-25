# Workflow Build Path Contract

**Feature**: 006-toggle-acr-mode
**Applies To**: `.github/workflows/cd-api.yml`, `.github/workflows/cd-frontend.yml`
**Version**: 1.0

## Input Contract

### Environment Variables (Required)

| Variable | Type | Values | Required | Source |
|----------|------|--------|----------|--------|
| `ACR_BUILD_MODE` | string | `public` \| `private` | ✅ Yes | Terraform output (via `update-github-vars-from-terraform.sh`) |
| `AZURE_ACR_NAME` | string | ACR name | ✅ Yes | Repository secret |
| `AZURE_ACR_ENDPOINT` | string | ACR endpoint (e.g., `myacr.azurecr.io`) | ✅ Yes | Repository secret |

### Conditional Variables

| Variable | Required When | Type | Source |
|----------|---------------|------|--------|
| `AZURE_ACR_AGENT_POOL` | `ACR_BUILD_MODE == "private"` | string | Terraform output (optional; null when public mode) |
| `AZURE_CREDENTIALS` | Always | JSON | Service principal credentials (role: AcrPush, AcrPull) |

---

## Workflow Branch Logic

### Validation Step (Early Exit)

```yaml
- name: Validate ACR mode prerequisites
  run: |
    if [[ "${{ env.ACR_BUILD_MODE }}" != "public" ]] && [[ "${{ env.ACR_BUILD_MODE }}" != "private" ]]; then
      echo "ERROR: ACR_BUILD_MODE must be 'public' or 'private', got '${{ env.ACR_BUILD_MODE }}'"
      exit 1
    fi
    if [[ "${{ env.ACR_BUILD_MODE }}" == "private" && -z "${{ env.AZURE_ACR_AGENT_POOL }}" ]]; then
      echo "ERROR: ACR_BUILD_MODE=private requires AZURE_ACR_AGENT_POOL to be set"
      exit 1
    fi
    echo "✓ ACR mode validation passed (mode=${{ env.ACR_BUILD_MODE }})"
```

---

### Public Mode Build Path

**Trigger**: `env.ACR_BUILD_MODE == 'public'`

```yaml
- name: Build & push image (public mode - Azure-hosted ACR Tasks)
  if: env.ACR_BUILD_MODE == 'public'
  run: |
    az acr build \
      --registry "${{ env.AZURE_ACR_NAME }}" \
      --image "${{ env.IMAGE_NAME }}:${{ github.sha }}" \
      --image "${{ env.IMAGE_NAME }}:latest" \
      --file Dockerfile \
      .
```

**Output**: Image pushed to `$AZURE_ACR_ENDPOINT/$IMAGE_NAME:$GITHUB_SHA` via cloud-hosted task
**Cost**: Minimal (per-build Azure-hosted execution; no dedicated pool)
**Expected Duration**: 3–8 minutes depending on build complexity

---

### Private Mode Build Path

**Trigger**: `env.ACR_BUILD_MODE == 'private'`

```yaml
- name: Build & push image (private mode - agent pool)
  if: env.ACR_BUILD_MODE == 'private'
  run: |
    az acr build \
      --registry "${{ env.AZURE_ACR_NAME }}" \
      --agent-pool "${{ env.AZURE_ACR_AGENT_POOL }}" \
      --image "${{ env.IMAGE_NAME }}:${{ github.sha }}" \
      --image "${{ env.IMAGE_NAME }}:latest" \
      --file Dockerfile \
      .
```

**Output**: Image pushed to `$AZURE_ACR_ENDPOINT/$IMAGE_NAME:$GITHUB_SHA` via private agent pool
**Cost**: Per-build execution on private pool + pool baseline (S2, 2 instances)
**Expected Duration**: 2–6 minutes (typically faster than public; dedicated private resources)

---

## Output Contract

Regardless of build path, the workflow MUST produce:

| Output | Format | Example |
|--------|--------|---------|
| Image tag (sha) | `$REGISTRY/$IMAGE:$SHA` | `myacr.azurecr.io/cadence-api:abc1234def` |
| Image tag (latest) | `$REGISTRY/$IMAGE:latest` | `myacr.azurecr.io/cadence-api:latest` |
| Build mode used | Logged to workflow output | "✓ Build completed via public mode" |
| Push status | Success = exit code 0 | N/A |

---

## Error Handling

| Error | Cause | Recovery |
|-------|-------|----------|
| `ERROR: ACR_BUILD_MODE not set` | GitHub variable not exported | Run `infra/scripts/update-github-vars-from-terraform.sh` and rerun workflow |
| `ERROR: ACR_BUILD_MODE=private requires AZURE_ACR_AGENT_POOL` | Pool not yet available | Verify pool was created with `az acr agentpool list`; wait 2–5 min for creation; retry |
| `Agent pool not found` | Pool deleted during mode switch | Normal during private→public transition; workflow falls back to public if ACR_BUILD_MODE was updated |
| `Unauthorized: insufficient permissions` | Service principal lacks AcrPush role | Verify service principal has `AcrPush` role on ACR in RBAC |
| `Build timed out` | Build exceeded ACR timeout | Optimize Dockerfile or increase ACR task timeout (default 3600s) |

---

## Testing & Validation

### Pre-Deployment Workflow Test

1. **Public Mode**:
   - Set `ACR_BUILD_MODE=public` in repository variables
   - Trigger workflow; expect successful build via public ACR Tasks
   - Verify image in ACR with `az acr repository list`

2. **Private Mode**:
   - Set `ACR_BUILD_MODE=private` in repository variables
   - Ensure pool exists with `az acr agentpool list`
   - Trigger workflow; expect successful build via agent pool
   - Check workflow logs for `--agent-pool` in `az acr build` command

3. **Mode Mismatch Detection**:
   - Set `ACR_BUILD_MODE=private` but ensure `AZURE_ACR_AGENT_POOL` is empty
   - Trigger workflow; expect early validation failure with clear error message
   - Fix and retry

---

## Future Enhancements

- **Diagnostic output**: Add step to log selected mode and agent pool status at workflow start
- **Fallback logic**: Auto-retry with public mode if private pool unavailable (soft fail)
- **Cost reporting**: Log build cost estimate per mode to help operators track spend trends
