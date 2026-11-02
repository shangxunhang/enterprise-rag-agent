"""Stage E writer enforcement, fail-closed classification, and artifact integrity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from observability.trace_summary import canonical_sha256
from contracts.evidence import evidence_bundle_sha256

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.schemas.writer_governance import (
    ClaimAuditIntegritySchema,
    ClaimAuditResultSchema,
    ClaimAuditViolationSchema,
    GeneratedClaimSchema,
    WriterExecutionStatus,
    WriterGateBindingSchema,
)
from schemas.claim import ClaimType
from schemas.rag import EvidenceBundleSchema
from .evidence_gate_policy import EvidenceGatePolicy
from .semantic_claim_classifier import (
    SEMANTIC_CLAIM_CLASSIFIER_ID,
    SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256,
    SEMANTIC_CLAIM_CLASSIFIER_VERSION,
    SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD,
    SEMANTIC_CLAIM_PROMPT_VERSION,
    SemanticClaimClassifier,
)


CLAIM_CLASSIFIER_ID = "deterministic_stage_e_claim_classifier"
CLAIM_CLASSIFIER_VERSION = "2.1.0"
SUBSTANTIVE_DETECTOR_VERSION = "1.0.0"
CLAIM_CLASSIFIER_CONFIDENCE_THRESHOLD = 1.0

_SENTENCE_PATTERN = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?", re.MULTILINE)
_CITATION_PATTERN = re.compile(r"\[[A-Za-z0-9_.:\-]+\]")
_CHINESE_NUMBER = r"零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億"
_ARABIC_OR_CHINESE_NUMBER = rf"(?:\d+(?:\.\d+)?|[{_CHINESE_NUMBER}]+)"

_MONEY_PATTERN = re.compile(
    rf"(?:预算|投资|概算|造价|费用|成本|单价|总价|限价|报价|资金|经费|"
    rf"总投入|资金需求|项目金额|人民币|¥|￥|"
    rf"{_ARABIC_OR_CHINESE_NUMBER}\s*(?:元|万元|万|亿元))"
)
_SCHEDULE_PATTERN = re.compile(
    rf"(?:工期|周期|建设期|进度|计划于|预计于|完成时间|交付时间|上线时间|"
    rf"第一季度|第二季度|第三季度|第四季度|上半年|下半年|本月底|年底前|"
    rf"\d{{4}}年\d{{1,2}}月|{_ARABIC_OR_CHINESE_NUMBER}\s*(?:日|天|个月|月|年)内?|"
    rf"(?:计划|预计|将在|拟于).{{0,24}}(?:完成|交付|上线|投产|启用))"
)
_QUANTITY_PATTERN = re.compile(
    rf"(?:数量|规模|台数|套数|节点数|用户数|并发数|容量|吞吐量|"
    rf"(?:{_ARABIC_OR_CHINESE_NUMBER}|若干)\s*(?:台|套|个|项|路|核|卡|TB|GB|PB|人))",
    re.IGNORECASE,
)
_CONFIGURATION_PATTERN = re.compile(
    r"(?:本项目|本次建设|本系统|项目建设|项目将|拟建设|计划建设)"
    r"(?:[^。；\n]{0,48})"
    r"(?:采用|配置|部署|安装|配备|选用|接入|升级)"
)
_POLICY_PATTERN = re.compile(
    r"(?:依据|根据|遵循|执行|参照|落实).{0,48}"
    r"(?:法律|法规|条例|办法|规定|政策|《[^》]*(?:法|条例|办法|规定|政策)》)|"
    r"《[^》]*(?:法|条例|办法|规定|政策)》|"
    r"(?:应当|必须|不得|严禁|应满足|应符合|须满足|需符合)"
)
_PROCUREMENT_PATTERN = re.compile(
    r"(?:采购|招标|投标|供应商|合同包|最高限价|磋商|中标|履约|验收|采购方式)"
)
_TECHNICAL_PATTERN = re.compile(
    r"(?:GB(?:/T)?\s*\d+(?:[.\-]\d+)*|YD/T\s*\d+(?:[.\-]\d+)*|"
    r"ISO\s*\d+(?:[.\-]\d+)*|IEC\s*\d+(?:[.\-]\d+)*|"
    r"行业标准|国家标准|集团规范|技术规范|技术标准|接口标准|接口规范|"
    r"技术指标|性能指标|参数要求|安全等级|兼容性|可用性|可靠性)"
    , re.IGNORECASE,
)
_TEMPLATE_PATTERN = re.compile(
    r"(?:模板|目录|章节结构|章节设置|文档结构|格式要求|占位符|章节名称)"
)
_STYLE_PATTERN = re.compile(
    r"(?:表述|措辞|风格|书面语|格式化|写作要求|行文|本章节围绕|章节展开)"
)
_REFERENCE_PATTERN = re.compile(r"(?:历史项目|同类项目|参考案例|案例表明|既有项目)")
_ARCHITECTURE_PATTERN = re.compile(
    r"(?:总体架构|技术架构|系统架构|分层架构|拓扑|高可用|容灾架构)"
)
_METHOD_PATTERN = re.compile(
    r"(?:方法|流程|机制|原则|策略|路径|步骤|治理方式|实施方式|事实边界|"
    r"追溯关系|责任分工|输入依赖|闭环治理)"
)
_CURRENT_ASSERTION_PATTERN = re.compile(
    r"(?:本项目|本次建设|本系统|项目建设|项目将|拟建设|拟采用|计划建设|"
    r"建设内容)(?:[^。；\n]{0,48})(?:是|为|采用|配置|部署|建设|实现|包括|"
    r"形成|提供|支持|具备|覆盖|开展)"
)
_SUBSTANTIVE_SIGNAL_PATTERN = re.compile(
    r"(?:是|为|采用|配置|部署|建设|实现|包括|应当|必须|不得|严禁|计划|预计|"
    r"建议|可采用|需要|依据|根据|遵循|执行|符合|参照|落实|支持|达到|满足|"
    r"形成|提供|具备|覆盖|开展|投入|上线|完成|交付|安装|配备|选用|接入|升级)"
)
_PURE_HEADING_PATTERN = re.compile(r"^#{1,6}\s*\S.+$")
_STRUCTURAL_LABEL_PATTERN = re.compile(
    r"^(?:"
    r"\s*#{1,6}\s*[^。！？!?；;]{1,40}"
    r"|\s*(?:[-*+]\s*)?(?:[（(]?[一二三四五六七八九十百千万\d]+[）).、.]?\s*)?"
    r"(?:\*\*|__)[^。！？!?；;]{1,24}(?:\*\*|__)\s*[:：]?"
    r"|\s*[（(]?[一二三四五六七八九十百千万\d]+[）).、.]\s*[^。！？!?；;]{1,24}\s*[:：]"
    r")\s*$"
)
_PURE_LIST_MARKER_PATTERN = re.compile(
    rf"^[（(]?(?:\d+|[{_CHINESE_NUMBER}]+)[）).、]?\s*$"
)
_PURE_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:待补充|待确认|TODO|TBD|N/?A|无|暂无|—|-|\.\.\.)$",
    re.IGNORECASE,
)
_DIAGNOSTIC_REFUSAL_PATTERN = re.compile(
    r"(?:当前不生成正式章节正文|已由最终证据门禁转入人工复核|"
    r"缺少可用于正式写作的合格证据|normal section generation is prohibited)"
)
_TITLE_SUFFIX_PATTERN = re.compile(
    r"^(?:[\u4e00-\u9fffA-Za-z0-9 /_-]{1,24})"
    r"(?:方案|设计|概述|目标|范围|内容|说明|要求|原则|架构|流程|章节)$"
)

_CLASSIFIER_POLICY_PAYLOAD = {
    "classifier_id": CLAIM_CLASSIFIER_ID,
    "classifier_version": CLAIM_CLASSIFIER_VERSION,
    "substantive_detector_version": SUBSTANTIVE_DETECTOR_VERSION,
    "sentence_pattern": _SENTENCE_PATTERN.pattern,
    "citation_pattern": _CITATION_PATTERN.pattern,
    "claim_patterns": {
        "current_project_cost": _MONEY_PATTERN.pattern,
        "current_project_schedule": _SCHEDULE_PATTERN.pattern,
        "current_project_quantity": _QUANTITY_PATTERN.pattern,
        "current_project_configuration": _CONFIGURATION_PATTERN.pattern,
        "policy_requirement": _POLICY_PATTERN.pattern,
        "procurement_requirement": _PROCUREMENT_PATTERN.pattern,
        "technical_specification": _TECHNICAL_PATTERN.pattern,
        "template_structure": _TEMPLATE_PATTERN.pattern,
        "style_guidance": _STYLE_PATTERN.pattern,
        "reference_case": _REFERENCE_PATTERN.pattern,
        "typical_architecture": _ARCHITECTURE_PATTERN.pattern,
        "general_method": _METHOD_PATTERN.pattern,
        "current_project_fact": _CURRENT_ASSERTION_PATTERN.pattern,
    },
    "multi_label": True,
    "classification_depends_on_gate": False,
    "unclassified_substantive_behavior": "fail_closed_human_review",
    "confidence_threshold": CLAIM_CLASSIFIER_CONFIDENCE_THRESHOLD,
}
CLAIM_CLASSIFIER_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _CLASSIFIER_POLICY_PAYLOAD,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
SUPPORTED_CLASSIFIER_POLICIES = {
    (
        CLAIM_CLASSIFIER_ID,
        CLAIM_CLASSIFIER_VERSION,
    ): CLAIM_CLASSIFIER_POLICY_SHA256,
    (
        SEMANTIC_CLAIM_CLASSIFIER_ID,
        SEMANTIC_CLAIM_CLASSIFIER_VERSION,
    ): SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256,
}

SEMANTIC_CLASSIFICATION_FAILURE_CODES = frozenset(
    {
        "semantic_unclassified_substantive_claim",
        "unclassified_substantive_claim",
        "unclassified_or_low_confidence_claim",
    }
)


@dataclass(frozen=True)
class ClaimClassificationResult:
    claim_types: tuple[ClaimType, ...]
    substantive: bool
    confidence: float


def content_sha256(content: str) -> str:
    """Hash the exact persisted UTF-8 section content without normalization."""

    return hashlib.sha256(str(content).encode("utf-8")).hexdigest()


def normalize_content_for_diagnostics(content: str) -> str:
    """Apply only stable, semantics-preserving normalization for diagnostics."""

    text = unicodedata.normalize(
        "NFC",
        str(content).replace("\r\n", "\n").replace("\r", "\n"),
    )
    return "\n".join(line.rstrip() for line in text.split("\n"))


def normalized_content_sha256(content: str) -> str:
    return hashlib.sha256(
        normalize_content_for_diagnostics(content).encode("utf-8")
    ).hexdigest()


def writer_gate_binding_sha256(binding: WriterGateBindingSchema) -> str:
    return canonical_sha256(binding.model_dump(mode="json", exclude_none=True))


def clean_claim_span_text(raw_span: str) -> str:
    """The only permitted claim/span difference is canonical citation removal."""

    return _CITATION_PATTERN.sub("", str(raw_span)).strip()


def classifier_policy_is_supported(
    *, classifier_id: str, classifier_version: str, policy_sha256: str
) -> bool:
    return (
        SUPPORTED_CLASSIFIER_POLICIES.get((classifier_id, classifier_version))
        == policy_sha256
    )


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _enum_values(values: Iterable[ClaimType]) -> list[str]:
    return [item.value for item in values]


class SubstantiveSentenceDetector:
    """Conservative detector: unknown complete assertions are never ignored."""

    version = SUBSTANTIVE_DETECTOR_VERSION

    def is_substantive_sentence(
        self,
        sentence: str,
        *,
        governance_qualifiers: Iterable[str] = (),
    ) -> bool:
        text = clean_claim_span_text(sentence).strip()
        normalized = text.rstrip("。！？!?；;").strip()
        if not normalized:
            return False
        if text.startswith("证据限定："):
            return False
        if normalized in set(governance_qualifiers):
            return False
        if _PURE_HEADING_PATTERN.fullmatch(normalized):
            return False
        if _STRUCTURAL_LABEL_PATTERN.fullmatch(normalized):
            return False
        if _PURE_LIST_MARKER_PATTERN.fullmatch(normalized):
            return False
        if _PURE_PLACEHOLDER_PATTERN.fullmatch(normalized):
            return False
        if _DIAGNOSTIC_REFUSAL_PATTERN.search(normalized):
            return False
        if (
            _TITLE_SUFFIX_PATTERN.fullmatch(normalized)
            and not _SUBSTANTIVE_SIGNAL_PATTERN.search(normalized)
        ):
            return False
        if any(
            pattern.search(normalized)
            for pattern in (
                _MONEY_PATTERN,
                _SCHEDULE_PATTERN,
                _QUANTITY_PATTERN,
                _CONFIGURATION_PATTERN,
                _POLICY_PATTERN,
                _PROCUREMENT_PATTERN,
                _TECHNICAL_PATTERN,
                _TEMPLATE_PATTERN,
                _STYLE_PATTERN,
                _REFERENCE_PATTERN,
                _ARCHITECTURE_PATTERN,
                _METHOD_PATTERN,
                _CURRENT_ASSERTION_PATTERN,
                _SUBSTANTIVE_SIGNAL_PATTERN,
            )
        ):
            return True
        return bool(len(normalized) >= 8 and re.search(r"[\u4e00-\u9fff]", normalized))


def extract_substantive_sentence_spans(
    content: str,
    *,
    governance_qualifiers: Iterable[str] = (),
    detector: SubstantiveSentenceDetector | None = None,
) -> list[dict[str, Any]]:
    """Extract stable sentence/span rows without assigning any ClaimType."""

    sentence_detector = detector or SubstantiveSentenceDetector()
    rows: list[dict[str, Any]] = []
    for sentence_index, match in enumerate(_SENTENCE_PATTERN.finditer(content)):
        raw = match.group(0)
        text = clean_claim_span_text(raw)
        if not text.strip():
            continue
        if not sentence_detector.is_substantive_sentence(
            raw,
            governance_qualifiers=governance_qualifiers,
        ):
            continue
        rows.append(
            {
                "id": f"s{sentence_index}",
                "sentence_index": sentence_index,
                "span_start": match.start(),
                "span_end": match.end(),
                "text": text,
            }
        )
    return rows


class DeterministicClaimClassifier:
    """Multi-label classifier whose result is independent of Gate permissions."""

    classifier_id = CLAIM_CLASSIFIER_ID
    classifier_version = CLAIM_CLASSIFIER_VERSION
    policy_sha256 = CLAIM_CLASSIFIER_POLICY_SHA256

    def __init__(self, detector: SubstantiveSentenceDetector | None = None) -> None:
        self.detector = detector or SubstantiveSentenceDetector()

    def classify_sentence(
        self,
        sentence: str,
        *,
        governance_qualifiers: Iterable[str] = (),
    ) -> ClaimClassificationResult:
        substantive = self.detector.is_substantive_sentence(
            sentence,
            governance_qualifiers=governance_qualifiers,
        )
        if not substantive:
            return ClaimClassificationResult(
                claim_types=(),
                substantive=False,
                confidence=1.0,
            )
        types: list[ClaimType] = []

        def add(value: ClaimType) -> None:
            if value not in types:
                types.append(value)

        if _MONEY_PATTERN.search(sentence):
            add(ClaimType.CURRENT_PROJECT_COST)
        if _SCHEDULE_PATTERN.search(sentence):
            add(ClaimType.CURRENT_PROJECT_SCHEDULE)
        if _QUANTITY_PATTERN.search(sentence):
            add(ClaimType.CURRENT_PROJECT_QUANTITY)
        if _CONFIGURATION_PATTERN.search(sentence):
            add(ClaimType.CURRENT_PROJECT_CONFIGURATION)
        if _POLICY_PATTERN.search(sentence):
            add(ClaimType.POLICY_REQUIREMENT)
        if _PROCUREMENT_PATTERN.search(sentence):
            add(ClaimType.PROCUREMENT_REQUIREMENT)
        if _TECHNICAL_PATTERN.search(sentence):
            add(ClaimType.TECHNICAL_SPECIFICATION)
        if _TEMPLATE_PATTERN.search(sentence):
            add(ClaimType.TEMPLATE_STRUCTURE)
        if _STYLE_PATTERN.search(sentence):
            add(ClaimType.STYLE_GUIDANCE)
        if _REFERENCE_PATTERN.search(sentence):
            add(ClaimType.REFERENCE_CASE)
        if _ARCHITECTURE_PATTERN.search(sentence):
            add(ClaimType.TYPICAL_ARCHITECTURE)
        if _METHOD_PATTERN.search(sentence):
            add(ClaimType.GENERAL_METHOD)
        if _CURRENT_ASSERTION_PATTERN.search(sentence) and not any(
            item
            in {
                ClaimType.CURRENT_PROJECT_COST,
                ClaimType.CURRENT_PROJECT_QUANTITY,
                ClaimType.CURRENT_PROJECT_SCHEDULE,
                ClaimType.CURRENT_PROJECT_CONFIGURATION,
            }
            for item in types
        ):
            add(ClaimType.CURRENT_PROJECT_FACT)

        return ClaimClassificationResult(
            claim_types=tuple(types),
            substantive=substantive,
            confidence=1.0 if types else 0.0,
        )


def extract_writer_gate_binding(
    normalized_output: dict[str, Any],
    *,
    evidence_bundle: EvidenceBundleSchema | None = None,
    section_id: str,
    section_title: str,
    section_order: int | None = None,
    document_id: str | None = None,
    run_id: str | None = None,
    evidence_contract_sha256: str | None = None,
) -> WriterGateBindingSchema | None:
    """Bind an Application-owned Gate decision to one writer invocation."""

    normalized_extra = normalized_output.get("extra")
    normalized_extra = normalized_extra if isinstance(normalized_extra, dict) else {}

    # Phase 4C: Application Gate consumes the explicit canonical Bundle only.
    # A normalized projection is never allowed to reconstruct Evidence authority.
    if evidence_bundle is None:
        return None
    contract_extra = dict(evidence_bundle.extra or {})
    gate_decision = EvidenceGatePolicy().evaluate(evidence_bundle.assessment)
    generation_allowed = gate_decision.delivery_mode in {
        DeliveryMode.GROUNDED,
        DeliveryMode.QUALIFIED,
        DeliveryMode.FRAMEWORK_ONLY,
    }

    prompt_contract = contract_extra.get("prompt_evidence_contract")
    prompt_contract = prompt_contract if isinstance(prompt_contract, dict) else {}
    prompt_source = contract_extra.get("prompt_evidence_source") or normalized_extra.get(
        "prompt_evidence_source"
    )
    prompt_contract_sha = (
        canonical_sha256(prompt_contract) if prompt_contract else None
    )
    contract_sha = evidence_contract_sha256
    if not contract_sha:
        contract_sha = evidence_bundle_sha256(evidence_bundle)

    return WriterGateBindingSchema(
        section_id=section_id,
        section_title=section_title,
        section_order=section_order,
        document_id=document_id,
        run_id=run_id,
        gate_decision=gate_decision,
        generation_allowed=generation_allowed,
        evidence_contract_sha256=contract_sha,
        prompt_evidence_source=(str(prompt_source).strip() if prompt_source else None),
        prompt_evidence_contract_sha256=prompt_contract_sha,
        metadata={
            "source": "application_evidence_gate_policy",
            "evidence_status": gate_decision.evidence_status.value,
        },
    )


def writer_gate_prompt_contract(binding: WriterGateBindingSchema) -> str:
    gate = binding.gate_decision
    allowed_types = set(gate.allowed_claim_types)
    allowed = "、".join(_enum_values(gate.allowed_claim_types)) or "无"
    forbidden = "、".join(_enum_values(gate.forbidden_claim_types)) or "无"
    qualifiers = "；".join(gate.required_qualifiers) or "无"
    executable_boundaries: list[str] = []
    if ClaimType.CURRENT_PROJECT_CONFIGURATION not in allowed_types:
        executable_boundaries.append(
            "未允许 current_project_configuration：不得把具体组件、产品、品牌、型号或选型写成本项目确定配置。"
        )
    if ClaimType.CURRENT_PROJECT_QUANTITY not in allowed_types:
        executable_boundaries.append(
            "未允许 current_project_quantity：不得确定服务器数量、节点数、容量和并发规模。"
        )
    if ClaimType.CURRENT_PROJECT_COST not in allowed_types:
        executable_boundaries.append(
            "未允许 current_project_cost：不得给出项目金额、预算、概算和造价结论。"
        )
    if ClaimType.CURRENT_PROJECT_SCHEDULE not in allowed_types:
        executable_boundaries.append(
            "未允许 current_project_schedule：不得确定工期、上线时间和实施周期。"
        )
    executable_boundaries.append(
        "历史案例和通用材料不得改写为本项目既定事实。"
    )
    if any(
        claim_type not in allowed_types
        for claim_type in (
            ClaimType.CURRENT_PROJECT_CONFIGURATION,
            ClaimType.CURRENT_PROJECT_QUANTITY,
            ClaimType.CURRENT_PROJECT_COST,
            ClaimType.CURRENT_PROJECT_SCHEDULE,
        )
    ):
        executable_boundaries.append(
            "输入不足时只能写建设原则、测算维度、选择方法、典型架构、参考方案和待确认项。"
        )
    return (
        "\n\n【最终证据门禁约束｜不得覆盖】\n"
        f"delivery_mode: {gate.delivery_mode.value}\n"
        f"generation_allowed: {str(binding.generation_allowed).lower()}\n"
        f"allowed_claim_types: {allowed}\n"
        f"forbidden_claim_types: {forbidden}\n"
        f"required_qualifiers: {qualifiers}\n"
        f"human_review_required: {str(gate.human_review_required).lower()}\n"
        "只能生成 allowed_claim_types 内的声明；不得生成 forbidden_claim_types 中的声明。"
        "qualified 模式必须逐字保留 required_qualifiers。\n"
        + "\n".join(executable_boundaries)
    )


def inject_required_qualifiers(
    content: str,
    binding: WriterGateBindingSchema,
) -> tuple[str, list[str]]:
    text = _clean_text(content)
    if binding.gate_decision.delivery_mode is not DeliveryMode.QUALIFIED:
        return text, []
    missing = [
        item for item in binding.gate_decision.required_qualifiers if item not in text
    ]
    if missing:
        prefix = "证据限定：" + "；".join(missing) + "。"
        text = f"{prefix}\n\n{text}".strip()
    applied = [
        item for item in binding.gate_decision.required_qualifiers if item in text
    ]
    return text, applied


def _integrity_for(
    content: str,
    *,
    binding: WriterGateBindingSchema,
    classifier_id: str = CLAIM_CLASSIFIER_ID,
    classifier_version: str = CLAIM_CLASSIFIER_VERSION,
    classifier_policy_sha256: str = CLAIM_CLASSIFIER_POLICY_SHA256,
) -> ClaimAuditIntegritySchema:
    return ClaimAuditIntegritySchema(
        section_id=binding.section_id,
        section_title=binding.section_title,
        content_sha256=content_sha256(content),
        normalized_content_sha256=normalized_content_sha256(content),
        writer_gate_binding_sha256=writer_gate_binding_sha256(binding),
        evidence_contract_sha256=binding.evidence_contract_sha256,
        prompt_evidence_contract_sha256=binding.prompt_evidence_contract_sha256,
        classifier_id=classifier_id,
        classifier_version=classifier_version,
        classifier_policy_sha256=classifier_policy_sha256,
        substantive_detector_version=SUBSTANTIVE_DETECTOR_VERSION,
    )


class WriterClaimAuditor:
    """Classify independently, then enforce Gate boundaries and fail closed."""

    def __init__(
        self,
        classifier: Any | None = None,
    ) -> None:
        # Deterministic classification remains an explicit compatibility/test seam.
        # The production SchemeWriter composition injects SemanticClaimClassifier.
        self.classifier = classifier or DeterministicClaimClassifier()

    @property
    def classifier_id(self) -> str:
        return str(getattr(self.classifier, "classifier_id", CLAIM_CLASSIFIER_ID))

    @property
    def classifier_version(self) -> str:
        return str(
            getattr(self.classifier, "classifier_version", CLAIM_CLASSIFIER_VERSION)
        )

    @property
    def classifier_policy_sha256(self) -> str:
        return str(
            getattr(
                self.classifier,
                "policy_sha256",
                CLAIM_CLASSIFIER_POLICY_SHA256,
            )
        )

    @property
    def classifier_confidence_threshold(self) -> float:
        return float(
            getattr(
                self.classifier,
                "confidence_threshold",
                CLAIM_CLASSIFIER_CONFIDENCE_THRESHOLD,
            )
        )

    def classify_sentence(self, sentence: str) -> ClaimClassificationResult:
        classify = getattr(self.classifier, "classify_sentence", None)
        if not callable(classify):
            raise RuntimeError("configured Claim classifier is batch semantic only")
        return classify(sentence)

    def _extract_claims_and_violations(
        self,
        content: str,
        *,
        binding: WriterGateBindingSchema,
        runtime_context: dict[str, Any] | None = None,
    ) -> tuple[
        list[GeneratedClaimSchema],
        list[ClaimAuditViolationSchema],
        dict[str, Any],
    ]:
        claims: list[GeneratedClaimSchema] = []
        violations: list[ClaimAuditViolationSchema] = []
        qualifiers = set(binding.gate_decision.required_qualifiers)
        rows = extract_substantive_sentence_spans(
            content,
            governance_qualifiers=qualifiers,
        )
        diagnostics: dict[str, Any] = {
            "classification_mode": self.classifier_id,
            "substantive_sentence_count": len(rows),
            "semantic_model_calls": [],
        }

        semantic_classify = getattr(self.classifier, "classify_sentences", None)
        semantic_results: dict[str, Any] = {}
        if callable(semantic_classify):
            batch_result = semantic_classify(
                rows,
                section_title=binding.section_title,
                runtime_context={
                    **dict(runtime_context or {}),
                    "workflow_run_id": (
                        dict(runtime_context or {}).get("workflow_run_id")
                        or binding.run_id
                    ),
                    "run_id": dict(runtime_context or {}).get("run_id") or binding.run_id,
                    "section_id": binding.section_id,
                    "section_title": binding.section_title,
                },
            )
            semantic_results = dict(batch_result.classifications)
            diagnostics["semantic_model_calls"] = list(batch_result.calls)

        for row in rows:
            sentence_index = int(row["sentence_index"])
            sentence = str(row["text"])
            span_start = int(row["span_start"])
            span_end = int(row["span_end"])
            model_metadata: dict[str, Any] = {}

            if callable(semantic_classify):
                classification = semantic_results.get(str(row["id"]))
                if classification is None:
                    violations.append(
                        ClaimAuditViolationSchema(
                            violation_code="semantic_unclassified_substantive_claim",
                            message="substantive sentence has no valid semantic ClaimType result after retry",
                            text=sentence,
                            metadata={
                                "sentence_index": sentence_index,
                                "span_start": span_start,
                                "span_end": span_end,
                                "classifier_confidence": 0.0,
                                "required_confidence": self.classifier_confidence_threshold,
                                "semantic_failure_reason": "missing_or_unparseable_semantic_classification",
                            },
                        )
                    )
                    continue
                claim_types = tuple(classification.claim_types)
                confidence = float(classification.confidence)
                model_metadata = {
                    "semantic_model_name": classification.model_name,
                    "semantic_model_call_id": classification.model_call_id,
                    "semantic_prompt_version": getattr(
                        self.classifier,
                        "prompt_version",
                        SEMANTIC_CLAIM_PROMPT_VERSION,
                    ),
                }
                if not claim_types:
                    violations.append(
                        ClaimAuditViolationSchema(
                            violation_code="semantic_unclassified_substantive_claim",
                            message="semantic classifier returned no ClaimType for a substantive sentence",
                            text=sentence,
                            metadata={
                                "sentence_index": sentence_index,
                                "span_start": span_start,
                                "span_end": span_end,
                                "classifier_confidence": confidence,
                                "required_confidence": self.classifier_confidence_threshold,
                                "semantic_failure_reason": "empty_claim_types",
                                **model_metadata,
                            },
                        )
                    )
                    continue
            else:
                classification = self.classifier.classify_sentence(
                    sentence,
                    governance_qualifiers=qualifiers,
                )
                claim_types = tuple(classification.claim_types)
                confidence = float(classification.confidence)
                if not classification.substantive or not claim_types:
                    violations.append(
                        ClaimAuditViolationSchema(
                            violation_code="unclassified_substantive_claim",
                            message="substantive sentence could not be deterministically classified",
                            text=sentence,
                            metadata={
                                "sentence_index": sentence_index,
                                "span_start": span_start,
                                "span_end": span_end,
                                "classifier_confidence": confidence,
                            },
                        )
                    )
                    continue

            if confidence < self.classifier_confidence_threshold:
                violations.append(
                    ClaimAuditViolationSchema(
                        violation_code="unclassified_or_low_confidence_claim",
                        message="substantive claim classification confidence is below policy threshold",
                        text=sentence,
                        metadata={
                            "sentence_index": sentence_index,
                            "span_start": span_start,
                            "span_end": span_end,
                            "classifier_confidence": confidence,
                            "required_confidence": self.classifier_confidence_threshold,
                            **model_metadata,
                        },
                    )
                )
                continue

            for claim_type in claim_types:
                digest = hashlib.sha256(
                    f"{binding.section_id}:{sentence_index}:{claim_type.value}:{sentence}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                claims.append(
                    GeneratedClaimSchema(
                        claim_id=f"claim_{digest}",
                        section_id=binding.section_id,
                        sentence_index=sentence_index,
                        claim_type=claim_type,
                        text=sentence,
                        span_start=span_start,
                        span_end=span_end,
                        classifier_id=self.classifier_id,
                        classifier_version=self.classifier_version,
                        confidence=confidence,
                        metadata={
                            "source": "final_section_content",
                            **model_metadata,
                        },
                    )
                )
        return claims, violations, diagnostics

    def audit(
        self,
        content: str,
        *,
        binding: WriterGateBindingSchema,
        applied_qualifiers: Iterable[str] = (),
        audit_created_at: datetime | None = None,
        runtime_context: dict[str, Any] | None = None,
        force_evaluate: bool = False,
    ) -> ClaimAuditResultSchema:
        del applied_qualifiers  # final content, not the caller, is the authority
        gate = binding.gate_decision
        allowed = set(gate.allowed_claim_types)
        forbidden = set(gate.forbidden_claim_types)
        evaluate_generated_content = bool(binding.generation_allowed or force_evaluate)
        if evaluate_generated_content:
            claims, violations, classification_metadata = (
                self._extract_claims_and_violations(
                    content,
                    binding=binding,
                    runtime_context=runtime_context,
                )
            )
        else:
            claims, violations, classification_metadata = [], [], {
                "classification_mode": self.classifier_id,
                "substantive_sentence_count": 0,
                "semantic_model_calls": [],
            }
        for claim in claims:
            if claim.claim_type in forbidden:
                violations.append(
                    ClaimAuditViolationSchema(
                        violation_code="forbidden_claim_type_generated",
                        message=(
                            f"generated claim type {claim.claim_type.value} is forbidden"
                        ),
                        claim_id=claim.claim_id,
                        claim_type=claim.claim_type,
                        text=claim.text,
                    )
                )
            elif claim.claim_type not in allowed:
                violations.append(
                    ClaimAuditViolationSchema(
                        violation_code="claim_type_not_allowed",
                        message=(
                            f"generated claim type {claim.claim_type.value} is not allowed"
                        ),
                        claim_id=claim.claim_id,
                        claim_type=claim.claim_type,
                        text=claim.text,
                    )
                )

        actual_applied_qualifiers = [
            item for item in gate.required_qualifiers if item in content
        ]
        qualifier_checks = {
            item: item in content for item in gate.required_qualifiers
        }
        for qualifier, present in qualifier_checks.items():
            if not present:
                violations.append(
                    ClaimAuditViolationSchema(
                        violation_code="required_qualifier_missing",
                        message=f"required qualifier is missing: {qualifier}",
                        text=qualifier,
                    )
                )

        checks = {
            "generation_allowed": binding.generation_allowed,
            "claim_types_within_allowed_set": not any(
                item.violation_code
                in {
                    "forbidden_claim_type_generated",
                    "claim_type_not_allowed",
                }
                for item in violations
            ),
            "all_substantive_sentences_classified": not any(
                item.violation_code in SEMANTIC_CLASSIFICATION_FAILURE_CODES
                for item in violations
            ),
            "required_qualifiers_visible": all(qualifier_checks.values()),
            "classifier_policy_supported": classifier_policy_is_supported(
                classifier_id=self.classifier_id,
                classifier_version=self.classifier_version,
                policy_sha256=self.classifier_policy_sha256,
            ),
        }
        if not gate.required_qualifiers:
            checks["required_qualifiers_visible"] = True
        human_review_required = bool(violations) or gate.human_review_required
        passed = not violations and all(checks.values())
        return ClaimAuditResultSchema(
            section_id=binding.section_id,
            execution_status=binding.execution_status,
            passed=passed,
            human_review_required=human_review_required,
            integrity=_integrity_for(
                content,
                binding=binding,
                classifier_id=self.classifier_id,
                classifier_version=self.classifier_version,
                classifier_policy_sha256=self.classifier_policy_sha256,
            ),
            audit_created_at=audit_created_at or datetime.now(timezone.utc),
            generated_claims=claims,
            violations=violations,
            allowed_claim_types=list(gate.allowed_claim_types),
            forbidden_claim_types=list(gate.forbidden_claim_types),
            required_qualifiers=list(gate.required_qualifiers),
            applied_qualifiers=actual_applied_qualifiers,
            checks=checks,
            metadata={
                "delivery_mode": gate.delivery_mode.value,
                "gate_human_review_required": gate.human_review_required,
                "classification_independent_of_gate": True,
                "evaluated_despite_generation_block": bool(
                    force_evaluate and not binding.generation_allowed
                ),
                "classifier_id": self.classifier_id,
                "classifier_version": self.classifier_version,
                "classifier_policy_sha256": self.classifier_policy_sha256,
                **classification_metadata,
            },
        )


def blocked_claim_audit(
    content: str,
    binding: WriterGateBindingSchema,
    *,
    audit_created_at: datetime | None = None,
    legacy_incomplete: bool = False,
) -> ClaimAuditResultSchema:
    """Bind the diagnostic artifact while proving normal generation was blocked."""

    mode = binding.gate_decision.delivery_mode
    execution_status = (
        WriterExecutionStatus.NOT_ASSESSED
        if legacy_incomplete
        else binding.execution_status
    )
    checks = {
        "generation_allowed": False,
        "claim_types_within_allowed_set": True,
        "all_substantive_sentences_classified": True,
        "required_qualifiers_visible": not binding.gate_decision.required_qualifiers,
        "classifier_policy_supported": True,
    }
    return ClaimAuditResultSchema(
        section_id=binding.section_id,
        execution_status=execution_status,
        passed=False,
        human_review_required=(
            legacy_incomplete or mode is DeliveryMode.HUMAN_REVIEW
        ),
        integrity=_integrity_for(content, binding=binding),
        audit_created_at=audit_created_at or datetime.now(timezone.utc),
        generated_claims=[],
        violations=[
            ClaimAuditViolationSchema(
                violation_code=(
                    "legacy_writer_artifact_incomplete"
                    if legacy_incomplete
                    else (
                        "writer_human_review_required"
                        if mode is DeliveryMode.HUMAN_REVIEW
                        else "writer_generation_blocked"
                    )
                ),
                message=(
                    "writer identity or evidence lineage is incomplete; human review is required"
                    if legacy_incomplete
                    else (
                        "normal section generation is prohibited until human review"
                        if mode is DeliveryMode.HUMAN_REVIEW
                        else "normal section generation is prohibited by the final evidence gate"
                    )
                ),
            )
        ],
        allowed_claim_types=list(binding.gate_decision.allowed_claim_types),
        forbidden_claim_types=list(binding.gate_decision.forbidden_claim_types),
        required_qualifiers=list(binding.gate_decision.required_qualifiers),
        applied_qualifiers=[
            item
            for item in binding.gate_decision.required_qualifiers
            if item in content
        ],
        checks=checks,
        metadata={
            "delivery_mode": mode.value,
            "gate_human_review_required": binding.gate_decision.human_review_required,
            "legacy_writer_artifact_incomplete": legacy_incomplete,
            "classification_independent_of_gate": True,
        },
    )


__all__ = [
    "CLAIM_CLASSIFIER_ID",
    "CLAIM_CLASSIFIER_POLICY_SHA256",
    "CLAIM_CLASSIFIER_VERSION",
    "SEMANTIC_CLASSIFICATION_FAILURE_CODES",
    "SEMANTIC_CLAIM_CLASSIFIER_ID",
    "SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256",
    "SEMANTIC_CLAIM_CLASSIFIER_VERSION",
    "SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD",
    "SUBSTANTIVE_DETECTOR_VERSION",
    "ClaimClassificationResult",
    "DeterministicClaimClassifier",
    "SubstantiveSentenceDetector",
    "WriterClaimAuditor",
    "blocked_claim_audit",
    "classifier_policy_is_supported",
    "clean_claim_span_text",
    "content_sha256",
    "extract_substantive_sentence_spans",
    "extract_writer_gate_binding",
    "inject_required_qualifiers",
    "normalize_content_for_diagnostics",
    "normalized_content_sha256",
    "writer_gate_binding_sha256",
    "writer_gate_prompt_contract",
]
