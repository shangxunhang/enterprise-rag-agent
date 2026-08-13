# Enterprise Construction Plan Agent-RAG: Multi-Agent V2 Implementation Plan

Date: 2026-08-09  
Status: Frozen implementation baseline / V2-00 Architecture-to-Code Gap Audit completed 2026-08-09  
Repository: `D:\MyCode\rag-agent`  
Architecture baseline:

- `docs/多agent架构_V2_收敛版_20260809.md`
- `docs/RAG架构_V2_收敛版_20260809.md`

Historical references only:

- `docs/AGENT_RAG_MULTI_AGENT_V1_COMPLETE_BUILD_PLAN_20260803.md`
- `docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_PLAN_20260805.md`
- `docs/AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md`

This document supersedes the old V1 "continue adding features" implementation direction. It does not delete historical code or documents. It defines the only allowed implementation scope for Multi-Agent V2.

---

# 1. Why this plan exists

The current problem is no longer lack of ideas. The project already has too many partially overlapping concepts:

- current production Supervisor / LangGraph;
- old Standalone V1 fixture/runtime;
- Human Review semantics;
- Version / Impact / Invalidation semantics;
- Paragraph / Claim / Recovery components;
- Context Manager;
- Tool Gateway;
- Trace / Capture;
- Adaptive / C-RAG / Self-RAG variants;
- historical Multi-Agent plans.

If development continues directly from old documents, there is a high risk of completing outdated V1 capabilities that are no longer business requirements.

Therefore the goal of V2 implementation is not:

> complete every previously designed Multi-Agent capability.

The goal is:

> keep one production control plane and four business Agents, and deepen only Memory/Context, Recovery/Idempotency, Trace/Eval, controlled RAG and strong contracts.

---

# 2. Frozen business scope

## 2.1 Only one main business product

The V2 product produces a text-centric structured construction-plan DOCX.

The system is responsible for:

```text
Current project inputs
+ Excel factual data
+ current project materials
+ enterprise knowledge / historical references
        ↓
controlled RAG + Multi-Agent
        ↓
construction-plan body text
        ↓
Document Verify + Hard Gate
        ↓
DOCX + Review Report
```

## 2.2 Excel boundary

Excel is parsed by deterministic services into a `FactSnapshot`.

Agents do not:

- directly parse raw cells;
- modify Excel;
- calculate investment estimates;
- generate the investment-estimate table;
- invent missing equipment quantities/models/prices.

Investment-estimate tables and other existing deterministic business tables remain manually pasted into the final Word document when needed.

## 2.3 Explicit non-goals

V2 does not add:

- TableAgent;
- MemoryAgent;
- CriticAgent;
- ReflectionAgent;
- second Supervisor;
- second Router control plane;
- second Checkpoint system;
- second TaskStatus system;
- automatic investment calculation;
- automatic output tables;
- topology drawing;
- CAD / rack / port / cabling generation;
- complex DOCX template reproduction;
- cross-user personality memory;
- cross-run major/minor version reuse graph;
- cross-run FactDiff → Claim → Summary propagation;
- runtime Human Review as a normal execution strategy;
- MongoDB / Neo4j / microservice expansion solely for architecture completeness.

---

# 3. Frozen production architecture

The only production chain remains:

```text
MainlineApplicationService
    ↓
SupervisorAgent
    ↓
EnterpriseDocumentWorkflowEngine
    ↓
SectionExecutionGraph
```

The only business Agents are:

```text
PlannerAgent
ResearchAgent
WriterAgent
VerifierAgent
```

Core engineering principle:

> Agent performs nondeterministic cognitive work; Service / Graph performs deterministic engineering work.

Therefore:

```text
LLM / Agent owns:
planning semantics
retrieval decision
writing
semantic verification

Deterministic runtime owns:
state transition
DAG
budget
retry
permission
checkpoint
idempotency
artifact commit
final hard gate
```

---

