"""Controlled adapters used only by the standalone vertical runtime."""

from __future__ import annotations

LEGACY_FIXTURE_ONLY = True

from collections import defaultdict
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.persistence.safe_paths import SafeExternalIdentifier, SafePathResolver

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    ExcelDeviceFactSchema,
    FactSnapshotSchema,
    GeneratedParagraphInputSchema,
    PlannerInvocationInputSchema,
    PlannerResultPayloadSchema,
    ResearchEvidencePackageSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
    ToolResultStatus,
    VerifierAction,
    VerifierInvocationInputSchema,
    VerifierResultPayloadSchema,
    WriterInvocationInputSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.services.agent_rag_v1 import (
    AgentToolSession,
    FileArtifactStore,
    StreamingWriterService,
)

from .contracts import (
    ExportedDocumentPathsSchema,
    MultiAgentProjectInputSchema,
    RetrievalRequestSchema,
    RetrievalResponseSchema,
    RuntimeSectionTemplateSchema,
    RuntimeTemplateSchema,
)


class FixtureFactSnapshotProvider:
    """Two-device fixture with the same hard-authority semantics as production."""

    def load(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> FactSnapshotSchema:
        snapshot_version = f"snapshot:{project_input.run_id}"
        return FactSnapshotSchema(
            snapshot_version=snapshot_version,
            artifact_ref=(
                f"artifact://{project_input.task_id}/facts/{snapshot_version}"
            ),
            devices=[
                ExcelDeviceFactSchema(
                    fact_id="fact_server",
                    device_name="服务器",
                    aliases=["计算节点"],
                    device_type="compute",
                    model="S-01",
                    specification="2U",
                    quantity=8,
                    unit="台",
                    unit_price=10000,
                    total_amount=80000,
                    source_ref="excel://devices/server",
                ),
                ExcelDeviceFactSchema(
                    fact_id="fact_storage",
                    device_name="存储设备",
                    aliases=["存储节点"],
                    device_type="storage",
                    model="ST-01",
                    specification="双控",
                    quantity=2,
                    unit="台",
                    unit_price=25000,
                    total_amount=50000,
                    source_ref="excel://devices/storage",
                ),
            ],
            construction_scope=["计算资源", "存储资源"],
        )


class FixtureTemplateProvider:
    def load(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> RuntimeTemplateSchema:
        del project_input
        return RuntimeTemplateSchema(
            template_version="standalone-template-v1",
            document_title="企业建设方案",
            sections=[
                RuntimeSectionTemplateSchema(
                    section_id="compute",
                    section_title="计算资源",
                    section_order=1,
                    execution_group="technical",
                ),
                RuntimeSectionTemplateSchema(
                    section_id="storage",
                    section_title="存储资源",
                    section_order=2,
                    execution_group="technical",
                ),
            ],
        )


class ScenarioRetrievalService:
    """Scenario-aware fake behind RetrievalServicePort, never imported by agents."""

    def __init__(self, project_input: MultiAgentProjectInputSchema) -> None:
        self.project_input = project_input
        self.calls: dict[str, int] = defaultdict(int)

    def retrieve(
        self,
        request: RetrievalRequestSchema,
    ) -> RetrievalResponseSchema:
        self.calls[request.section_id] += 1
        round_number = self.calls[request.section_id]
        forced = str(
            self.project_input.metadata.get("retrieval_status", "")
        ).strip()

        if forced == "retry_then_sufficient":
            status = "insufficient" if round_number == 1 else "sufficient"
        elif forced in {
            "sufficient",
            "partial",
            "insufficient",
            "conflicted",
            "empty",
        }:
            status = forced
        elif (
            request.scenario == "research-more"
            and request.section_id == "compute"
            and round_number == 1
        ):
            status = "insufficient"
        elif request.scenario == "failed":
            status = "empty"
        elif (
            request.scenario == "partial-success"
            and request.section_id == "storage"
        ):
            status = "empty"
        else:
            status = "sufficient"

        if status == "sufficient":
            return RetrievalResponseSchema(
                status="sufficient",
                evidence_ids=[
                    f"evidence:{request.section_id}:{round_number}:technical"
                ],
                supported_topics=request.query_topics or ["technical_design"],
                source_refs=[
                    f"knowledge://standalone/{request.section_id}/technical"
                ],
            )
        if status == "partial":
            return RetrievalResponseSchema(
                status="partial",
                evidence_ids=[
                    f"evidence:{request.section_id}:{round_number}:partial"
                ],
                supported_topics=["overview"],
                evidence_gaps=["implementation_details"],
                source_refs=[
                    f"knowledge://standalone/{request.section_id}/partial"
                ],
            )
        if status == "conflicted":
            return RetrievalResponseSchema(
                status="conflicted",
                conflicts=[{"code": "SCENARIO_EVIDENCE_CONFLICT"}],
                evidence_gaps=["conflict_resolution"],
            )
        return RetrievalResponseSchema(
            status="empty" if status == "empty" else "insufficient",
            evidence_gaps=["technical_evidence"],
        )


class MarkdownDocumentExporter:
    """Writes standalone Markdown/JSON files; this is not a DOCX exporter."""

    def __init__(self, output_root: str | Path) -> None:
        self._path_resolver = SafePathResolver(output_root)
        self.output_root = self._path_resolver.root

    def export(
        self,
        *,
        task_id: str,
        run_id: str,
        document_version: str,
        document_content: bytes,
        generation_report: Mapping[str, Any],
    ) -> ExportedDocumentPathsSchema:
        safe_task_id = SafeExternalIdentifier.validate(
            task_id,
            field_name="task_id",
        )
        safe_run_id = SafeExternalIdentifier.validate(
            run_id,
            field_name="run_id",
        )
        SafeExternalIdentifier.validate(
            document_version,
            field_name="document_version",
        )
        run_dir = self._path_resolver.resolve(safe_task_id, safe_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        document_path = self._path_resolver.path_for_identifier(
            document_version,
            field_name="document_version",
            directory=(safe_task_id, safe_run_id),
            prefix="construction-scheme-",
            suffix=".md",
        )
        report_path = self._path_resolver.path_for_identifier(
            document_version,
            field_name="document_version",
            directory=(safe_task_id, safe_run_id),
            prefix="generation-report-",
            suffix=".json",
        )
        self._atomic_write(document_path, document_content)
        self._atomic_write(
            report_path,
            (
                json.dumps(
                    dict(generation_report),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )
        return ExportedDocumentPathsSchema(
            document_path=str(document_path),
            generation_report_path=str(report_path),
        )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


class ScenarioAgentReasoner:
    """One scenario-driven reasoner shared by all four stateless agent shells."""

    def __init__(
        self,
        *,
        project_input: MultiAgentProjectInputSchema,
        template: RuntimeTemplateSchema,
        fact_snapshot: FactSnapshotSchema,
        artifact_store: FileArtifactStore,
        streaming_writer: StreamingWriterService,
    ) -> None:
        self.project_input = project_input
        self.template = template
        self.fact_snapshot = fact_snapshot
        self.artifact_store = artifact_store
        self.streaming_writer = streaming_writer
        self._role_calls: dict[str, int] = defaultdict(int)
        self._section_calls: dict[tuple[str, str], int] = defaultdict(int)
        self._writer_outcomes: dict[str, Any] = {}

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: AgentToolSession,
    ) -> Mapping[str, Any] | ContractSchema:
        self._role_calls[context.agent_role.value] += 1
        section_key = context.section_id or "task"
        key = (context.agent_role.value, section_key)
        self._section_calls[key] += 1
        round_number = self._section_calls[key]
        if context.agent_role == AgentRole.PLANNER:
            return self._plan(
                context,
                PlannerInvocationInputSchema.model_validate(invocation_input),
                round_number,
            )
        if context.agent_role == AgentRole.RESEARCH:
            return self._research(
                context,
                ResearchInvocationInputSchema.model_validate(invocation_input),
                tools,
                round_number,
            )
        if context.agent_role == AgentRole.WRITER:
            return self._write(
                context,
                WriterInvocationInputSchema.model_validate(invocation_input),
                round_number,
            )
        return self._verify(
            context,
            VerifierInvocationInputSchema.model_validate(invocation_input),
            round_number,
        )

    def _plan(
        self,
        context: AgentExecutionContextSchema,
        invocation: PlannerInvocationInputSchema,
        round_number: int,
    ) -> PlannerResultPayloadSchema:
        revision = round_number
        plan_ref = f"artifact://{context.task_id}/runtime-plan/v{revision}"
        self.artifact_store.put_json(
            {
                "plan_revision": revision,
                "document_title": self.template.document_title,
                "target_section_ids": (
                    invocation.target_section_ids
                    or [section.section_id for section in self.template.sections]
                ),
                "sections": [
                    section.model_dump(mode="json")
                    for section in self.template.sections
                ],
            },
            artifact_type="runtime_plan",
            schema_version="runtime_plan_v1",
            created_by="planner_reasoner",
            task_id=context.task_id,
            attempt_id=context.attempt_id,
            input_snapshot_version=context.input_snapshot_version,
            artifact_ref=plan_ref,
        )
        section_ids = (
            invocation.target_section_ids
            or [section.section_id for section in self.template.sections]
        )
        return PlannerResultPayloadSchema(
            plan_ref=plan_ref,
            plan_revision=revision,
            section_task_ids=section_ids,
            affected_section_ids=section_ids,
        )

    def _research(
        self,
        context: AgentExecutionContextSchema,
        invocation: ResearchInvocationInputSchema,
        tools: AgentToolSession,
        round_number: int,
    ) -> ResearchResultPayloadSchema:
        result = tools.call(
            "knowledge.retrieve",
            {
                "section_id": invocation.section_id,
                "query_topics": invocation.evidence_topics,
                "research_round": round_number,
                "scenario": self.project_input.scenario,
            },
        )
        if result.status not in {
            ToolResultStatus.SUCCESS,
            ToolResultStatus.PARTIAL,
            ToolResultStatus.EMPTY,
        }:
            status = "conflicted"
            evidence_ids: list[str] = []
            supported: list[str] = []
            gaps = ["retrieval_tool_error"]
            conflicts = [{"error_code": result.error.error_code}]
        else:
            status = str(result.data.get("status", "empty"))
            evidence_ids = [
                str(value) for value in result.data.get("evidence_ids", [])
            ]
            supported = [
                str(value) for value in result.data.get("supported_topics", [])
            ]
            gaps = [
                str(value) for value in result.data.get("evidence_gaps", [])
            ]
            conflicts = [
                dict(value) for value in result.data.get("conflicts", [])
            ]
        mapping = {
            "sufficient": (
                ResearchStatus.SUFFICIENT,
                ResearchRecommendedAction.WRITE,
            ),
            "partial": (
                ResearchStatus.INSUFFICIENT,
                ResearchRecommendedAction.PARTIAL_DELIVERY,
            ),
            "insufficient": (
                ResearchStatus.INSUFFICIENT,
                ResearchRecommendedAction.RESEARCH_MORE,
            ),
            "conflicted": (
                ResearchStatus.CONFLICTED,
                ResearchRecommendedAction.HUMAN_REVIEW,
            ),
            "empty": (
                ResearchStatus.EXHAUSTED,
                ResearchRecommendedAction.PARTIAL_DELIVERY,
            ),
        }
        research_status, action = mapping.get(status, mapping["empty"])
        evidence_ref = (
            f"artifact://{context.task_id}/evidence/{invocation.section_id}/"
            f"round-{round_number}"
        )
        summary = ResearchEvidencePackageSchema(
            section_id=invocation.section_id,
            accepted_evidence_ids=evidence_ids,
            supported_topics=supported,
            evidence_gaps=gaps,
            conflicts=conflicts,
            research_status=research_status,
            recommended_action=action,
        )
        self.artifact_store.put_json(
            summary.model_dump(mode="json"),
            artifact_type="evidence_package",
            schema_version=summary.schema_version,
            created_by="research_reasoner",
            task_id=context.task_id,
            section_id=invocation.section_id,
            attempt_id=context.attempt_id,
            input_snapshot_version=context.input_snapshot_version,
            artifact_ref=evidence_ref,
        )
        return ResearchResultPayloadSchema(
            section_id=invocation.section_id,
            evidence_package_ref=evidence_ref,
            evidence_summary=summary,
        )

    def _write(
        self,
        context: AgentExecutionContextSchema,
        invocation: WriterInvocationInputSchema,
        round_number: int,
    ) -> WriterResultPayloadSchema:
        section_id = invocation.section_id
        expected = ["p001", "p002"]
        if self.project_input.scenario == "crash-recovery" and section_id == "compute":
            expected = ["p001", "p002", "p003"]
            if round_number == 1:
                paragraphs = self._paragraphs(section_id, round_number)[:2]
                outcome = self.streaming_writer.generate(
                    context=context,
                    section_id=section_id,
                    draft_version=1,
                    paragraphs=paragraphs,
                    fact_snapshot=self.fact_snapshot,
                    expected_paragraph_ids=expected,
                )
            else:
                previous = self._writer_outcomes[section_id]
                outcome = self.streaming_writer.generate(
                    context=context,
                    section_id=section_id,
                    draft_version=2,
                    paragraphs=[
                        GeneratedParagraphInputSchema(
                            paragraph_id="p003",
                            paragraph_version=1,
                            order=3,
                            role="operations",
                            content="运行阶段通过监控和审计保持方案可追踪。",
                        )
                    ],
                    fact_snapshot=self.fact_snapshot,
                    expected_paragraph_ids=expected,
                    prior_paragraphs=previous.writer_result.paragraphs,
                )
        elif (
            self.project_input.scenario == "paragraph-repair"
            and section_id == "compute"
        ):
            if round_number == 1:
                outcome = self.streaming_writer.generate(
                    context=context,
                    section_id=section_id,
                    draft_version=1,
                    paragraphs=[
                        GeneratedParagraphInputSchema(
                            paragraph_id="p001",
                            order=1,
                            role="overview",
                            content="本项目部署服务器 6 台，型号 S-01。",
                        ),
                        *self._paragraphs(section_id, round_number)[1:],
                    ],
                    fact_snapshot=self.fact_snapshot,
                    expected_paragraph_ids=expected,
                )
            else:
                outcome = self.streaming_writer.resume_after_precheck(
                    context=context,
                    section_id=section_id,
                    draft_version=2,
                    blocked_outcome=self._writer_outcomes[section_id],
                    corrected_and_remaining=[
                        GeneratedParagraphInputSchema(
                            paragraph_id="p001",
                            paragraph_version=2,
                            order=1,
                            role="overview",
                            content="本项目部署服务器 8 台，型号 S-01。",
                        ),
                        GeneratedParagraphInputSchema(
                            paragraph_id="p002",
                            order=2,
                            role="implementation",
                            content="计算平台采用分层部署并支持弹性扩展。",
                        ),
                    ],
                    fact_snapshot=self.fact_snapshot,
                    expected_paragraph_ids=expected,
                )
        else:
            paragraph_version = 2 if round_number > 1 else 1
            paragraphs = self._paragraphs(
                section_id,
                round_number,
                paragraph_version=paragraph_version,
            )
            outcome = self.streaming_writer.generate(
                context=context,
                section_id=section_id,
                draft_version=round_number,
                paragraphs=paragraphs,
                fact_snapshot=self.fact_snapshot,
                expected_paragraph_ids=expected,
            )
        self._writer_outcomes[section_id] = outcome
        return outcome.writer_result

    def _verify(
        self,
        context: AgentExecutionContextSchema,
        invocation: VerifierInvocationInputSchema,
        round_number: int,
    ) -> VerifierResultPayloadSchema:
        section_id = invocation.section_id
        if section_id is None:
            raise ValueError("standalone scenario verifier requires section_id")
        outcome = self._writer_outcomes[section_id]
        paragraph_ids = [
            paragraph.paragraph_id
            for paragraph in outcome.writer_result.paragraphs
        ]
        if (
            self.project_input.scenario == "rewrite"
            and section_id == "compute"
            and round_number == 1
        ):
            action = VerifierAction.REWRITE
        elif (
            self.project_input.scenario == "replan"
            and section_id == "compute"
            and round_number == 1
        ):
            action = VerifierAction.REPLAN
        elif (
            self.project_input.scenario == "human-review"
            and section_id == "storage"
        ):
            action = VerifierAction.HUMAN_REVIEW
        elif (
            self.project_input.scenario == "partial-success"
            and section_id == "compute"
        ):
            action = VerifierAction.PARTIAL_ACCEPT
        else:
            action = VerifierAction.ACCEPT
        verification_ref = (
            f"artifact://{context.task_id}/verification/{section_id}/"
            f"round-{round_number}"
        )
        self.artifact_store.put_json(
            {
                "section_id": section_id,
                "round": round_number,
                "action": action.value,
                "paragraph_ids": paragraph_ids,
            },
            artifact_type="section_verification",
            schema_version="section_verification_v1",
            created_by="verifier_reasoner",
            task_id=context.task_id,
            section_id=section_id,
            attempt_id=context.attempt_id,
            input_snapshot_version=context.input_snapshot_version,
            artifact_ref=verification_ref,
        )
        return VerifierResultPayloadSchema(
            execution_mode=invocation.execution_mode,
            action=action,
            accepted_paragraph_ids=(
                paragraph_ids
                if action == VerifierAction.ACCEPT
                else (
                    paragraph_ids[:1]
                    if action == VerifierAction.PARTIAL_ACCEPT
                    else []
                )
            ),
            rejected_paragraph_ids=(
                paragraph_ids if action == VerifierAction.REJECT else []
            ),
            verification_ref=verification_ref,
        )

    @staticmethod
    def _paragraphs(
        section_id: str,
        round_number: int,
        *,
        paragraph_version: int = 1,
    ) -> list[GeneratedParagraphInputSchema]:
        if section_id == "compute":
            overview = "本项目部署服务器 8 台，型号 S-01。"
            implementation = "计算平台采用分层部署并支持弹性扩展。"
        else:
            overview = "本项目部署存储设备 2 台，型号 ST-01。"
            implementation = "存储平台采用双控架构并配置数据保护策略。"
        if round_number > 1:
            implementation += " 本轮已根据验证反馈完成修订。"
        return [
            GeneratedParagraphInputSchema(
                paragraph_id="p001",
                paragraph_version=paragraph_version,
                order=1,
                role="overview",
                content=overview,
            ),
            GeneratedParagraphInputSchema(
                paragraph_id="p002",
                paragraph_version=paragraph_version,
                order=2,
                role="implementation",
                content=implementation,
            ),
        ]
