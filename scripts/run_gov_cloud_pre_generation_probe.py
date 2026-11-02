"""Run the frozen government-cloud scenario through planning and RAG only.

This probe deliberately stops before section generation. It validates the
explicit ProjectInput, deterministic DocumentPlan, per-section retrieval query,
C-RAG assessment/correction trace, and Evidence Governance gate output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for import_root in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.prompt_service import (
    SectionPromptService,
)
from context_manager.policies import SectionGenerationContextPolicy
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.runtime.clock import SystemClock
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RetrievalAccessScopeSchema
from task.task_manager import JsonlTaskManager

from run_demo import _configure_runtime


PROMPT_LEAK_PROBE_TOKENS = [
    "324.2TB",
    "8 台 XSSD",
    "8台XSSD",
    "Kunpeng 920",
    "512GB",
    "12×7.68TB",
    "HTTPS",
    "AES",
    "手机号验证码",
    "指纹识别",
    "面部识别",
]

AUTHORIZED_KB_IDS = [
    "historical_case_kb",
    "policy_standard_kb",
    "project_fact_kb",
    "technical_reference_kb",
    "template_style_kb",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-input-file",
        default="backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json",
    )
    parser.add_argument(
        "--output",
        default="E:/rag-agent-runtime/gov_cloud_pre_generation_probe_20260805.json",
    )
    parser.add_argument(
        "--rag-project-root",
        default=None,
        help=(
            "Optional runtime root containing data/processed/indexes/active_index.json. "
            "This changes only the online index root; code and policy files remain in the repository."
        ),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    fixture_path = (PROJECT_ROOT / args.project_input_file).resolve()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_root = output_path.parent / "gov_cloud_pre_generation_probe_runtime"
    runs_dir = runtime_root / "runs"
    captures_dir = runtime_root / "captures"
    tasks_dir = runtime_root / "tasks"
    for path in (runs_dir, captures_dir, tasks_dir):
        path.mkdir(parents=True, exist_ok=True)

    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    project_input = ProjectInputSchema.model_validate(raw, strict=True)
    if project_input.metadata.get("allow_demo_defaults") is not False:
        raise AssertionError("formal probe requires allow_demo_defaults=false")

    clock = SystemClock()
    created_at = clock.now_iso()
    run_id = "gov_cloud_pre_generation_probe_20260805"
    document_id = f"document_{run_id}"
    required_sections = list(
        project_input.generation_requirements.required_sections
    )
    plan = DocumentPlanningService.build_document_plan(
        run_id=run_id,
        document_id=document_id,
        project_input=project_input,
        required_sections=required_sections,
        created_at=created_at,
    )

    _configure_runtime(fake_runtime=False)
    if args.rag_project_root:
        os.environ["RAG_PROJECT_ROOT"] = str(
            Path(args.rag_project_root).expanduser().resolve()
        )
    settings = get_settings(reload=True)
    options = RuntimeOptions.resolve(settings, PROJECT_ROOT)
    if not options.use_real_rag:
        raise AssertionError("pre-generation probe must use real RAG")

    access_scope = RetrievalAccessScopeSchema(
        tenant_id=project_input.tenant_id,
        authorized_kb_ids=AUTHORIZED_KB_IDS,
    )
    task_manager = JsonlTaskManager(output_dir=tasks_dir)
    supervisor = SupervisorFactory().build(
        workflow=build_scheme_generation_workflow(created_at=created_at),
        runs_dir=runs_dir,
        captures_dir=captures_dir,
        task_manager=task_manager,
        settings=settings,
        options=options,
        allow_legacy_unscoped_rag=False,
        retrieval_access_scope=access_scope,
    )

    shared_state = SharedStateSchema(
        task_id=project_input.task_id,
        run_id=run_id,
        task_type=project_input.task_type,
        user_input=project_input.user_query,
        tenant_id=project_input.tenant_id,
        task={
            "task_id": project_input.task_id,
            "run_id": run_id,
            "task_type": project_input.task_type,
            "kb_ids": AUTHORIZED_KB_IDS,
        },
        requirements=project_input.generation_requirements.model_dump(),
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query=project_input.user_query),
            task=TaskContextSchema(
                task_id=project_input.task_id,
                run_id=run_id,
                task_type=project_input.task_type,
            ),
        ),
        created_at=created_at,
    )

    stage_service = supervisor.workflow_engine.stage_service
    query_builder = stage_service.section_execution_coordinator.query_builder
    evidence_service = stage_service.evidence_service
    section_rows: list[dict[str, object]] = []
    try:
        for section in plan.sections:
            query = query_builder.build(project_input, section.section_title)
            bundle = evidence_service.retrieve(
                shared_state,
                project_input,
                query=query,
                scope="section",
                section_id=section.section_id,
                section_title=section.section_title,
                call_suffix=f"probe_{section.section_order:03d}",
            )
            if bundle is None:
                raise RuntimeError(
                    f"real RAG returned no bundle for {section.section_title}"
                )
            gate = dict((bundle.extra or {}).get("gate_decision") or {})
            governance = dict(
                (bundle.extra or {}).get("evidence_governance_v2") or {}
            )
            trace = bundle.trace
            item_rejections = []
            for assessment in governance.get("item_assessments") or []:
                if assessment.get("eligibility") is False:
                    item_rejections.append(
                        {
                            "evidence_id": assessment.get("evidence_id"),
                            "reason_codes": list(
                                assessment.get("reason_codes") or []
                            ),
                        }
                    )
            raw_citation_text = "\n".join(
                str(item.quote_text or "") for item in bundle.citations
            )
            writer_citation_catalog = SectionPromptService.citation_catalog(
                bundle.citations
            )
            compact_citation_catalog, _, _ = (
                SectionGenerationContextPolicy._citation_catalog(
                    bundle.citations,
                    max_items=max(1, len(bundle.citations)),
                    max_quote_chars=1000,
                )
            )
            writer_visible_text = "\n".join(
                [
                    bundle.context.context_text,
                    writer_citation_catalog,
                    compact_citation_catalog,
                ]
            )
            raw_citation_token_hits = [
                token
                for token in PROMPT_LEAK_PROBE_TOKENS
                if token in raw_citation_text
            ]
            writer_visible_token_hits = [
                token
                for token in PROMPT_LEAK_PROBE_TOKENS
                if token in writer_visible_text
            ]
            section_rows.append(
                {
                    "section_id": section.section_id,
                    "section_order": section.section_order,
                    "section_title": section.section_title,
                    "retrieval_query": query,
                    "raw_retrieved_count": (
                        trace.retrieved_count if trace is not None else len(bundle.items)
                    ),
                    "reranked_count": (
                        trace.reranked_count if trace is not None else None
                    ),
                    "selected_count": len(bundle.selected_evidence_ids),
                    "dropped_count": len(bundle.dropped_evidence_ids),
                    "citation_count": len(bundle.citations),
                    "c_rag_status": bundle.assessment.status.value,
                    "c_rag_judge": bundle.assessment.judge_name,
                    "c_rag_reason_codes": list(bundle.assessment.reason_codes),
                    "rewritten_queries": list(bundle.rewritten_queries),
                    "corrective_retrieval_count": len(bundle.correction_trace),
                    "correction_trace": list(bundle.correction_trace),
                    "allowed_claim_types": list(
                        gate.get("allowed_claim_types") or []
                    ),
                    "forbidden_claim_types": list(
                        gate.get("forbidden_claim_types") or []
                    ),
                    "delivery_mode": gate.get("delivery_mode"),
                    "generation_allowed": bool(
                        (bundle.extra or {}).get("generation_allowed")
                    ),
                    "gate_reason_codes": list(gate.get("reason_codes") or []),
                    "eligible_candidate_ids": list(
                        governance.get("eligible_candidate_ids") or []
                    ),
                    "ineligible_candidate_ids": list(
                        governance.get("ineligible_candidate_ids") or []
                    ),
                    "item_rejections": item_rejections,
                    "claim_resolution_mode": (
                        gate.get("metadata") or {}
                    ).get("claim_resolution_mode"),
                    "requested_claim_types_ignored": bool(
                        (gate.get("metadata") or {}).get(
                            "requested_claim_types_ignored"
                        )
                    ),
                    "raw_citation_token_hits": raw_citation_token_hits,
                    "writer_visible_token_hits": writer_visible_token_hits,
                    "writer_catalog_uses_safe_projection": all(
                        str(item.extra.get("prompt_visible_text") or "").strip()
                        for item in bundle.citations
                    ),
                }
            )
    finally:
        supervisor.close()

    report = {
        "schema_version": "gov_cloud_pre_generation_probe_v1",
        "created_at": clock.now_iso(),
        "project_input_file": str(fixture_path),
        "allow_demo_defaults": False,
        "rag_project_root": str(options.rag_project_root),
        "project_input": project_input.model_dump(mode="json"),
        "document_plan": plan.model_dump(mode="json"),
        "section_count": len(section_rows),
        "sections": section_rows,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"probe_output={output_path}")


if __name__ == "__main__":
    main()