# 4. Current code reality: what already exists

The implementation plan must reuse existing code instead of recreating it.

## 4.1 Production runtime already exists

Confirmed existing capabilities include:

- `SupervisorAgent`;
- production top-level LangGraph;
- production Section graph;
- official SQLite LangGraph checkpointer;
- TaskManager / workflow state;
- model gateway;
- RAG service;
- trace/capture;
- section parallel execution;
- side-effect commit/idempotency infrastructure;
- Paragraph-related schemas/services;
- Claim and evidence gate infrastructure.

These are not V2 rewrite targets.

## 4.2 Existing Context Manager is valuable infrastructure

Current repository already contains:

```text
backend/context_manager/manager.py
backend/context_manager/policies.py
backend/context_manager/token_estimator.py
```

`LLMContextManager` already provides:

- deterministic ordering;
- required vs optional items;
- token/character budget;
- truncation/drop decisions;
- deterministic token estimation;
- rendered context hash;
- selection decision records;
- compatibility passthrough mode.

`SectionGenerationContextPolicy` already provides:

- current section context candidates;
- project core facts;
- RAG evidence candidates;
- citation catalog;
- previous-section summary candidates;
- priority and required flags.

Therefore V2 must NOT create a second independent context system.

The correct evolution is:

```text
Existing LLMContextManager
        ↓
becomes the deterministic selection/compaction engine
        ↑
new Role-aware / Memory-aware ContextCompiler feeds it candidates
```

## 4.3 Existing old Standalone V1 is reference/fixture only

`backend/apps/enterprise_document/runtime/agent_rag_v1/` is already marked archived/fixture in multiple files.

Its components may be reused as behavior references, but must not be restored as production control plane.

## 4.4 Human Review still exists broadly in current code

Current production schemas/runtime still contain `human_review` fields and routes.

V2 decision:

- do not immediately mass-delete these fields;
- stop expanding them;
- remove them from the intended normal V2 happy path;
- only delete/retire when tests and compatibility impact are known.

This avoids turning scope reduction into a risky repository-wide cleanup project.

---

# 5. Implementation strategy

V2 uses a vertical-slice strategy.

Do NOT first implement all Memory schemas, all Agent contracts, all recovery, all RAG and only then run E2E.

Use one representative technical section, preferably "compute resources", and gradually make it pass through the new runtime semantics.

Each phase must satisfy:

```text
small code scope
single authority
mainline integration
negative tests
trace evidence
no new parallel runtime
```

---

# 6. Phase V2-00: Architecture-to-Code Gap Audit

## Goal

Before modifying behavior, map the new V2 architecture onto the actual repository.

This phase is mandatory and code-change-free except for audit documentation/tests if necessary.

## Questions to answer

### Control plane

- Which top-level nodes are currently active?
- Which Section nodes are currently active?
- Which routes still depend on Human Review?
- Which old Standalone components are still imported by production code?

### Agents

- Where is Planner actually invoked?
- Where is Research actually invoked?
- Does Writer generate Paragraphs itself or still consume legacy section generation?
- Which Verifier modes are truly active?

### State / Artifact

- Where are Paragraphs committed?
- Where are Claims committed?
- Where is EvidencePackage persisted?
- What is the canonical attempt identity?
- What fields already support recovery?

### Context

- Which model calls use `LLMContextManager`?
- Which calls still assemble prompts manually?
- Which contexts currently inject raw previous sections?
- Which existing context decision records can become V2 Memory Context Trace?

### Recovery

- What does LangGraph checkpoint currently restore automatically?
- What side effects already use idempotency?
- What is missing for Research Round and Paragraph-level recovery?

### Trace / Eval

- What structured decision fields are already logged?
- Which Adaptive/C-RAG/Self-RAG signals already exist?
- Which metrics can already be computed from current traces?

## Deliverable

Create one gap matrix:

