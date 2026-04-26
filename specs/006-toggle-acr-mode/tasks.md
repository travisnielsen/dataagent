# Tasks: ACR Mode Toggle

**Input**: Design documents from `/specs/006-toggle-acr-mode/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Automated tests were not explicitly requested in the feature spec. This task list uses validation and verification tasks instead of new test-suite code.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (`US1`, `US2`, `US3`)
- Every task includes an exact file path

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Introduce the core mode configuration and baseline documentation for deployments managed in `infra/terraform/`.

- [ ] T001 Add `acr_build_mode` variable with default `public` and enum validation in `infra/terraform/variables.tf`
- [ ] T002 [P] Add `acr_build_mode = "public"` with operator guidance comments in `infra/terraform/terraform.tfvars.example`
- [ ] T003 [P] Add ACR mode overview section and scope note (`infra/terraform/` only) in `infra/terraform/README.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared mode outputs and GitHub variable propagation required by workflow and documentation stories.

**CRITICAL**: No user story work should start until this phase is complete.

- [ ] T004 Add and align Terraform output contract notes for mode semantics in `specs/006-toggle-acr-mode/contracts/terraform-config.md`
- [ ] T005 [P] Emit `ACR_BUILD_MODE` and null-safe `AZURE_ACR_AGENT_POOL` behavior in `infra/scripts/print-github-vars-from-terraform.sh`
- [ ] T006 [P] Update GitHub variable update logic for `ACR_BUILD_MODE` and public-mode pool handling in `infra/scripts/update-github-vars-from-terraform.sh`
- [ ] T007 Document script behavior and expected GitHub variable states in `infra/terraform/README.md`

**Checkpoint**: Foundation ready. User stories can now proceed.

---

## Phase 3: User Story 1 - Toggle ACR Build Execution Path (Priority: P1) 🎯 MVP

**Goal**: Enable public/private ACR mode in Terraform so image build execution path can be controlled and private pool cost can be removed when public mode is selected.

**Independent Test**: Apply private mode and verify private pool exists/public access blocked; apply public mode and verify pool is removed/public access enabled.

### Implementation for User Story 1

- [ ] T008 [US1] Add `local.acr_is_private` mode helper in `infra/terraform/ai-platform.tf`
- [ ] T009 [US1] Switch container registry `public_network_access_enabled` to mode-driven behavior in `infra/terraform/ai-platform.tf`
- [ ] T010 [US1] Make `azurerm_container_registry_agent_pool.acr_tasks` conditional with `count` in `infra/terraform/ai-platform.tf`
- [ ] T011 [US1] Update `infra/terraform/outputs.tf` for null-safe and `count`-index-safe agent pool output behavior after T010
- [ ] T012 [US1] Add mode-switch validation steps and expected Azure CLI checks in `specs/006-toggle-acr-mode/quickstart.md`
- [ ] T013 [US1] Add Terraform convergence matrix commands (public→private→public) in `specs/006-toggle-acr-mode/quickstart.md`

**Checkpoint**: User Story 1 is complete when Terraform mode toggling independently controls ACR access and pool lifecycle.

---

## Phase 4: User Story 2 - Deployment Workflow Adapts to Mode (Priority: P2)

**Goal**: Ensure API and frontend CD workflows choose the correct ACR build path based on configured mode.

**Independent Test**: Run each workflow once with `ACR_BUILD_MODE=public` and once with `ACR_BUILD_MODE=private`, confirming correct `az acr build` command path.

### Implementation for User Story 2

- [ ] T014 [P] [US2] Add `ACR_BUILD_MODE` environment wiring and validation step in `.github/workflows/cd-api.yml`
- [ ] T015 [US2] Implement public/private conditional image build steps in `.github/workflows/cd-api.yml`
- [ ] T016 [P] [US2] Add `ACR_BUILD_MODE` environment wiring and validation step in `.github/workflows/cd-frontend.yml`
- [ ] T017 [US2] Implement public/private conditional image build steps in `.github/workflows/cd-frontend.yml`
- [ ] T018 [US2] Align workflow error messages and prerequisite checks with contract expectations in `specs/006-toggle-acr-mode/contracts/workflow-build-path.md`
- [ ] T019 [US2] Document workflow mode selection behavior and failure remediation in `infra/terraform/README.md`

**Checkpoint**: User Story 2 is complete when both CD workflows build successfully in both modes with explicit mode-aware behavior.

---

