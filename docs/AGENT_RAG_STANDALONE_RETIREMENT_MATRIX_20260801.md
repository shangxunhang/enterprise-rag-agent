# Agent-RAG Standalone Runtime Retirement Matrix

Date: 2026-08-01

## Decision

The production execution authority is the existing Mainline:

- `EnterpriseDocumentWorkflowEngine`
- `SectionExecutionGraph`
- official LangGraph checkpointer
- existing `TaskManager`, Hard Gate, artifact lifecycle, ModelGateway and RAG composition root

The former `runtime/agent_rag_v1` Standalone Orchestrator, RuntimeFactory and application service are no longer production entrypoints. Fake reasoners and fixture providers may remain as test data generators, but they do not own routing, checkpoints, task status or artifact publication.

## Scenario ownership

| Former Standalone scenario | Mainline E2E owner | Required assertion |
|---|---|---|
| success | `test_phase2_top_level_business_graph.py::test_production_artifact_lifecycle_and_capture_are_exactly_once` | Mainline publishes exactly one deliverable and capture |
| research-more | `test_phase4_section_agents_mainline.py::test_verifier_research_more_drives_real_section_route` | Verifier routes through a second Research round in the Section graph |
| rewrite | `test_phase8_recovery_budget_retirement.py::test_mainline_rewrite_scenario_creates_new_generation_attempt` | Verifier rewrite creates a new generation attempt and then accepts |
| paragraph-repair | `test_phase8_recovery_budget_retirement.py::test_mainline_paragraph_repair_replaces_conflicting_version` | Blocking precheck causes Paragraph v2 to replace the conflicting version |
| replan | `test_phase8_recovery_budget_retirement.py::test_mainline_replan_supersedes_attempt_and_rejects_old_result` | New plan creates Attempt 2 and stale Attempt 1 output is rejected |
| partial-success | `test_phase8_recovery_budget_retirement.py::test_mainline_partial_success_publishes_deliverable` | Partial Section remains an explicit partial-success deliverable |
| failed | `test_phase4_section_agents_mainline.py::test_explicit_insufficient_evidence_stops_before_writer_and_verifier` | Business failure produces rejected artifact and no deliverable |
| budget-exhausted | `test_phase8_recovery_budget_retirement.py::test_task_budget_draining_finishes_first_section_and_starts_no_more_agents` | In-flight Section finishes; no later Section starts an Agent call |
| human-review | `test_phase8_recovery_budget_retirement.py::test_stale_human_review_decision_keeps_durable_interrupt_paused` | Stale decision is rejected; valid decision resumes across a new engine instance |
| crash-recovery | `test_phase8_recovery_budget_retirement.py::test_section_crash_resume_reuses_committed_paragraph_without_duplication` | New process resumes from Section cursor without duplicating committed Paragraphs |

## Archived control plane

The following names are legacy fixture/reference implementations only:

- `WorkflowOrchestrator`
- `RuntimeFactory`
- `MultiAgentApplicationService`
- Standalone `WorkflowRouter`
- Standalone `ResumeRouter`
- `JsonCheckpointStore`
- Standalone `TaskStatusCalculator`

They must not be imported by the production composition root, top-level graph or Section graph. The historical CLI `scripts/run_agent_rag_v1_standalone.py` is retired and directs callers to `scripts/run_demo.py`.