| V2 Capability | Existing Code | Status | Action |
| --- | --- | --- | --- |
| Role-aware context | context_manager exists | UPGRADE | extend existing capability |
| Research history | existing durable sources/trace | UPGRADE | project/query existing capability |
| Paragraph resume | existing paragraph/idempotency base | REUSE / UPGRADE | reuse and close only proven gaps |
| Human review normal route | existing compatibility path | LEGACY_COMPAT | STOP_EXPANDING |
| Cross-run invalidation | old design only | STOP_EXPANDING | do not implement |

Audit status vocabulary is frozen to:

```text
REUSE
UPGRADE
MISSING
LEGACY_COMPAT
STOP_EXPANDING
```

Do not introduce additional audit statuses such as `partial`, `remove_from_main_path`, or architecture-specific synonyms.

## Exit condition

No implementation starts until every P0 capability has one canonical code owner.

---

# 7. Phase V2-01: Freeze the V2 Runtime Contract

## Goal

Prevent old V1 concepts from silently re-entering the implementation.

## Work

Define the minimal canonical contracts used by the production mainline.

### RunContext

```yaml
RunContext:
  task_id
  run_id
  input_snapshot_id
  model_version
  prompt_version
  workflow_version
  knowledge_index_version
```

One complete input submission creates one independent Run.

No cross-run stale propagation in V2.

### SectionPlan

Keep only:

```yaml
section_id
section_title
applicable
section_strategy
required_facts
evidence_topics
dependencies
consistency_refs
risk_hints
allow_suggested_design
parallel_group
```

Allowed strategy values:

```text
source_adapt
rag_generate
technical_generate
skip
```

No `human_review` strategy.

### EvidenceRequirement / EvidencePackage

The canonical `EvidenceRequirement` and `EvidencePackage` contracts are owned by RAG V2 (`docs/RAG架构_V2_收敛版_20260809.md`). Multi-Agent must not define a parallel schema family.

Planner produces runtime EvidenceRequirement instances that conform to the RAG-owned contract. Research produces/commits the RAG-owned EvidencePackage. Agent contracts may carry references or compatible projections only.

Frozen semantic rule:

```text
evidence_status != stop_reason
```

`budget_exhausted`, `no_gain`, and `max_rounds` are stop reasons, not Evidence statuses.

### DraftPackage

Must contain:

```yaml
section_id
paragraphs
claims
bindings
generation_status
need_more_evidence
```

### VerificationResult

Allowed top-level semantic results:

```text
passed
repairable
need_more_evidence
review_required
```

`review_required` means final Review Report / unresolved engineering judgment, not runtime human interrupt.

## Constraints

Do not introduce a second state schema family if existing production payloads can carry these fields.

## Exit condition

Four Agents communicate via stable structured contracts; no free-form Agent-to-Agent chat is needed.

---

# 8. Phase V2-02: Minimal Run Memory Projection

## Goal

Provide durable run-level memory without creating a second business truth database.

## Important implementation decision

Do NOT start by creating a large standalone `SQLiteMemoryStore`.

First reuse current:

```text
GraphState
Checkpoint
Artifact references
Claims
Evidence contracts
Trace/events
```

Then add a lightweight projection/query layer only where current access is insufficient.

## Minimal memory subjects

Only support:

```text
fact
evidence
verified_claim
research_history
writer_progress
verifier_issue
decision
artifact_ref
```

No broad memory taxonomy.

## Event-driven write rule

Only committed operations can produce durable memory:

```text
ResearchRoundCommitted
EvidencePackageCommitted
ParagraphCommitted
ClaimVerified
VerifierIssueCreated
VerifierIssueRepaired
VerifierIssueResolved
SectionAccepted
DecisionCommitted
```

LLM itself cannot directly write long-term memory.

## Lifecycle

Lifecycle is type-specific and stays minimal within the same Run.

Shared projection states use only what the subject actually needs, primarily:

```text
active
resolved
superseded
invalid
```