## Phase 5: User Story 3 - Clear Operator Guidance (Priority: P3)

**Goal**: Provide clear operator documentation and examples for selecting and operating ACR modes.

**Independent Test**: A maintainer follows docs and example values from a clean clone to configure and run either mode without unstated steps.

### Implementation for User Story 3

- [ ] T020 [US3] Finalize mode decision matrix and cost rationale in `infra/terraform/README.md`
- [ ] T021 [US3] Finalize `acr_build_mode` examples and explanatory comments in `infra/terraform/terraform.tfvars.example`
- [ ] T022 [US3] Update operator runbook for mode switching and verification in `specs/006-toggle-acr-mode/quickstart.md`
- [ ] T023 [US3] Reconcile Terraform contract examples with implemented fields in `specs/006-toggle-acr-mode/contracts/terraform-config.md`
- [ ] T024 [US3] Reconcile workflow contract examples with implemented YAML in `specs/006-toggle-acr-mode/contracts/workflow-build-path.md`

**Checkpoint**: User Story 3 is complete when docs and examples are implementation-accurate and independently usable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and quality gates across all stories.

- [ ] T025 [P] Run Terraform formatting/validation for changed files in `infra/terraform/variables.tf`, `infra/terraform/ai-platform.tf`, and `infra/terraform/outputs.tf`
- [ ] T026 [P] Validate workflow syntax for `.github/workflows/cd-api.yml` and `.github/workflows/cd-frontend.yml`
- [ ] T027 Validate mode transition matrix results and record evidence in `specs/006-toggle-acr-mode/quickstart.md`
- [ ] T028 Run `uv run poe check` and capture outcome for this feature in `specs/006-toggle-acr-mode/plan.md`
- [ ] T029 Verify FR-012 by auditing for any dependency/reference on legacy alternate-topology artifacts and record evidence in `specs/006-toggle-acr-mode/plan.md`
- [ ] T030 Execute a documentation walkthrough validation for SC-005 and record evidence in `specs/006-toggle-acr-mode/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies, starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 completion.
- **Phase 4 (US2)**: Depends on Phase 2 completion; can run after US1 if preferred for lower risk.
- **Phase 5 (US3)**: Depends on Phases 3 and 4 so documentation matches final implementation.
- **Phase 6 (Polish)**: Depends on all user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Independent after foundational phase.
- **US2 (P2)**: Independent after foundational phase but references outputs/scripts from foundational tasks.
- **US3 (P3)**: Depends on implemented behavior from US1 and US2 to avoid documentation drift.

### Within Each User Story

- Implement mode logic before verification steps.
- Implement workflow branching before workflow docs reconciliation.
- Complete story checkpoint before moving to lower-priority story.

---

## Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel.
- **Phase 2**: T005 and T006 can run in parallel.
- **US1**: T012 and T013 can run in parallel after T008–T011.
- **US2**: T014 and T016 can run in parallel; T015 and T017 can run in parallel afterward.
- **Polish**: T025 and T026 can run in parallel.

---

## Parallel Example: User Story 2

```bash
# Parallel validation wiring updates
Task: "T014 Add ACR_BUILD_MODE environment wiring and validation step in .github/workflows/cd-api.yml"
Task: "T016 Add ACR_BUILD_MODE environment wiring and validation step in .github/workflows/cd-frontend.yml"

# Parallel build-step updates after validation wiring
Task: "T015 Implement public/private conditional image build steps in .github/workflows/cd-api.yml"
Task: "T017 Implement public/private conditional image build steps in .github/workflows/cd-frontend.yml"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate Terraform toggling and convergence using quickstart checks.
4. Deploy/demo cost-saving public mode behavior.

### Incremental Delivery

1. Deliver US1 (mode-driven Terraform toggle).
2. Deliver US2 (mode-aware CD workflows).
3. Deliver US3 (operator-ready docs).
4. Complete Polish phase and quality gates.

### Team Parallel Strategy

1. Team completes Setup + Foundational together.
2. Then split:
   - Engineer A: US1 Terraform resource toggles.
   - Engineer B: US2 workflow branching.
   - Engineer C: US3 docs and contract reconciliation (after US1/US2 merge points).

---

## Notes

- Tasks preserve the explicit scope constraint: Terraform changes are limited to `infra/terraform/`.
- No tasks target legacy alternate-topology artifacts.
- Every task includes a concrete path and can be executed by an LLM without extra context.
