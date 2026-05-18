# Specification Quality Checklist: Foundry Trace-Based Nightly Evaluations

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-05-17 (rescoped)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond named scope. The Microsoft SDK surface (`azure-ai-projects.client.evals`, `data_source_type: azure_ai_traces`) is scope-defining and unavoidable for a feature whose purpose is to adopt that specific API.
- [x] Focused on user value (operators stop fighting greyed-out **Analyze Results**; scores reflect real production behavior, not replay traffic).
- [x] Written for non-technical stakeholders. The user stories describe what an operator observes in the Foundry portal and the workflow log.
- [x] All mandatory sections completed.

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain.
- [x] Requirements are testable. FR-001/008 are workflow-log inspection; FR-002/005 are CLI behavior; FR-003 is a portal screenshot; FR-004 is an exit-code matrix; FR-007 is a repo grep.
- [x] Success criteria are measurable. SC-001 is a portal observation; SC-002 is a 30-day rolling percentage; SC-003/004 are zero-result repository or telemetry checks; SC-005 is an end-to-end correlation match between SSE response and portal row.
- [x] Success criteria reference outcomes, not internals. SC-001 specifies portal-side affordance state; SC-003 specifies external request counters.
- [x] All acceptance scenarios are defined with Given/When/Then form.
- [x] Edge cases are identified (rotating agent id, missing prereqs, retention boundary, malformed spans).
- [x] Scope is clearly bounded. Out-of-scope explicitly excludes continuous evaluation, new evaluators, backfill, and the legacy `AIAgentConverter` path.
- [x] Dependencies and assumptions identified. The hard gating dependency on feature 007 producing `gen_ai.agent.id`-tagged traces is called out at the top and in Dependencies.

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria.
- [x] User scenarios cover primary flows (nightly job, resilience, ad-hoc CLI).
- [x] Feature meets measurable outcomes defined in Success Criteria.
- [x] No leakage of implementation details beyond what is intrinsic to "adopt the documented GA evaluation API".

## Notes

- This spec replaces an earlier draft that used a custom `AIAgentConverter`-driven replay/harvester. Research established that path is the classic-Foundry surface (`learn.microsoft.com/azure/foundry-classic/…`) and is the very thing GA `client.evals` + `azure_ai_traces` was introduced to replace.
- The user's verbatim portal symptom — "**Analyze Results** doesn't work" — is the explicit headline outcome (SC-001). Resolution requires the combination of (a) feature 007 setting `agent_reference` so `gen_ai.agent.id` appears on spans, and (b) this feature submitting via the trace-based evaluation surface so the portal links the evaluation back to the agent and its conversation.
- The existing `cadence-eval-v1` evaluation in the Foundry portal is intentionally targeted as the default `--evaluation-id` so historical trend lines are continuous after the rewrite.