`verifier_issue` specifically freezes the review lifecycle as:

```text
active
→ repaired
→ resolved
```

Do not introduce TTL/expiration machinery or a broader lifecycle framework merely for schema completeness.

## Must prove

- stale attempt output cannot become active memory;
- unresolved issue can be retrieved later;
- old paragraph version can be superseded;
- verified claim can be shared across dependent sections;
- no cross-run automatic invalidation is implemented.

## Exit condition

A committed Research/Writer/Verifier event can be projected into a retrievable run memory record without becoming a second source of truth.

---

# 9. Phase V2-03: ContextCompiler V2

## Goal

Turn the existing Context Manager into a true role-aware, memory-aware Context Engineering layer.

## Reuse

Keep `LLMContextManager` as the deterministic budget/selection/compaction engine.

Do not replace it.

## Add one orchestration layer

Conceptually:

```text
AgentInvocationRequest
    ↓
Role Policy
    ↓
Memory / Fact / Evidence candidate retrieval
    ↓
Authority + lifecycle filtering
    ↓
Candidate ContextItem generation
    ↓
existing LLMContextManager
    ↓
AgentMemoryPackage / LLMContextPackage
```

## Role policies

### Planner

Reads:

- TemplateSchema;
- FactSnapshot;
- SourceInventory;
- relevant plan decisions.

Does not read:

- raw Writer drafts;
- raw Research traces.

### Research

Reads:

- evidence requirements;
- relevant facts;
- query/action history;
- accepted/rejected evidence summary;
- no-gain history;
- current gaps.

### Writer

Reads:

- SectionPlan;
- relevant Fact subset;
- EvidencePackage;
- committed Paragraph summaries/current cursor;
- unresolved issues;
- authorized cross-section Verified Claims.

Does not read:

- other section raw draft;
- entire Research reasoning;
- all tool raw results.

### Verifier

Reads:

- current paragraph/section;
- claims;
- fact/evidence bindings;
- unresolved issues;
- draft diff;
- required cross-section Verified Claims.

## Compression levels

### Level A: no-loss structured facts

Never summarize away:

```text
fact_id
model
quantity
amount
formula result
claim_id
evidence_id
status
```

### Level B: structured compression

Convert long Evidence/Issue/Research histories into stable structured records.

### Level C: controlled summaries

Use for old attempts / accepted section summaries; keep IDs/lineage.

### Level D: reference-only

Large artifacts remain refs until explicitly needed.

## Trace

Every invocation records:

```yaml
agent_role
requested_memory_types
selected_refs
omitted_refs
rejection_reasons
tokens_before
tokens_after
compaction_applied
package_hash
```

## Eval

At minimum compare:

```text
legacy/full-history or naive history
vs
role-aware context compiler
```

Measure:

- fact retention;
- hallucination;
- input tokens;
- latency;
- task quality.

## Exit condition

One Writer invocation can prove exactly why each context item was selected or omitted, and key facts survive budget pressure.

---

# 10. Phase V2-04: Research Round Recovery + Retrieval Memory

## Goal

Make Research a bounded resumable loop rather than a stateless one-shot retriever.

## Execution unit

`ResearchRound` is the atomic research progress unit.

Each committed round stores:

```yaml
round_id
current_evidence_state
selected_retrieval_action
query/action fingerprint
tool_call_refs
accepted_evidence_delta
rejected_evidence_delta
coverage_delta
no_gain
stop_reason
```

## Recovery behavior

```text
round1 committed
round2 committed
round3 crashes
```

Resume:

```text
load round1/round2 summary
reconstruct current evidence state
continue from round3
```

No replay of already committed tool work unless explicitly required by failed side-effect semantics.

## No-duplicate behavior

Use fingerprints for equivalent retrieval actions/queries to prevent meaningless repeats.

## RAG integration

This phase follows the separate RAG V2 architecture:

