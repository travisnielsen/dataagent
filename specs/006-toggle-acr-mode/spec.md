# Feature Specification: ACR Mode Toggle

**Feature Branch**: `006-toggle-acr-mode`
**Created**: 2026-04-25
**Status**: Draft
**Input**: User description: "Create a new feature that supports toggling Azure Container Registry between public and private mode for private-networking deployments, including ACR agent pool deletion when public, public network access enablement, CI workflow mode switching, and documentation/tfvars example updates."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Toggle ACR Build Execution Path (Priority: P1)

As an infrastructure operator, I can set ACR mode to either public or private in the private-networking deployment so I can control where container images are built—via a private build agent pool or the public Azure-hosted ACR build task—enabling cost optimization when network isolation is not required.

**Why this priority**: This is the core requested capability and directly controls container image build execution and operational costs.

**Independent Test**: Can be fully tested by configuring each mode and verifying that new container image builds execute on the correct build path (private agent pool or public ACR build task) and that infrastructure changes reflect the selected mode.

**Acceptance Scenarios**:

1. **Given** a private-networking deployment configured for private mode, **When** infrastructure is applied, **Then** container images can be built via the private ACR build agent pool, and ACR disallows public network access.
2. **Given** a private-networking deployment configured for public mode, **When** infrastructure is applied, **Then** container images can be built via the public Azure-hosted ACR build task, and ACR allows public network access with no private build agent pool present.
3. **Given** an existing private-mode deployment with a private build agent pool, **When** mode is changed to public and infrastructure is applied, **Then** the private build agent pool is removed, and subsequent container image builds can only use the public ACR build task.

---

### User Story 2 - Deployment Workflow Adapts to Mode (Priority: P2)

As a release engineer, I can run API and frontend continuous deployment workflows that select the correct image build path for the configured ACR mode so deployments continue to work in both private and public ACR configurations.

**Why this priority**: Infrastructure changes are incomplete unless deployment automation follows the same mode selection.

**Independent Test**: Can be fully tested by running each deployment workflow once in each mode and verifying successful image build and push behavior using the expected execution path for that mode.

**Acceptance Scenarios**:

1. **Given** deployments are configured for private mode, **When** API or frontend deployment workflow runs, **Then** image builds use the private build path and complete successfully.
2. **Given** deployments are configured for public mode, **When** API or frontend deployment workflow runs, **Then** image builds use the cloud-hosted build path and complete successfully.

---

### User Story 3 - Clear Operator Guidance (Priority: P3)

As a platform maintainer, I can use updated infrastructure and deployment documentation, including example variable values, so I can confidently configure and operate either ACR mode.

**Why this priority**: Documentation reduces configuration errors and shortens onboarding time for future maintenance.

**Independent Test**: Can be fully tested by following the documentation from a clean environment to configure both modes without requiring unstated steps.

**Acceptance Scenarios**:

1. **Given** an operator reviews deployment docs, **When** they locate ACR mode settings, **Then** they can identify valid values, expected behavior, and related deployment workflow behavior.
2. **Given** an operator uses the example variables file, **When** they prepare deployment values for either mode, **Then** the file provides clear, usable examples aligned with implementation.

---

## Clarifications

### Session 2026-04-25

- Q: Should there be a default ACR mode, or must it always be explicitly set? → A: Default mode is public to optimize cost out of the box. Private mode can be explicitly selected when network isolation is required. This reflects the primary motivation for this feature: reducing operational costs by eliminating the expensive private ACR build pool when it is not needed.

---

### Edge Cases

- Mode value is missing or invalid at deployment time.
- Existing ACR private agent pool deletion is delayed or partially fails during a mode switch to public.
- Workflows are configured for one mode while infrastructure is configured for the other mode.
- Repeated applies are executed without mode changes and must not create drift or duplicate resources.
- A switch from public back to private must recreate required private build resources predictably.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The private-networking deployment configuration MUST expose a single ACR mode setting with allowed values of public or private, defaulting to public to optimize for cost-efficiency.
- **FR-002**: When ACR mode is private, deployment MUST configure ACR to block public network access.
- **FR-003**: When ACR mode is private, deployment MUST create and maintain the private ACR build agent pool.
- **FR-004**: When ACR mode is public, deployment MUST configure ACR to allow public network access.
- **FR-005**: When ACR mode is public, deployment MUST ensure no private ACR build agent pool exists after apply, including deleting an existing one created by private mode.
- **FR-006**: Switching modes between consecutive applies MUST converge to the target mode state without manual cleanup steps.
- **FR-007**: API and frontend continuous deployment workflows MUST select build execution behavior based on the configured ACR mode.
- **FR-008**: API and frontend continuous deployment workflows MUST fail with a clear actionable message when mode-specific build prerequisites are missing.
- **FR-009**: Documentation MUST describe ACR mode behavior, operator decision guidance for each mode, and required configuration points.
- **FR-010**: The example infrastructure variable file for private-networking MUST include ACR mode configuration examples and explanatory comments.
- **FR-011**: The feature scope MUST apply only to deployments using private-networking.
- **FR-012**: The feature MUST not require any dependency on the public-networking deployment path.

### Key Entities *(include if feature involves data)*

- **ACR Mode Configuration**: Deployment setting that defines desired ACR exposure state; key attributes include mode value and deployment scope.
- **Registry Access State**: Effective ACR network accessibility outcome; key attributes include whether public network access is enabled or blocked.
- **Build Agent Pool State**: Existence state of private ACR build pool resources; key attributes include present or absent status and lifecycle transitions during mode changes.
- **Deployment Workflow Mode Input**: Workflow-level configuration value used to select the build path; key attributes include mode alignment with infrastructure and validation status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In acceptance testing, 100% of private-mode deployments result in public registry access blocked and private build pool present.
- **SC-002**: In acceptance testing, 100% of public-mode deployments result in public registry access enabled and private build pool absent.
- **SC-003**: In acceptance testing, mode-switch operations (private to public and public to private) complete in a single deployment cycle without manual remediation in at least 95% of runs.
- **SC-004**: API and frontend deployment workflows each complete successfully in both modes in at least 95% of validation runs.
- **SC-005**: At least 90% of maintainers can correctly configure the target mode using only updated documentation and example variable files in a documentation walkthrough test.

## Assumptions

- Cost optimization is the primary driver: the private ACR build pool is expensive, and enabling a public mode default allows operators to reduce costs when network isolation is not required.
- Private-networking is the only supported deployment topology for this feature.
- Existing private-mode behavior is treated as the baseline and must remain functionally unchanged when mode is private.
- Workflow mode selection can be driven by repository-level configuration without introducing additional manual steps per run.
- Operators have permissions required to create, update, and delete ACR-related resources in the target environment.
- Public-networking artifacts may be removed in the future and are intentionally excluded from this feature scope.
