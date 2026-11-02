"""Read-only five-step diagnostic for the original FPBCA V6 DeepSeek Section 1.

Never dispatches a provider, never creates a production Owner/Task/Run, never
rewrites the historical campaign. New output is exclusively on E:.
This is an observational quality review packet, NOT a Gold review or native
formal Post-write continuation of a terminated Run.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
RUN = Path("D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T112450Z")
BUS = RUN / "business-runtime"
NATIVE = BUS / "runs"
OUTPUT_PARENT = Path("E:/rag-agent-evaluation")
SECTION_ID = "section_fpbca-real-e2e-20261009t112450z_001"
RUN_ID = "fpbca-real-e2e-20261009t112450z"

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema, VerifierSemanticResultCandidateSchema,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime, ClaimBindingError,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    scan_citation_markers,
)
from apps.enterprise_document.services.scheme_writer.citation_integrity_service import (
    CitationOccurrenceIntegrityGate,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_coverage_composition import (
    TargetPostWriteCoverageComposition,
)
from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.snapshot import load_source_snapshot
from eval.full_chain.runner import observe
from eval.full_chain.report import emit_report
from eval.full_chain.references import load_references


def h(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def j(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def jl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]


def brief(text: str, n: int = 100) -> str:
    # Never put contact numbers/addresses from source material into the public packet.
    text = re.sub(r"1[3-9][0-9]{9}", "[REDACTED_PHONE]", text or "")
    text = re.sub(r"\s+", " ", text)
    return text[:n]


def unpack_context(text: str, label: str):
    needle = "## " + label
    at = text.index(needle)
    start = text.index("{", at)
    return json.JSONDecoder().raw_decode(text[start:])[0]


def actual_writer_observation():
    rows = jl(BUS / "observed_calls.jsonl")
    requests = [
        row["request"]
        for row in rows
        if row.get("event") == "request_started"
        and (row.get("request") or {}).get("model_role") == "section_generation"
    ]
    assert len(requests) == 1, "exact one first-section Writer only"
    request = requests[0]
    responses = [
        row["response"]
        for row in rows
        if row.get("event") == "response_received"
        and row["model_call_id"] == request["model_call_id"]
    ]
    assert len(responses) == 1
    response = responses[0]
    assert request["extra"]["section_id"] == SECTION_ID
    return request, response, rows


def retrieval_observations():
    rows = jl(BUS / "observed_retrieval.jsonl")
    assert [row.get("event") for row in rows] == [
        "retrieval_started", "retrieval_finished",
        "retrieval_started", "retrieval_finished",
    ]
    pairs = []
    for i in (0, 2):
        request, response = rows[i]["request"], rows[i + 1]["response"]
        assert request["run_id"] == response["run_id"] == RUN_ID
        assert request["retrieval_scope"] == response["retrieval_scope"]
        assert request["retrieval_trace_id"] == response["retrieval_trace_id"]
        pairs.append((request, response))
    assert [row[1]["retrieval_scope"] for row in pairs] == ["document", "section"]
    return pairs


def candidate_artifacts():
    records = []
    for path in sorted((NATIVE / "artifacts").rglob("*.json")):
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("oversized archived verifier artifact")
        wrapper = j(path)
        ref = (wrapper.get("reference") or {}).get("artifact_ref", "")
        if "canonical-verifier-v2/" not in ref or not ref.endswith("/candidate"):
            continue
        content = json.loads(base64.b64decode(wrapper["content_base64"], validate=True))
        if content.get("schema_version") == "canonical_verifier_candidate_v2":
            records.append((str(path), h(path), content))
    return records


def native_claims():
    events = jl(NATIVE / "claim_verification.jsonl")
    claims = next(
        row["claim_records"] for row in events if row.get("event_type") == "claim_records_committed"
    )
    binding = {
        row["record"]["claim_ref"]["claim_id"]: row["record"]
        for row in events if row.get("event_type") == "binding_facts_committed"
    }
    support = {
        row["record"]["claim_support_subject_ref"]["subject_ref"]: row["record"]
        for row in jl(NATIVE / "claim_support.jsonl")
    }
    committed = {
        row["verifier_candidate"]["claim_ref"]["claim_id"]: row
        for row in events if row.get("event_type") == "verification_committed"
    }
    return claims, binding, support, committed, events


def read_original_postwrite_counts():
    path = NATIVE / "post_write.sqlite3"
    assert path.exists()
    con = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        allowed = ("artifact_states", "validation_runs", "validation_facts",
                   "validation_completions", "post_write_control_decisions",
                   "post_write_entries")
        known = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        counts = {
            name: con.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
            for name in allowed if name in known
        }
        return counts
    finally:
        con.close()


def five_step_analysis():
    execution = j(BUS / "execution.json")
    summary = j(BUS / "summary.json")
    assert execution["summary_status"] == "failed"
    assert execution["terminal_outcome"]["status"] == "TECHNICAL_FAILURE"
    assert execution["terminal_outcome"]["phase"] == "execute_sections"
    assert not execution["terminal_outcome"]["publication_exists"]
    project = j(RUN / "audit/PROJECT_INPUT_V3.json")
    (doc_request, doc), (sec_request, sec) = retrieval_observations()
    req, resp, calls = actual_writer_observation()
    body = resp["content"]
    assert resp["success"] and resp["finish_reason"] == "stop"
    assert req["max_tokens"] == 4096
    assert len(req["messages"]) == 5
    original_messages = "\n".join(row["content"] for row in req["messages"])
    assert "exact ASCII square-bracket marker [C1]" in original_messages
    current = req["messages"][-1]["content"]
    fact = unpack_context(current, "Current FactSnapshot relevant subset")
    evidence = unpack_context(current, "Current EvidencePackage")
    plan = unpack_context(current, "Current SectionPlan")

    retrieval = []
    for request, response in ((doc_request, doc), (sec_request, sec)):
        selected = [item for item in response["items"] if item["disposition"] == "selected"]
        dropped = [item for item in response["items"] if item["disposition"] == "dropped"]
        assert [item["evidence_id"] for item in selected] == response["selected_evidence_ids"]
        assert set(response["selected_evidence_ids"]).isdisjoint(response["dropped_evidence_ids"])
        retrieval.append({
            "scope": response["retrieval_scope"],
            "request_trace_id": request["retrieval_trace_id"],
            "candidate_count": len(response["items"]),
            "selected_count": len(selected),
            "dropped_count": len(dropped),
            "assessment": (response.get("assessment") or {}).get("status"),
            "packing_strategy": (response.get("extra") or {}).get("packing_strategy"),
            "token_budget": (response.get("extra") or {}).get("token_budget"),
            "tokens_used": (response.get("extra") or {}).get("tokens_used"),
            "items": [
                {
                    "evidence_id": item["evidence_id"],
                    "disposition": item["disposition"],
                    "drop_reason": item.get("drop_reason"),
                    "rank": item.get("rank"),
                    "rerank_score": item.get("rerank_score"),
                    "doc_id": item.get("doc_id"),
                    "title": brief(item.get("title") or "[NO_TITLE]", 90),
                    "citation_ids": item.get("citation_ids", []),
                    "match_text_sha256": sha256((item.get("match_text") or "").encode("utf-8")).hexdigest(),
                    "context_text_sha256": sha256((item.get("context_text") or "").encode("utf-8")).hexdigest(),
                    "match_excerpt": brief(item.get("match_text") or "", 125) if
                        item["disposition"] == "selected" else "",
                }
                for item in response["items"]
            ],
        })

    source_rows = []
    for src in evidence["sources"]:
        pointer = src["support_ref"]["bundle_json_pointer"]
        assert re.fullmatch(r"/items/[0-9]+", pointer)
        original = sec["items"][int(pointer.rsplit("/", 1)[1])]
        assert original["disposition"] == "selected"
        hash_pass = canonical_sha256(original) == src["support_ref"]["sha256"]
        assert hash_pass
        assert src["evidence_id"] == original["evidence_id"]
        assert src["citation_ids"] == original["citation_ids"]
        source_rows.append({
            "evidence_id": src["evidence_id"],
            "citation_ids": src["citation_ids"],
            "source_json_pointer": pointer,
            "exact_source_sha256": src["support_ref"]["sha256"],
            "original_source_hash_match": hash_pass,
            "context_marker_exists": all(
                "[" + cid + "]" in evidence["context_text"] for cid in src["citation_ids"]
            ),
        })
    assert len(source_rows) == 3
    assert evidence["citation_usage"]["allowed_aliases"] == req["extra"]["available_citation_ids"]

    markers = scan_citation_markers(body)
    native = CitationOccurrenceIntegrityGate.extract_marker_occurrences(
        section_id=SECTION_ID, content=body
    )
    assert len(markers) == len(native) == 2
    assert all(marker.well_formed for marker in markers)
    assert {m.alias for m in markers} <= set(req["extra"]["available_citation_ids"])
    assert {m.alias for m in markers} == {"C1", "C2"}
    missing_facts = project.get("missing_information", [])
    missed = [str(x) for x in missing_facts if not any(
        str(value) in body for value in (
            x.get("field_name", ""), x.get("fact_key", ""), x.get("name", "")
        ) if value
    )] if all(isinstance(x, dict) for x in missing_facts) else []
    # This merely checks literal mention of source fact labels, not semantic coverage.
    writer = {
        "section_id": SECTION_ID,
        "model_call_id": req["model_call_id"],
        "role": req["model_role"],
        "output_cap": req["max_tokens"],
        "finish_reason": resp["finish_reason"],
        "completion_tokens": resp.get("token_usage", {}).get("completion_tokens"),
        "prompt_tokens": resp.get("token_usage", {}).get("prompt_tokens"),
        "body_characters": len(body),
        "body_sha256": sha256(body.encode("utf-8")).hexdigest(),
        "citation_markers": [
            {"alias":m.alias,"byte_or_char_start":m.start,"token":m.token,
             "valid":m.well_formed} for m in markers
        ],
        "allowed_aliases": evidence["citation_usage"]["allowed_aliases"],
        "unused_but_allowed_aliases": sorted(
            set(req["extra"]["available_citation_ids"]) - {m.alias for m in markers}
        ),
        "fact_projection_count": len(fact["facts"]),
        "citation_required": True,
        "body_excerpt": brief(body, 260),
        "source_explicit_missing_keys_without_literal_match": missed,
    }

    claims, bindings, supports, committed, events = native_claims()
    archived = {}
    for path, digest, item in candidate_artifacts():
        if item.get("outcome") != "completed":
            continue
        cand = item.get("candidate") or {}
        ref = cand.get("claim_ref", {}).get("claim_id")
        if ref:
            assert ref not in archived, "ambiguous candidate identity"
            archived[ref] = (path, digest, item)

    claim_rows = []
    for idx, claim in enumerate(claims, 1):
        cid = claim["claim_ref"]["claim_id"]
        support = ClaimEvidenceSupportRecordSchema.model_validate(supports[cid])
        binding = ClaimBindingFactsRecordSchema.model_validate(bindings[cid])
        item = {
            "ordinal": idx,
            "claim_id": cid,
            "claim_type": claim["claim_type"],
            "text": claim["normalized_value"],
            "exact_quote_in_writer": claim["normalized_value"] in body,
            "support_relation": support.relation.value,
            "supporting_refs_count": len(support.supporting_refs),
            "contradicting_refs_count": len(support.contradicting_refs),
            "binding_relations": dict(Counter(f.relation.value for f in binding.facts)),
            "verifier_committed": cid in committed,
            "verifier_candidate_recommendation": None,
            "current_M6_admission": "NOT_EVALUATED",
            "verifier_candidate_receipt": None,
        }
        if cid in archived:
            path, digest, envelope = archived[cid]
            candidate = VerifierSemanticResultCandidateSchema.model_validate(
                envelope["candidate"]
            )
            item["verifier_candidate_recommendation"] = candidate.recommended_verdict.value
            item["verifier_candidate_receipt"] = {"path": path, "sha256": digest}
            item["verifier_issue_codes"] = [i.issue_code for i in candidate.issue_candidates]
            item["positive_basis_refs_count"] = len(candidate.positive_basis_refs)
            try:
                ClaimVerificationRuntime.validate_v2_provenance(
                    binding, support, candidate
                )
            except ClaimBindingError as error:
                item["current_M6_admission"] = "REJECTED"
                item["mechanical_refusal"] = str(error)
            else:
                item["current_M6_admission"] = "ADMITTED_BY_PROVENANCE_ONLY"
        else:
            item["verifier_issue_codes"] = []
        claim_rows.append(item)
    assert len(claim_rows) == 8
    assert sum(x["verifier_committed"] for x in claim_rows) == 5
    assert all(x["current_M6_admission"] == "ADMITTED_BY_PROVENANCE_ONLY"
               for x in claim_rows[:5])
    assert (claim_rows[5]["support_relation"],
            claim_rows[5]["verifier_candidate_recommendation"],
            claim_rows[5]["current_M6_admission"]) == (
                "INDETERMINATE","VERIFIED","REJECTED"
            )
    assert claim_rows[6]["current_M6_admission"] == "NOT_EVALUATED"
    assert claim_rows[7]["current_M6_admission"] == "NOT_EVALUATED"
    assert all(x["exact_quote_in_writer"] for x in claim_rows)
    assert len(archived) == 6

    ref_index = {
        "G1": "PROVISIONAL", "G2": "PROVISIONAL", "G3": "PROVISIONAL",
        "G4": "PROVISIONAL", "G5": "PROVISIONAL",
    }
    postwrite_counts = read_original_postwrite_counts()
    assert not any(postwrite_counts.values()), "failed original Run cannot have full Section Post-write"

    # Pure read-only Coverage owner. This constructs a *diagnostic selector*,
    # not a legitimate production ValidationRun/Artifact instance.
    check_row = SimpleNamespace(requirement_kind="CITATION_PRESENCE",
        constraints={"section_title":"项目背景","document_global":False})
    units = ({"unit_ref":"isolated-offline-first-section",
              "section_title":"项目背景","selector":{"section_id":SECTION_ID}},)
    matched = TargetPostWriteCoverageComposition._structural_matches(
        check_row, units,
        tuple({"span":{"section_id":SECTION_ID},"token":m.token} for m in markers)
    )
    assert matched == ("isolated-offline-first-section",)

    postwrite = {
        "canonical_validation_run_executed":False,
        "original_native_postwrite_tables":postwrite_counts,
        "current_raw_citation_parser_valid_count":len(markers),
        "current_native_occurrence_parser_count":len(native),
        "all_used_aliases_in_frozen_writer_allowlist":True,
        "pure_citation_presence_test": "PASS_DIAGNOSTIC_ONLY",
        "formal_citation_integrity": "NOT_RUN",
        "formal_citation_grounding": "NOT_RUN",
        "formal_faithfulness": "NOT_RUN",
        "formal_coverage_completeness": "NOT_RUN",
        "formal_consistency": "NOT_RUN",
        "formal_control_release": "NOT_RUN",
        "no_fake_formal_postwrite_success":True,
    }

    # The frozen Gold manifest has synthetic candidates, not human-reviewed
    # semantic labels. Record review hypotheses only.
    findings = [
        {
            "id":"CH1-G2-001","severity":"REVIEW","step":1,
            "finding":"First-section E3 has a table-of-contents-like title, but its match/body is historical project prose. Its actual added value to the first section and impact on packing require independent Gold review.",
            "evidence_refs":["observed_retrieval.jsonl#/events/3/response/items/2"],
            "semantic_gold":"PROVISIONAL",
        },
        {
            "id":"CH1-G2-002","severity":"REVIEW","step":1,
            "finding":"One section candidate was dropped due to token_budget_overflow; this alone cannot prove rerank relevance order or retrieval recall without a judged candidate pool.",
            "evidence_refs":["observed_retrieval.jsonl#/events/3/response/items/3"],
            "semantic_gold":"PROVISIONAL",
        },
        {
            "id":"CH1-WR-001","severity":"PASS_STRUCTURAL","step":2,
            "finding":"All three EvidencePackage source pointers and SHA-256 hashes exactly match frozen first-section RAG selected items.",
            "evidence_refs":["observed_retrieval.jsonl#/events/3/response/items",
                              "observed_calls.jsonl#real-writer-first-section-request"],
            "semantic_gold":"NOT_APPLICABLE_TO_HASH_PROOF",
        },
        {
            "id":"CH1-G3-001","severity":"REVIEW","step":3,
            "finding":"Writer generalizes a disaster-recovery requirement from another project's historical case to generic government-cloud practice. Citation and historical qualifier exist, but semantic universality needs independent judgment.",
            "evidence_refs":["observed_calls.jsonl#real-writer-first-section-response",
                              "observed_retrieval.jsonl#/events/3/response/items/0"],
            "semantic_gold":"PROVISIONAL",
        },
        {
            "id":"CH1-G3-002","severity":"UNMEASURED","step":3,
            "finding":"Eight exact extracted spans are present in the writer text, but no independently reviewed must-extract Claim universe exists; recall and precision cannot be inferred from an 8/8 source span membership check.",
            "semantic_gold":"PROVISIONAL",
        },
        {
            "id":"CH1-G4-001","severity":"CONFIRMED_CONTRACT_REJECTION","step":4,
            "finding":"Original Claim006 VERIFIED with shared INDETERMINATE Support is rejected by current unchanged M6 provenance invariant; original Run had no applicable local verifier correction dispatch.",
            "evidence_refs":["runs/claim_support.jsonl","runs/claim_verification.jsonl",
                              "runs/artifacts/archived-failed-candidate"],
            "semantic_gold":"NO_INDEPENDENT_SEMANTIC_VERDICT_YET",
        },
        {
            "id":"CH1-G4-002","severity":"REVIEW","step":4,
            "finding":"Five previously committed VERIFIED judgments include NOT_SUPPORTED shared evidence relationships. Fact/ProjectInput authority may lawfully support them under current M6; complete-claim semantic correctness must be reviewed separately.",
            "semantic_gold":"PROVISIONAL",
        },
        {
            "id":"CH1-G5-001","severity":"NOT_RUN","step":5,
            "finding":"Canonical Post-write never started on original terminal Run, so syntactic citation and pure Coverage checks do not establish the original first section's overall Post-write quality or Release eligibility.",
            "evidence_refs":["runs/post_write.sqlite3","execution.json"],
            "semantic_gold":"PROVISIONAL",
        },
    ]

    return {
        "schema_version":"fpbca_real_deepseek_chapter1_five_step_observation_v1",
        "observation_mode":"HISTORICAL_READ_ONLY",
        "run_id":RUN_ID,
        "task_id":project["task_id"],
        "section_id":SECTION_ID,
        "run_terminal": {
            "status":execution["terminal_outcome"]["status"],
            "phase":execution["terminal_outcome"]["phase"],
            "publication_exists":execution["terminal_outcome"]["publication_exists"],
        },
        "step1_retrieval":retrieval,
        "step2_evidence_writer_binding":{
            "binding_count":len(source_rows),
            "exact_source_hashes_verified":sum(x["original_source_hash_match"] for x in source_rows),
            "source_rows":source_rows,
            "locked_context_characters":len(evidence["context_text"]),
            "locked_citation_entries":len(evidence["citations"]),
            "current_section_plan_sha256":canonical_sha256(plan),
            "current_fact_subset_sha256":canonical_sha256(fact),
            "model_user_message_sha256":sha256(current.encode("utf-8")).hexdigest(),
        },
        "step3_real_writer":writer,
        "step4_claim_support_verifier":{
            "claims_total":len(claim_rows),
            "exact_quote_present":sum(x["exact_quote_in_writer"] for x in claim_rows),
            "support_relations":dict(Counter(x["support_relation"] for x in claim_rows)),
            "committed_verified":sum(x["verifier_committed"] for x in claim_rows),
            "raw_verifier_candidates":len(archived),
            "blocked_claim_ordinal":6,
            "remaining_verifier_not_run":2,
            "claim_rows":claim_rows,
        },
        "step5_postwrite_offline":postwrite,
        "findings":findings,
        "reference_review":ref_index,
        "honesty_fence":{
            "semantic_quality_requires_independent_gold":True,
            "actual_postwrite_run_recreated":False,
            "artifacts_not_upgraded_to_production_pass":True,
            "no_original_run_modification":True,
            "no_paid_or_fake_provider_dispatch":True,
        },
    }


def build_formal_snapshot(out: Path, frozen_shas):
    sources = [
        ("original_project_input", RUN / "audit/PROJECT_INPUT_V3.json", "project_input", "json"),
        ("original_retrieval_wire", BUS / "observed_retrieval.jsonl", "retrieval_observer", "jsonl"),
        ("original_model_wire", BUS / "observed_calls.jsonl", "model_observer", "jsonl"),
        ("original_claim_verification", NATIVE / "claim_verification.jsonl", "claim_native", "jsonl"),
        ("original_claim_support", NATIVE / "claim_support.jsonl", "claim_native", "jsonl"),
        ("original_paragraph_manifest", NATIVE / "paragraph_manifest.jsonl", "paragraph_manifest", "jsonl"),
        ("original_artifact_log", NATIVE / "artifact_runtime.jsonl", "artifact_runtime", "jsonl"),
        ("original_lifecycle", NATIVE / "lifecycle.jsonl", "lifecycle", "jsonl"),
        ("original_research", NATIVE / "research_runtime.jsonl", "research_runtime", "jsonl"),
        ("original_runtime_trace", NATIVE / (RUN_ID + "_trace.jsonl"), "runtime_trace", "jsonl"),
        ("original_summary", BUS / "summary.json", "public_summary", "json"),
        ("original_execution", BUS / "execution.json", "execution", "json"),
        ("original_postwrite", NATIVE / "post_write.sqlite3", "postwrite", "sqlite"),
    ]
    names = []
    for name,path,family,fmt in sources:
        item = {"name":name, "path":str(path),"family":family,
                "format":fmt,"sha256":frozen_shas[str(path)],
                "owner":"original_closed_run","schema_version":"original_native_archived_v1"}
        if fmt == "sqlite":
            item.update(frozen=True, tables=[
                "artifact_states","post_write_entries","validation_runs",
                "validation_facts","validation_completions",
                "post_write_control_decisions","delivery_gate_decisions",
            ])
        names.append(item)
    manifest={"schema_version":"full_chain_source_manifest_v1",
              "snapshot_id":"fpbca-real-chapter1-original-"+RUN_ID,
              "as_of":"2026-10-09T11:28:00Z",
              "sources":names}
    (out/"source_manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    refs=ROOT/"data/eval/full_chain/reference_manifest.json"
    snapshot=load_source_snapshot(manifest,[RUN],output_root=out,max_bytes=64*1024*1024,max_records=100000)
    reference=load_references(refs,[refs.parent])
    report=observe(snapshot,EvaluationRunIdentity(
        "FCE-FPBCA-CH1-REAL-"+RUN_ID, SECTION_ID,
        source_snapshot_id=snapshot.snapshot_id
    ),reference_view=reference)
    paths=emit_report(report,out/"formal_full_chain_report",source_roots=[RUN,ROOT])
    return {
        "snapshot_id":snapshot.snapshot_id,
        "source_read_limitations":list(snapshot.limitations),
        "formal_report_json":str(paths[0]),
        "formal_report_markdown":str(paths[1]),
        "status":{
            "read_status":report.execution_coverage["read_status"],
            "stage_instances":report.execution_coverage["stage_instances"],
            "measured_instances":report.execution_coverage["measurement_counts"].get("MEASURED"),
            "reported_domains":len({
                v.unit_id[0] for v in report.stage_quality
            }),
            "limitations":list(report.limitations)[:40],
            "finding_codes":dict(Counter(f.code for f in report.findings)),
            "diagnostic_readiness":report.diagnostic_readiness,
            "reference_review":report.reference_review,
        },
        "historical_capture_boundary":{
            "old_observed_calls_have_event_not_diagnostic_event_type":True,
            "old_raw_provider_diagnostics_triples_proven":False,
            "not_a_new_T15_run":True,
        },
    }


def markdown(data, fce, shas):
    retr = data["step1_retrieval"]
    w = data["step3_real_writer"]
    claim = data["step4_claim_support_verifier"]
    pw = data["step5_postwrite_offline"]
    lines=[
        "# 政务云真实 DeepSeek Run · 第一章五步评测",
        "",
        "> 本报告只读取已终态的真实 Run。真实观测与离线纯校验可以有 PASS；没有独立 Gold 的语义正确性一律不能签 PASS。",
        "",
        f"**Run**: \`{RUN_ID}\`　**Section**: \`{SECTION_ID}\`",
        f"**正式原 Run 状态**：{data['run_terminal']['status']} / {data['run_terminal']['phase']}；不是业务 E2E 成功。",
        "",
        "## Step 1 · RAG 检索/准入",
        "",
        "| Scope | 召回项 | 选中 | 丢弃 | 证据包口径 | Token |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for q in retr:
        lines.append(f"| {q['scope']} | {q['candidate_count']} | {q['selected_count']} | {q['dropped_count']} | {q['assessment']} | {q['tokens_used']}/{q['token_budget']} |")
    lines.extend(["","### 第一章实际条目","","| Evidence | Rank | Rerank Score | 处理 | 文档章节 |","|---|---:|---:|---|---|"])
    for x in retr[1]["items"]:
        lines.append(f"| {x['evidence_id']} | {x['rank']} | {x['rerank_score']:.3f} | {x['disposition']} / {x['drop_reason'] or '-'} | {x['title'].replace('|','/')} |")
    lines.extend([
        "",
        "**有证据的判断**：E1/E2/E3 被保留，E4 因 token_budget_overflow 被丢弃。E3 的标题类似目录，但实际来源材料不止目录；不能直接判它无效。评分和排序的语义质量 G2 待审。",
        "",
        "## Step 2 · EvidencePackage → Writer 的真实输入",
        "",
        "| ID | 引用 Alias | 原始 JSON Pointer | SHA-256 来源绑定 |",
        "|---|---|---|---|",
    ])
    for x in data["step2_evidence_writer_binding"]["source_rows"]:
        lines.append(f"| {x['evidence_id']} | {','.join(x['citation_ids'])} | \`{x['source_json_pointer']}\` | {'PASS' if x['original_source_hash_match'] else 'FAIL'} |")
    lines.extend([
        "",
        f"全部 {data['step2_evidence_writer_binding']['binding_count']} 条 source 与原 RAG selected item 的 canonical SHA-256 精确一致。只证明来源字节哈希绑定，不代表语义有用。",
        "",
        "## Step 3 · Writer 正文及引用",
        "",
        f"- DeepSeek 实际完成；输出限制 {w['output_cap']}，finish_reason={w['finish_reason']}，Prompt/Completion={w['prompt_tokens']}/{w['completion_tokens']} tokens。",
        f"- 正文 {w['body_characters']} 字符；引用：{', '.join('['+m['alias']+']' for m in w['citation_markers'])}。C3 允许但没有出现在正文中。",
        "- 引用语法：ASCII 中括号格式和受允许 Alias 集均 PASS；这**不是**引用所对应陈述被资料充分支持的语义判断。",
        "- 语义候选风险：对其他项目的灾备建设目标作了较广泛的政务云通用表述。正文保留“同类历史项目参考、不构成当前建设事实”的限定；是否充分避免不当泛化仍需 Gold G3 核验。",
        "",
        "## Step 4 · Claim → Support → Verifier",
        "",
        "| # | Claim type | Support | Verifier 原始候选 | 正式提交 | M6 机械准入 |",
        "|---:|---|---|---|---|---|",
    ])
    for x in claim["claim_rows"]:
        lines.append(f"| {x['ordinal']} | {x['claim_type']} | {x['support_relation']} | {x['verifier_candidate_recommendation'] or 'NOT_RUN'} | {'YES' if x['verifier_committed'] else 'NO'} | {x['current_M6_admission']} |")
    lines.extend([
        "",
        f"8 条 Claim 的文本均能在真实 Writer 正文中找到对应原文；这只是**已提取项能溯源**，不证明没有漏提。",
        "- **直接故障**：第 6 条是对“待确认信息将影响后续方案结论”的整段复合陈述；Support=INDETERMINATE，Verifier 却给出 VERIFIED，并在 issues 中承认 SHARED_SUPPORT_INDETERMINATE。当前生产 M6 拒绝完全符合冻结合同。",
        "- 第 1/4 条的 Support=NOT_SUPPORTED 而 Verifier=VERIFIED 已正式提交。这不自动违规（Fact/ProjectInput 可能独立支持），但 G4 必须检查整段内容是否真正得到支持。",
        "",
        "## Step 5 · Post-write 原生运行与离线小检查",
        "",
        "| 检查对象 | 状态 |",
        "|---|---|",
        f"| 原生 validation_runs | {pw['original_native_postwrite_tables']['validation_runs']} 条 |",
        f"| 原生 post_write_control_decisions | {pw['original_native_postwrite_tables']['post_write_control_decisions']} 条 |",
        f"| Citation 扫描 | {pw['current_raw_citation_parser_valid_count']} 个有效语法标记；离线 PASS |",
        "| Citation Presence（纯函数测试，构造临时选择器） | PASS_DIAGNOSTIC_ONLY |",
        "| Citation Grounding / Faithfulness / Coverage / Consistency | NOT_RUN |",
        "| 正式 Control / Assessment / Release | NOT_RUN |",
        "",
        "必须保留真实失败的完整链路：禁止用第一章原始文本直接伪装成已完成 Claim/Validation/Release 的 canonical 产物。",
        "",
        "## Full-Chain Evaluation 框架输出",
        "",
        f"- 报告：\`{fce['formal_report_json']}\`，\`{fce['formal_report_markdown']}\`",
        f"- 正式诊断就绪状态：\`{fce['status']['diagnostic_readiness']}\`。",
        "- 老 Run 的观测文件是 observer JSONL，没有完整的 FCE DiagnosticSession physical raw/request/projected 三表示和 SESSION_SEAL；因此 FCE 的部分技术边无法严格测量，报告不能假装全量采集齐全。",
        "- G1–G5 当前都没有获得独立 REVIEWED Gold，相关语义质量标为 PROVISIONAL/UNMEASURED。",
        "",
        "## 发现与下一步",
        "",
        "| Finding | 类别 | 结论 |",
        "|---|---|---|",
    ])
    for x in data["findings"]:
        lines.append(f"| {x['id']} | {x['severity']} | {x['finding'].replace('|','/')} |")
    lines.extend([
        "",
        "### 下一步（不需要新付费调用）",
        "1. 审核 G2：E1/E2/E3 与第一章的相关性、历史用途约束与候选 E4 的损失，形成独立的证据效用标签。",
        "2. 审核 G3：正文复合事实/提取全集，尤其是历史案例泛化以及对未确认项的因果推论。",
        "3. 审核 G4：逐 Claim 绑定与可允许 Verdict，特别是第 1、3、4、6、7 条；不要把 M6 机械 PASS 当语义正确。",
        "4. 用隔离的完整合法 Section Artifact/Claim/Support/Verifier 前置资料运行 G5 Post-write 正反例；原 Run 只读终态保持不变。",
        "5. 完成 G1–G5 真实独立评审后，才给出语义准确率/漏提率/引用 grounding 率。",
        "",
        "## 证据保全与实现边界",
        "",
        f"- 保护原件清单：{len(shas)} 个源文件，均做运行前/后的 SHA-256 对照。",
        "- 没有真实/模拟模型派发，没有恢复旧 Run，没有修改正式产品代码、Gate/Verifier/Recovery 业务规则。",
        "- 明细机器回执与原文见同目录 evidence JSON / 正式报告；本文不复制原始私人联系方式等非评测必需内容。",
    ])
    return "\n".join(lines)+"\n"


def main() -> int:
    assert BUS.is_dir() and NATIVE.is_dir()
    paths=[
        RUN/"audit/PROJECT_INPUT_V3.json",
        BUS/"observed_retrieval.jsonl", BUS/"observed_calls.jsonl",
        BUS/"execution.json",BUS/"summary.json",
        NATIVE/"claim_verification.jsonl",NATIVE/"claim_support.jsonl",
        NATIVE/"paragraph_manifest.jsonl",NATIVE/"artifact_runtime.jsonl",
        NATIVE/"lifecycle.jsonl", NATIVE/"research_runtime.jsonl",
        NATIVE/(RUN_ID+"_trace.jsonl"), NATIVE/"post_write.sqlite3",
    ]
    before={str(p):h(p) for p in paths}
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out=OUTPUT_PARENT/("REAL_CH1_5STEP_FCE_20261009_"+stamp)
    out.mkdir(parents=False,exist_ok=False)
    data=five_step_analysis()
    fce=build_formal_snapshot(out,before)
    after={str(p):h(p) for p in paths}
    if before!=after:
        raise RuntimeError("PROTECTED_HISTORICAL_FILE_CHANGED_DURING_EVALUATION")
    data["formal_full_chain_report"]=fce
    data["evidence_fence"]={
        "frozen_file_count":len(before),
        "initial_source_sha256":before,
        "end_source_sha256":after,
        "all_source_files_unchanged":True,
        "real_provider_calls_this_evaluation":0,
        "new_prod_task_run_created":False,
    }
    (out/"five_step_observation.json").write_text(
        json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"five_step_evaluation.md").write_text(
        markdown(data,fce,before),encoding="utf-8")
    receipt={
        "status":"OFFLINE_FIVE_STEP_DIAGNOSTIC_COMPLETE_NOT_GOLD_ACCEPTANCE",
        "original_run_status":data["run_terminal"]["status"],
        "step1_document_items":len(data["step1_retrieval"][0]["items"]),
        "step1_section_items":len(data["step1_retrieval"][1]["items"]),
        "step2_verified_exact_bindings":data["step2_evidence_writer_binding"]["exact_source_hashes_verified"],
        "step3_citations":len(data["step3_real_writer"]["citation_markers"]),
        "step4_committed_claims":data["step4_claim_support_verifier"]["committed_verified"],
        "step4_total_claims":data["step4_claim_support_verifier"]["claims_total"],
        "step4_illegal_candidate_confirmed":True,
        "step5_full_native_postwrite":"NOT_RUN",
        "formal_diagnostic_readiness":fce["status"]["diagnostic_readiness"],
        "output_root":str(out),
        "original_sha256_unchanged":True,
        "real_provider_calls":0,
        "no_reinterpretation_of_old_terminal":True,
    }
    (out/"mechanical_receipt.json").write_text(
        json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "output_root":str(out),
        "receipt":receipt,
        "full_chain":fce["status"],
        "findings":[{"id":f["id"],"severity":f["severity"]}
                    for f in data["findings"]],
    },ensure_ascii=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