```text
Adaptive Router
→ retrieval action
→ ToolGateway
→ retrieval result
→ C-RAG-like evaluator
→ evidence state
→ continue/stop
```

Do not add more Agents.

## Exit condition

Research can crash and resume without redoing committed rounds, and Trace explains why it continued or stopped.

---

# 11. Phase V2-05: Writer Paragraph Recovery + Idempotency

## Goal

Make Paragraph the smallest committed generation unit.

## Flow

```text
ContextCompiler(Writer)
→ generate paragraph
→ commit Paragraph Artifact
→ claim extraction/binding
→ deterministic paragraph precheck
→ if pass: advance cursor
→ if repairable: repair current paragraph
→ if evidence gap: return structured need_more_evidence
```

## Stable identity

Use stable logical keys, reusing current infrastructure where possible:

```text
run_id
section_id
attempt_id
paragraph_id
operation_id
```

Build idempotency keys around committed side effects.

## Recovery behavior

```text
p001 committed
p002 committed
p003 crashes
```

Resume from p003.

Do not regenerate p001/p002.

## Important boundary

Writer cannot call retrieval directly.

If evidence is missing:

```text
Writer → need_more_evidence
Supervisor/graph → Research
Research → updated EvidencePackage
Writer resumes
```

## Exit condition

A forced p003 crash resumes correctly, committed paragraphs are not duplicated, and the Trace shows the recovery entry point.

---

# 12. Phase V2-06: Verifier Issue Lifecycle + Cross-Section Verified Claims

## Goal

Make semantic verification durable and reusable without sharing raw drafts.

## Issue lifecycle

```text
issue active
→ Writer repair committed
→ issue repaired
→ Verifier recheck
→ issue resolved
```

Stable issue identity prevents duplicate issue creation and distinguishes "repair committed but not yet re-verified" from a resolved issue.

## Cross-section memory

Only structured `Verified Claim` may propagate as cross-section factual memory.

A section summary may still be produced when ContextCompiler needs compact semantic context, but it is only a derived Context Projection from existing `artifact_ref` / `writer_progress`; it is **not** a ninth Durable Memory type and does not gain factual authority.

Never propagate raw unverified drafts by default.

## Example

Compute section verifies:

```text
server_count = 16
```

Network section may consume the verified claim.

It must not consume the raw compute draft as factual authority.

## Exit condition

A dependent section can read a verified claim, an unresolved issue survives a repair cycle, and raw-draft leakage is rejected by policy/test.

---

# 13. Phase V2-07: Supervisor Control Plane Cleanup

## Goal

Make the production routing semantics match the V2 architecture without rewriting Supervisor.

## Deterministic routes

Keep deterministic:

```text
research sufficient → writer
need_more_evidence → research
writer completed → verifier
repairable → writer repair
budget exhausted → stop/degrade
hard dependency complete → unlock
hard gate fail → no publish
```

## Supervisor LLM

Not required for initial V2.

Only add later if Trace proves there are frequent ambiguous semantic routing cases that cannot be represented by existing typed results.

## Human Review

Do not mass-delete all legacy fields in this phase.

Instead:

1. ensure the normal V2 happy path does not choose runtime Human Review;
2. convert unresolved engineering judgment into `review_required` / Review Report output;
3. preserve legacy schema compatibility until targeted cleanup is safe;
4. do not add new Human Review state transitions.

## Exit condition

The full normal V2 mainline executes without runtime Human Review or a Supervisor LLM.

---

# 14. Phase V2-08: Decision Trace + Evaluation Signals

## Goal

Turn the project from "works" into "measurable and explainable" while keeping Evaluation authority in the independent Evaluation Foundation.

## Trace types

### Context decision

```text
what entered context
what was omitted
why
before/after tokens
```

### Research decision

```text
evidence need
current evidence state
selected retrieval action
reason code
confidence
result gain
continue/stop reason
```

### Writer decision

```text
facts used
evidence used
paragraph id
self-rag trigger
candidate count
selected candidate
```

