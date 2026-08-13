"""3B semantic ClaimType classification for Stage E Claim Audit.

This module only performs natural-language classification. It never decides whether a
ClaimType is allowed by the current Writer Gate; deterministic policy evaluation remains
in ``writer_governance``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from apps.enterprise_document.quality.model_adapter import reserve_current_workflow_budget
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from rag.catalog.document_profile import ClaimType


SEMANTIC_CLAIM_CLASSIFIER_ID = "semantic_stage_e_claim_classifier"
SEMANTIC_CLAIM_CLASSIFIER_VERSION = "1.0.0"
SEMANTIC_CLAIM_PROMPT_VERSION = "stage_e_semantic_claim_contract_v1"
SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD = 0.70
SEMANTIC_CLAIM_DEFAULT_BATCH_SIZE = 10
SEMANTIC_CLAIM_MAX_RETRY_DEPTH = 3

CLAIM_TYPE_DESCRIPTIONS = {
    ClaimType.GENERAL_METHOD.value: "通用的方法、原则、流程、策略、建议或实施方式，不把它表述为当前项目已确定事实。",
    ClaimType.TYPICAL_ARCHITECTURE.value: "通用或典型架构模式、分层方式、高可用/容灾等参考架构，不代表当前项目已经采用。",
    ClaimType.REFERENCE_CASE.value: "明确作为历史项目、同类项目、参考案例存在的事实或做法。",
    ClaimType.CURRENT_PROJECT_FACT.value: "关于当前项目已经存在、确定、将执行或将实现的事实性断言，且不属于配置/数量/成本/工期。",
    ClaimType.CURRENT_PROJECT_COST.value: "当前项目的预算、投资、概算、造价、费用、金额或成本结论。",
    ClaimType.CURRENT_PROJECT_QUANTITY.value: "当前项目的服务器/节点/用户/容量/带宽/并发等数量或规模结论。",
    ClaimType.CURRENT_PROJECT_SCHEDULE.value: "当前项目的工期、上线、交付、实施周期或明确时间安排。",
    ClaimType.CURRENT_PROJECT_CONFIGURATION.value: "当前项目确定采用/配置/部署的具体产品、品牌、型号、组件、技术选型或资源配置。",
    ClaimType.POLICY_REQUIREMENT.value: "法律、法规、政策、管理办法等产生的必须/应当/不得等要求。",
    ClaimType.TECHNICAL_SPECIFICATION.value: "技术规范、国家/行业标准、接口规范、性能指标、安全等级、兼容性等技术要求。",
    ClaimType.PROCUREMENT_REQUIREMENT.value: "采购、招标、投标、供应商、合同、验收、限价等采购履约要求。",
    ClaimType.TEMPLATE_STRUCTURE.value: "模板、目录、章节结构、章节名称、占位符或固定文档结构要求。",
    ClaimType.STYLE_GUIDANCE.value: "写作风格、措辞、格式、行文方式等表达要求。",
}
VALID_CLAIM_TYPES = frozenset(CLAIM_TYPE_DESCRIPTIONS)

_SEMANTIC_POLICY_PAYLOAD = {
    "classifier_id": SEMANTIC_CLAIM_CLASSIFIER_ID,
    "classifier_version": SEMANTIC_CLAIM_CLASSIFIER_VERSION,
    "prompt_version": SEMANTIC_CLAIM_PROMPT_VERSION,
    "model_role": ModelRole.SEMANTIC_GATE.value,
    "claim_type_descriptions": CLAIM_TYPE_DESCRIPTIONS,
    "classification_independent_of_gate": True,
    "multi_label": True,
    "confidence_threshold": SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD,
    "batch_size": SEMANTIC_CLAIM_DEFAULT_BATCH_SIZE,
    "max_retry_depth": SEMANTIC_CLAIM_MAX_RETRY_DEPTH,
    "malformed_output_behavior": "retry_then_fail_closed",
}
SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _SEMANTIC_POLICY_PAYLOAD,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class SemanticClaimClassification:
    claim_types: tuple[ClaimType, ...]
    confidence: float
    model_name: str
    model_call_id: str


@dataclass(frozen=True)
class SemanticClaimBatchResult:
    classifications: dict[str, SemanticClaimClassification]
    calls: tuple[dict[str, Any], ...]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("semantic ClaimType output does not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("semantic ClaimType JSON root must be an object")
    return value


def _build_prompt(
    *,
    section_title: str,
    sentences: Iterable[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = (
        "你是企业文档 Claim Semantic Classifier。你的唯一任务是把每个中文句子映射到给定的 ClaimType。"
        "不要判断最终 PASS/FAIL，不要根据门禁允许项改变分类结果。分类必须独立于 allowed/forbidden policy。"
        "允许多标签。若句子确实无法映射到任何现有类型，claim_types 返回空数组。"
        "只输出 JSON，不要解释，不要输出 reason 字段。"
    )
    definitions = "\n".join(
        f"- {claim_type}: {description}"
        for claim_type, description in CLAIM_TYPE_DESCRIPTIONS.items()
    )
    sentence_payload = [
        {"id": str(item["id"]), "text": str(item["text"])}
        for item in sentences
    ]
    prompt = (
        f"章节：{section_title}\n\n"
        "ClaimType 定义：\n"
        f"{definitions}\n\n"
        "分类原则：\n"
        "1. 判断句子实际语义，不因出现‘一套/一个’等普通量词就自动判为 current_project_quantity。\n"
        "2. 只要句子使用‘本项目/本次建设/我们将/将/拟/计划’等当前项目承诺语气，并明确说采用、选用、配置、部署、安装、配备、接入某设备/品牌/型号/组件，就必须判 current_project_configuration；例如‘我们将选用华为路由器’绝不能判 general_method。\n"
        "3. 只有‘建议/可采用/可考虑/通常/参考/应根据实际情况’等非承诺表达，且没有把做法写成当前项目已定方案时，才优先 general_method 或 typical_architecture。\n"
        "4. 历史/同类项目必须显式作为参考时才判 reference_case；把历史案例改写成本项目事实则判 current_project_fact 或更具体的当前项目类型。\n"
        "5. ‘我们将配备多台防火墙’属于 current_project_configuration，若包含明确资源数量也可同时标 current_project_quantity；‘将在三个月内完成’必须标 current_project_schedule。\n"
        "6. 政策/标准/采购类要求可与其他类型多标签共存。\n"
        "7. 句子只是说明方案将包含‘投资概算’等章节内容时，不得仅因出现该词就判 current_project_cost；只有陈述当前项目实际金额、预算、投资或概算结论才属于 current_project_cost。\n"
        "8. 输出每条输入一次，id 必须原样返回。\n\n"
        "输入句子：\n"
        f"{json.dumps(sentence_payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "严格输出：\n"
        '{"items":[{"id":"s0","claim_types":["general_method"],"confidence":0.95}]}'
    )
    return system_prompt, prompt


class SemanticClaimClassifier:
    """Batch 3B semantic classifier with strict parsing and fail-closed retries."""

    classifier_id = SEMANTIC_CLAIM_CLASSIFIER_ID
    classifier_version = SEMANTIC_CLAIM_CLASSIFIER_VERSION
    policy_sha256 = SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256
    prompt_version = SEMANTIC_CLAIM_PROMPT_VERSION
    confidence_threshold = SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD

    def __init__(
        self,
        *,
        model_gateway: Any,
        batch_size: int = SEMANTIC_CLAIM_DEFAULT_BATCH_SIZE,
        max_retry_depth: int = SEMANTIC_CLAIM_MAX_RETRY_DEPTH,
    ) -> None:
        self.model_gateway = model_gateway
        self.batch_size = max(1, int(batch_size))
        self.max_retry_depth = max(0, int(max_retry_depth))

    def classify_sentences(
        self,
        sentences: list[dict[str, Any]],
        *,
        section_title: str,
        runtime_context: dict[str, Any] | None = None,
    ) -> SemanticClaimBatchResult:
        classifications: dict[str, SemanticClaimClassification] = {}
        calls: list[dict[str, Any]] = []
        context = dict(runtime_context or {})
        call_serial = 0

        if not sentences:
            return SemanticClaimBatchResult(classifications={}, calls=())
        if self.model_gateway is None:
            calls.append(
                {
                    "success": False,
                    "error": "model_gateway_unavailable",
                    "batch_sentence_ids": [str(item["id"]) for item in sentences],
                }
            )
            return SemanticClaimBatchResult(classifications={}, calls=tuple(calls))

        def response_hook(request: Any, response: Any) -> None:
            shared_state = context.get("shared_state")
            if shared_state is not None and hasattr(shared_state, "generated_outputs"):
                shared_state.generated_outputs[request.model_call_id] = response.model_dump()

        def classify_batch(
            batch: list[dict[str, Any]],
            *,
            retry_depth: int,
            batch_label: str,
        ) -> None:
            nonlocal call_serial
            if not batch:
                return
            call_serial += 1
            system_prompt, prompt = _build_prompt(
                section_title=section_title,
                sentences=batch,
            )
            boundary = ModelCallBoundary(
                model_gateway=self.model_gateway,
                model_role=ModelRole.SEMANTIC_GATE,
                runtime_context={
                    **context,
                    "caller_agent": "WriterClaimAuditor",
                    "section_title": section_title,
                },
                default_purpose="claim_semantic_audit",
                call_suffix=f"claim_semantic_{call_serial}",
                budget_hook=reserve_current_workflow_budget,
                response_hook=response_hook,
            )
            run_id = str(
                context.get("workflow_run_id")
                or context.get("run_id")
                or "model_run"
            )
            section_id = str(context.get("section_id") or "section")
            call_id = (
                f"model_call_{run_id}_{section_id}_claim_semantic_{call_serial}"
            )
            max_new_tokens = max(192, min(640, len(batch) * 72))
            response = boundary.generate_response(
                prompt,
                system_prompt=system_prompt,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                model_call_id=call_id,
                model_extra={
                    "semantic_claim_classifier_id": self.classifier_id,
                    "semantic_claim_classifier_version": self.classifier_version,
                    "semantic_claim_prompt_version": self.prompt_version,
                    "semantic_claim_policy_sha256": self.policy_sha256,
                    "semantic_claim_batch_label": batch_label,
                    "semantic_claim_retry_depth": retry_depth,
                },
            )
            expected_ids = {str(row["id"]) for row in batch}
            parsed_ids: set[str] = set()
            call_record: dict[str, Any] = {
                "model_call_id": response.model_call_id,
                "success": bool(response.success),
                "model_name": response.model_name,
                "finish_reason": response.finish_reason,
                "latency_ms": response.latency_ms,
                "batch_label": batch_label,
                "retry_depth": retry_depth,
                "batch_sentence_ids": [str(item["id"]) for item in batch],
            }
            if response.success:
                try:
                    payload = _extract_json_object(response.content)
                    items = payload.get("items")
                    if not isinstance(items, list):
                        raise ValueError("items must be a list")
                    seen_ids: set[str] = set()
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        sentence_id = str(item.get("id") or "").strip()
                        if sentence_id not in expected_ids or sentence_id in seen_ids:
                            continue
                        seen_ids.add(sentence_id)
                        raw_types = item.get("claim_types")
                        if not isinstance(raw_types, list):
                            continue
                        normalized_types = [str(value).strip() for value in raw_types]
                        if any(value not in VALID_CLAIM_TYPES for value in normalized_types):
                            continue
                        raw_confidence = item.get("confidence")
                        if raw_confidence is None:
                            continue
                        confidence = float(raw_confidence)
                        if not 0.0 <= confidence <= 1.0:
                            continue
                        classifications[sentence_id] = SemanticClaimClassification(
                            claim_types=tuple(
                                ClaimType(value)
                                for value in dict.fromkeys(normalized_types)
                            ),
                            confidence=confidence,
                            model_name=str(response.model_name or ""),
                            model_call_id=str(response.model_call_id),
                        )
                        parsed_ids.add(sentence_id)
                except Exception as exc:
                    call_record["parse_error"] = str(exc)
                    call_record["response_preview"] = str(response.content or "")[:1000]
            else:
                call_record["error"] = (
                    response.error.model_dump(mode="json")
                    if response.error is not None
                    else response.error_message
                )

            missing_ids = expected_ids - parsed_ids
            call_record["parsed_sentence_ids"] = sorted(parsed_ids)
            call_record["missing_sentence_ids"] = sorted(missing_ids)
            calls.append(call_record)

            if missing_ids and retry_depth < self.max_retry_depth:
                retry_rows = [row for row in batch if str(row["id"]) in missing_ids]
                if len(retry_rows) == 1:
                    classify_batch(
                        retry_rows,
                        retry_depth=retry_depth + 1,
                        batch_label=f"{batch_label}.retry",
                    )
                else:
                    midpoint = max(1, len(retry_rows) // 2)
                    classify_batch(
                        retry_rows[:midpoint],
                        retry_depth=retry_depth + 1,
                        batch_label=f"{batch_label}.a",
                    )
                    classify_batch(
                        retry_rows[midpoint:],
                        retry_depth=retry_depth + 1,
                        batch_label=f"{batch_label}.b",
                    )

        for batch_index in range(0, len(sentences), self.batch_size):
            classify_batch(
                sentences[batch_index : batch_index + self.batch_size],
                retry_depth=0,
                batch_label=f"batch_{batch_index // self.batch_size + 1}",
            )
        return SemanticClaimBatchResult(
            classifications=classifications,
            calls=tuple(calls),
        )


__all__ = [
    "CLAIM_TYPE_DESCRIPTIONS",
    "SEMANTIC_CLAIM_CLASSIFIER_ID",
    "SEMANTIC_CLAIM_CLASSIFIER_POLICY_SHA256",
    "SEMANTIC_CLAIM_CLASSIFIER_VERSION",
    "SEMANTIC_CLAIM_CONFIDENCE_THRESHOLD",
    "SEMANTIC_CLAIM_PROMPT_VERSION",
    "SemanticClaimBatchResult",
    "SemanticClaimClassification",
    "SemanticClaimClassifier",
]
