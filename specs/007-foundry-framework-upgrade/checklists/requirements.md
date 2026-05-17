# Specification Quality Checklist: Foundry Agent Framework Upgrade & Portal Trace Correlation

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-05-17 (rescoped)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) beyond named scope. The framework name (`agent-framework`), the new attribute (`agent_reference`), and the GA protocol name (Responses) appear as scope identifiers, not implementation guidance.
- [x] Focused on user value and business needs (escape rc4, unblock 008's portal-side "Analyze Results", preserve working multi-turn behavior).
- [x] Written for non-technical stakeholders. The user stories describe what an operator or developer observes, not what code paths change.
- [x] All mandatory sections completed.

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain.
- [x] Requirements are testable and unambiguous. Each FR has either a grep test, a portal verification, or a behavioral acceptance scenario.
- [x] Success criteria are measurable. SC-001/004 are repo greps; SC-002/005 are A/B comparisons against the pre-upgrade baseline; SC-003 is a portal observation with a stated time bound; SC-006 is a discoverability check via OTel attributes.
- [x] Success criteria are technology-agnostic where they describe outcomes (user-visible behavior, portal state). Where they reference internals (lockfile contents, OTel attribute names), the reference is intrinsic to the work and unavoidable.
- [x] All acceptance scenarios are defined with Given/When/Then form.
- [x] Edge cases are identified (framework symbol rename within 1.4.x, stale configured agent id, transient 429s, expired conversation ids).
- [x] Scope is clearly bounded. The "Out of Scope" section explicitly excludes the threads/runs migration that the earlier version of this spec wrongly proposed.
- [x] Dependencies and assumptions identified. The relationship to feature 008 is one-directional (008 depends on 007's FR-004) and stated explicitly.

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria.
- [x] User scenarios cover primary flows (framework upgrade, portal correlation, code cleanup).
- [x] Feature meets measurable outcomes defined in Success Criteria.
- [x] No implementation details leak into specification beyond what is unavoidable for a framework-upgrade feature.

## Notes

- This feature was originally scoped as a "migration to Foundry Agent Service threads" (a switch to `AzureAIAgentClient` + `thread_id`). Research established that this is the legacy/classic surface (visible in `learn.microsoft.com/azure/foundry-classic/...` paths and in `azure.ai.agents<1.0.0b10`) and not the direction the new Foundry portal uses. The new portal Traces tab columns are **Trace ID** and **Conversation ID** — the GA Responses protocol primitives, which Cadence already uses correctly today. The spec was rescoped to reflect this on 2026-05-17.
- The most concrete user pain point ("Analyze Results is greyed out in the existing `cadence-eval-v1` evaluation") is addressed by FR-004 (set `agent_reference` on responses calls). A repository grep confirmed zero current usages of `agent_reference` in `src/backend/`, making this the most likely single root cause.
