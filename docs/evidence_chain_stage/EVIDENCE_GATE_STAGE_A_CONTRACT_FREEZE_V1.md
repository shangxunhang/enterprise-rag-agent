# Evidence Gate Stage A Contract Freeze V1

## 1. Scope

Stage A freezes the contracts required before document classification, offline
index migration, retrieval routing, gate-policy resolution, or writer changes.
It does not modify the current runtime chain.

Frozen contracts:

| Contract | Schema version | Code location |
| --- | --- | --- |
| Document profile | `document_profile_v1` | `backend/rag/catalog/document_profile.py` |
| Evidence item assessment | `evidence_assessment_v2` | `backend/rag/schema/evidence_assessment_v2.py` |
| Evidence set assessment | `evidence_set_assessment_v1` | `backend/rag/schema/evidence_assessment_v2.py` |
| Gate decision | `gate_decision_v1` | `backend/apps/enterprise_document/schemas/gate_decision.py` |
| Delivery mode | enum | `backend/apps/enterprise_document/schemas/gate_decision.py` |

## 2. Frozen delivery modes

The only V1 delivery modes are:

```text
grounded
qualified
framework_only
blocked
human_review
```

Their contract semantics are:

- `grounded`: only `sufficient` evidence may enter this mode.
- `qualified`: only `sufficient` or `partial` evidence may enter this mode;
  qualifiers are mandatory. `partial` plus `high` or `critical` claim risk is
  rejected by the contract.
- `framework_only`: only `absent` or `not_assessed` evidence may enter this
  mode; only template structure or style guidance may be allowed.
- `blocked`: no claim type may be allowed.
- `human_review`: `human_review_required` must be true.

`conflicted` evidence may only map to `blocked` or `human_review`.

## 3. Frozen evidence statuses

The V2 evidence status set is:

```text
sufficient
partial
absent
conflicted
not_assessed
```

The old runtime `sufficient/insufficient` contract remains untouched during
Stage A. Later phases must use an explicit adapter; they must not silently map
all non-sufficient V2 states back to `insufficient`.

Set-level consistency rules:

- `sufficient`: `coverage_ratio=1.0`, no missing topics, no conflicts.
- `partial`: `0 < coverage_ratio < 1`, both covered and missing topics exist.
- `absent`: `coverage_ratio=0.0`, no covered topics or supporting counts.
- `conflicted`: at least one explicit conflict is required.
- `not_assessed`: no coverage result, evidence count, or conflict is allowed.

## 4. Frozen document boundaries

The supported V1 document types are:

```text
current_project_requirement
current_project_decision
current_project_inventory
historical_feasibility_study
historical_construction_scheme
policy_standard
technical_whitepaper
tender_requirement
document_template
unknown
```

Each document type has one required evidence role. The schema rejects role
mismatches.

Hard boundaries encoded in the contract:

1. Current-project documents must use `current_project` relation.
2. Historical project documents must use `other_project` relation.
3. Historical evidence cannot allow current-project facts, cost, quantity,
   schedule, or configuration claims.
4. Template evidence may only allow `template_structure` and `style_guidance`.
5. Unclassified documents cannot allow any claim type.
6. Effective and expiration dates must form a valid interval.
7. Allowed and forbidden claim sets must be unique and disjoint.
8. Unknown fields are rejected at all frozen contract boundaries.

## 5. Evidence item boundary

`EvidenceAssessmentV2Schema` separates:

```text
semantic_relevance
```

from:

```text
eligibility
```

An item can be semantically relevant while still being ineligible to support a
claim. Ineligible or irrelevant items cannot expose supported claim types.
Historical and template role boundaries are checked again at assessment time,
not only at catalog time.

## 6. Compatibility rule

Stage A is additive:

- no current `ResearchStatus`, `ResearchRecommendedAction`, section state, or
  writer contract is removed;
- no current retrieval or generation code consumes the new contracts yet;
- subsequent phases must introduce explicit mapping at each legacy/V2 boundary;
- persisted payloads must retain their schema version and use
  `model_dump(mode="json")` at JSON boundaries.

## 7. Test baseline

The Stage A baseline is:

```bash
.venv/Scripts/python.exe -m pytest \
  backend/tests/test_stage_a_evidence_gate_contracts.py -q
```

The test suite covers:

- JSON round-trip and enum freeze;
- document role and project-relation boundaries;
- cross-project claim leakage rejection;
- authority date validation;
- relevance versus eligibility;
- partial, absent, and conflicted set invariants;
- all five delivery modes;
- high-risk partial-evidence blocking;
- conflict assertions being rejected;
- undeclared fields being rejected.

## 8. Entry condition for Stage B

Stage B may begin only when:

1. the frozen tests pass;
2. no Stage A contract is changed implicitly to fit catalog data;
3. any required contract change increments the affected schema version or is
   explicitly reviewed as a backward-compatible addition;
4. document classification output validates against `DocumentProfileSchema`.