### Verifier decision

```text
issue type
target
severity
binding state
recommended route
resolution
```

### Supervisor route

```text
input statuses
selected deterministic route
budget/checkpoint state
```

Do not store private model chain-of-thought. Store structured decision summaries and observable outputs.

## Required observable signals

The fields below are signals/diagnostics that Multi-Agent must expose. Formal MetricDefinition, Gold, Judge, Result and Report semantics belong to `RAG_EVALUATION_FOUNDATION_IMPLEMENTATION_PLAN_20260809.md`; Multi-Agent does not create a second Eval authority.

### Memory/Context

- context tokens per invocation;
- critical fact retention rate;
- context omission reasons;
- cross-section leakage failures;
- compression ratio.

### Research

- evidence coverage;
- no-gain retrieval rate;
- average tool calls;
- retrieval latency/cost;
- router accuracy/utility.

### Writer

- fact hallucination rate;
- Self-RAG trigger rate;
- repair success rate;
- generation attempts.

### Recovery

- resume correctness;
- duplicate side-effect count;
- stale attempt rejection.

## Exit condition

At least one baseline-vs-improved A/B can be reproduced from traces.

---

# 15. Phase V2-09: RAG V2 Integration

This phase does not invent a new RAG design. It implements the already frozen `RAG架构_V2_收敛版_20260809.md`.

## Research side

```text
Planner evidence need
→ Adaptive-RAG-like Router
→ ToolGateway retrieval action
→ C-RAG-like evidence evaluation
→ bounded Research loop
→ EvidencePackage
```

## Writer side

Normal generation first.

For `technical_generate` only, if trigger metrics indicate low quality:

```text
candidate_1
→ independent generation evaluator critique
→ targeted rewrite candidate_2
→ if still insufficient: independent candidate_3
→ select best
→ stop
```

No combinatorial evidence-context candidate explosion.

## Backtrack rule

Only explicit `evidence_gap` can return to Research.

`generation_quality` alone stays inside generation repair.

## Exit condition

RAG behavior is bounded, traced, and evaluated; no infinite Research↔Writer loop exists.

---

# 16. Phase V2-10: One Real E2E Closure

## Goal

Prove the base architecture with one real construction-plan scenario before expanding sections or features.

## Required scenario

Use one representative project containing:

- Excel facts;
- current project text materials;
- enterprise/historical retrieval evidence;
- at least one technical section;
- at least one cross-section consistency claim.

## Required assertions

### Input/facts

- raw Excel is parsed by service;
- Writer does not read raw Excel;
- Excel-authoritative facts win against historical conflict.

### Research

- multiple rounds are bounded;
- equivalent query/action is not repeated unnecessarily;
- C-RAG evidence state determines stop/continue;
- crash recovery resumes correctly.

### Writer

- paragraph commit is incremental;
- p003 recovery works;
- evidence gap returns via supervisor;
- no unsupported hard fact passes.

### Context

- role-aware package is traceable;
- token budget compression preserves key facts;
- unverified cross-section draft is not injected.

### Verifier

- issue lifecycle closes;
- verified claims propagate safely.

### Output

- Document Verify runs;
- deterministic Hard Gate retains final authority;
- PASS publishes the normal text-centric DOCX;
- FAIL produces rejected output/Review Report according to current safe behavior;
- no automatic investment table generation is introduced.

---

# 17. Work that is explicitly NOT part of the implementation path

The following old V1 items must not block V2 completion:

```text
Excel update 6→8 cross-run invalidation demo
Major Version impact graph
Minor Version partial reuse workflow
Human Review resume demo
complete failed skeleton document framework
complex multi-version document publication system
new memory database technology
new agent society
new user-facing UI
new tool ecosystem
new document types
```

If legacy code already supports some of these, leave it alone unless it interferes with V2 mainline or causes correctness problems.

---

# 18. Recommended execution order

The actual sequence is:

```text
V2-00  Gap Audit
  ↓
V2-01  Contract Freeze
  ↓
V2-02  Minimal Run Memory Projection
  ↓
V2-03  ContextCompiler V2
  ↓
V2-04  Research Recovery + Retrieval Memory
  ↓
V2-05  Writer Paragraph Recovery + Idempotency
  ↓
V2-06  Verifier Issue + Verified Claim Memory
  ↓
V2-07  Supervisor route cleanup
  ↓
V2-08  Decision Trace + Evaluation Signals
  ↓
V2-09  RAG V2 integration
  ↓
V2-10  One real E2E + A/B
```

Important:

- phases can reuse already-existing implementations;
- a phase may become "audit confirms already complete" and require little/no code;
- do not create code merely to satisfy the phase name;
- no new phase is added without a concrete business/technical gap.

---

# 19. Priority levels

## P0: must be technically deep

```text
Memory Projection
ContextCompiler / Compression
Research Round Recovery
Paragraph Recovery / Idempotency
Decision Trace / Evaluation Signals
```

## P1: must be clean, not necessarily large

```text
Supervisor deterministic routing
Tool Gateway authority
Agent Contracts
Section DAG
Cross-section Verified Claims
```

## P2: keep minimal

```text
DOCX rendering
Review Report formatting
UI
legacy compatibility cleanup
```

---

# 20. Scope-control gate for every proposed task

Before adding any implementation task, answer:

## Question A

Does it directly improve one of:

```text
Memory/Context
Recovery/Idempotency
Trace/Eval
RAG decision/evidence/generation quality
Agent contract/control correctness
```

If no, it is probably out of V2 scope.

## Question B

Can the need be satisfied by an existing Service/Graph instead of a new Agent/component?

If yes, reuse the existing component.

## Question C

Does the task create a second source of truth/control plane?

If yes, reject it.

## Question D

Can its benefit be demonstrated by a real trace/test/metric?

If no, do not prioritize it for "technical depth".

---

# 21. Definition of Done

Multi-Agent V2 is complete when all conditions below are true.

## Architecture

- exactly four business Agents;
- one Supervisor;
- one top-level graph;
- one Section graph;
- one production checkpoint authority;
- no production Standalone control plane.

## Memory/Context

- Agents are stateless across invocations;
- committed events can be projected into run-level memory;
- ContextCompiler is role-aware;
- existing Context Manager is reused for deterministic budget/compaction;
- critical facts survive compression;
- selected/omitted context is traceable;
- only verified cross-section claims propagate.

## Recovery

- Research Round resume works;
- Paragraph resume works;
- side-effect replay is idempotent;
- stale attempts cannot overwrite active state.

## RAG

- Adaptive retrieval decision is traced;
- C-RAG-like evidence quality/coverage loop is bounded;
- Self-RAG-like generation fallback is only triggered when necessary;
- evidence-gap backtrack is bounded.

## Eval

- at least one Context A/B;
- at least one RAG routing/evidence A/B;
- recovery tests prove no duplicate committed work;
- hallucination/fact consistency is measurable.

## Business output

- real construction-plan DOCX can be produced;
- Excel facts are respected;
- investment estimate table is not generated by the Agent system;
- Hard Gate remains final deterministic release authority;
- Review Report handles unresolved engineering judgment after generation rather than normal runtime interruption.

---

# 22. Immediate next action

`V2-00 Architecture-to-Code Gap Audit` was completed on 2026-08-09.

The audit output is frozen to the five statuses:

```text
REUSE
UPGRADE
MISSING
LEGACY_COMPAT
STOP_EXPANDING
```

Implementation now proceeds only from the finite `UPGRADE` / `MISSING` backlog already identified by that audit. Do not reopen frozen architecture decisions and do not add new phases merely because an enterprise system could contain more components.

Before each implementation task, first confirm the existing code owner and reuse path. This remains the main mechanism for preventing uncontrolled expansion.
