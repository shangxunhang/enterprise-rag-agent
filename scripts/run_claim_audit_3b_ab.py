"""A/B compare deterministic Claim Audit classification with 3B semantic classification.

This script is intentionally offline with respect to retrieval/writing. It reuses one persisted
scheme-generation checkpoint, keeps section content and writer-gate bindings frozen, and only
changes how substantive sentences are mapped to ClaimType before the existing allow/forbid
policy is evaluated.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any, Iterable

from agent.runtime.official_checkpoint_saver import _checkpoint_serde
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    DeterministicClaimClassifier,
    SubstantiveSentenceDetector,
    _SENTENCE_PATTERN,
    clean_claim_span_text,
)
from bootstrap.model_factory import ModelGatewayFactory
from core.config import get_settings
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from rag.catalog.document_profile import ClaimType


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
VALID_CLAIM_TYPES = set(CLAIM_TYPE_DESCRIPTIONS)
SEMANTIC_CONFIDENCE_THRESHOLD = 0.70


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, encoding="utf-8").strip()
    except Exception:
        return "unknown"


def _load_latest_checkpoint(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(str(database_path))
    try:
        row = connection.execute(
            "SELECT type, checkpoint FROM checkpoints ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError(f"no checkpoints found in {database_path}")
        type_name, payload = row
        return _checkpoint_serde().loads_typed((str(type_name), bytes(payload)))
    finally:
        connection.close()


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
        raise ValueError("3B claim classifier output does not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("3B claim classifier JSON root must be an object")
    return value


_STRUCTURAL_LABEL_PATTERN = re.compile(
    r"^(?:"
    r"\s*#{1,6}\s*[^。！？!?；;]{1,40}"
    r"|\s*(?:[-*+]\s*)?(?:[（(]?[一二三四五六七八九十百千万\d]+[）).、.]?\s*)?"
    r"(?:\*\*|__)[^。！？!?；;]{1,24}(?:\*\*|__)\s*[:：]?"
    r"|\s*[（(]?[一二三四五六七八九十百千万\d]+[）).、.]\s*[^。！？!?；;]{1,24}\s*[:：]"
    r")\s*$"
)


def _substantive_sentences(
    content: str,
    *,
    qualifiers: Iterable[str],
) -> list[dict[str, Any]]:
    detector = SubstantiveSentenceDetector()
    rows: list[dict[str, Any]] = []
    for sentence_index, match in enumerate(_SENTENCE_PATTERN.finditer(content)):
        raw = match.group(0)
        cleaned = clean_claim_span_text(raw)
        if not cleaned.strip():
            continue
        if _STRUCTURAL_LABEL_PATTERN.fullmatch(cleaned.strip()):
            continue
        if not detector.is_substantive_sentence(
            raw,
            governance_qualifiers=qualifiers,
        ):
            continue
        rows.append(
            {
                "id": f"s{sentence_index}",
                "sentence_index": sentence_index,
                "span_start": match.start(),
                "span_end": match.end(),
                "text": cleaned,
            }
        )
    return rows


def _build_prompt(
    *,
    section_title: str,
    sentences: list[dict[str, Any]],
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
        {"id": item["id"], "text": item["text"]} for item in sentences
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
        "7. 输出每条输入一次，id 必须原样返回。\n\n"
        "输入句子：\n"
        f"{json.dumps(sentence_payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "严格输出：\n"
        '{"items":[{"id":"s0","claim_types":["general_method"],"confidence":0.95}]}'
    )
    return system_prompt, prompt


def _semantic_classify_batches(
    *,
    gateway: Any,
    run_id: str,
    task_id: str,
    section_id: str,
    section_title: str,
    sentences: list[dict[str, Any]],
    batch_size: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    classifications: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    call_serial = 0

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
            model_gateway=gateway,
            model_role=ModelRole.SEMANTIC_GATE,
            runtime_context={
                "task_id": task_id,
                "workflow_run_id": run_id,
                "section_id": section_id,
                "section_title": section_title,
                "caller_agent": "ClaimAudit3BAB",
            },
            default_purpose="claim_semantic_audit_ab",
            call_suffix=f"claim_audit_ab_{call_serial}",
        )
        call_id = (
            f"model_call_{run_id}_{section_id}_claim_audit_ab_{call_serial}"
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
            max_new_tokens=max_new_tokens,
            created_at=_now_iso(),
            model_call_id=call_id,
        )
        expected_ids = {row["id"] for row in batch}
        parsed_ids: set[str] = set()
        call_record = {
            "model_call_id": response.model_call_id,
            "success": response.success,
            "model_name": response.model_name,
            "finish_reason": response.finish_reason,
            "latency_ms": response.latency_ms,
            "batch_label": batch_label,
            "retry_depth": retry_depth,
            "max_new_tokens": max_new_tokens,
            "batch_sentence_ids": [item["id"] for item in batch],
        }
        if response.success:
            try:
                payload = _extract_json_object(response.content)
                items = payload.get("items")
                if not isinstance(items, list):
                    raise ValueError("items must be a list")
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sentence_id = str(item.get("id") or "").strip()
                    if sentence_id not in expected_ids:
                        continue
                    raw_types = item.get("claim_types")
                    raw_types = raw_types if isinstance(raw_types, list) else []
                    claim_types = [
                        str(value).strip()
                        for value in raw_types
                        if str(value).strip() in VALID_CLAIM_TYPES
                    ]
                    raw_confidence = item.get("confidence")
                    if raw_confidence is None:
                        continue
                    confidence = float(raw_confidence)
                    classifications[sentence_id] = {
                        "claim_types": list(dict.fromkeys(claim_types)),
                        "confidence": max(0.0, min(1.0, confidence)),
                        "reason": str(item.get("reason") or "").strip(),
                        "model_name": response.model_name,
                        "model_call_id": response.model_call_id,
                    }
                    parsed_ids.add(sentence_id)
            except Exception as exc:
                call_record["parse_error"] = str(exc)
                call_record["response_preview"] = response.content[:1000]
        else:
            call_record["error"] = (
                response.error.model_dump(mode="json") if response.error else None
            )
        missing_ids = expected_ids - parsed_ids
        call_record["parsed_sentence_ids"] = sorted(parsed_ids)
        call_record["missing_sentence_ids"] = sorted(missing_ids)
        calls.append(call_record)

        if missing_ids and retry_depth < 3:
            retry_rows = [row for row in batch if row["id"] in missing_ids]
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

    for batch_index in range(0, len(sentences), batch_size):
        classify_batch(
            sentences[batch_index : batch_index + batch_size],
            retry_depth=0,
            batch_label=f"batch_{batch_index // batch_size + 1}",
        )
    return classifications, calls


def _rule_violations(
    *,
    claim_types: Iterable[str],
    allowed: set[str],
    forbidden: set[str],
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for claim_type in claim_types:
        if claim_type in forbidden:
            violations.append(
                {
                    "violation_code": "forbidden_claim_type_generated",
                    "claim_type": claim_type,
                }
            )
        elif claim_type not in allowed:
            violations.append(
                {
                    "violation_code": "claim_type_not_allowed",
                    "claim_type": claim_type,
                }
            )
    return violations


def _counter_dict(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _analyze_section(
    *,
    gateway: Any,
    run_id: str,
    task_id: str,
    section: dict[str, Any],
    batch_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    extra = dict(section.get("extra") or {})
    binding = dict(extra.get("writer_gate_binding") or {})
    gate = dict(binding.get("gate_decision") or {})
    audit = dict(extra.get("claim_audit") or {})
    section_title = str(section.get("section_title") or "")
    section_id = str(section.get("section_id") or "")
    content = str(section.get("content") or "")
    qualifiers = list(gate.get("required_qualifiers") or [])
    allowed = set(gate.get("allowed_claim_types") or [])
    forbidden = set(gate.get("forbidden_claim_types") or [])

    substantive = _substantive_sentences(content, qualifiers=qualifiers)
    semantic, calls = _semantic_classify_batches(
        gateway=gateway,
        run_id=run_id,
        task_id=task_id,
        section_id=section_id,
        section_title=section_title,
        sentences=substantive,
        batch_size=batch_size,
    )

    deterministic_classifier = DeterministicClaimClassifier()
    baseline_violations = list(audit.get("violations") or [])
    baseline_codes = [
        str(item.get("violation_code") or "")
        for item in baseline_violations
        if isinstance(item, dict)
    ]

    semantic_violation_rows: list[dict[str, Any]] = []
    semantic_unclassified_rows: list[dict[str, Any]] = []
    changed_examples: list[dict[str, Any]] = []
    semantic_claim_type_counts: Counter[str] = Counter()
    deterministic_claim_type_counts: Counter[str] = Counter()

    for row in substantive:
        deterministic = deterministic_classifier.classify_sentence(
            row["text"], governance_qualifiers=qualifiers
        )
        deterministic_types = [item.value for item in deterministic.claim_types]
        deterministic_claim_type_counts.update(deterministic_types)

        semantic_item = semantic.get(row["id"])
        if semantic_item is None:
            semantic_unclassified_rows.append(
                {**row, "reason": "missing_or_unparseable_3b_classification"}
            )
            semantic_types: list[str] = []
            semantic_confidence = 0.0
        else:
            semantic_types = list(semantic_item["claim_types"])
            semantic_confidence = float(semantic_item["confidence"])
            if not semantic_types or semantic_confidence < SEMANTIC_CONFIDENCE_THRESHOLD:
                semantic_unclassified_rows.append(
                    {
                        **row,
                        "reason": (
                            "empty_claim_types"
                            if not semantic_types
                            else "low_semantic_confidence"
                        ),
                        "semantic": semantic_item,
                    }
                )
        semantic_claim_type_counts.update(semantic_types)
        rule_violations = _rule_violations(
            claim_types=semantic_types,
            allowed=allowed,
            forbidden=forbidden,
        )
        for violation in rule_violations:
            semantic_violation_rows.append(
                {
                    **violation,
                    "sentence_id": row["id"],
                    "sentence_index": row["sentence_index"],
                    "text": row["text"],
                    "confidence": semantic_confidence,
                }
            )

        if set(deterministic_types) != set(semantic_types):
            changed_examples.append(
                {
                    "section_id": section_id,
                    "section_title": section_title,
                    "sentence_id": row["id"],
                    "text": row["text"],
                    "deterministic_claim_types": deterministic_types,
                    "semantic_claim_types": semantic_types,
                    "semantic_confidence": semantic_confidence,
                    "semantic_reason": (
                        semantic_item.get("reason") if semantic_item else None
                    ),
                    "semantic_rule_violations": rule_violations,
                }
            )

    semantic_codes = [
        item["violation_code"] for item in semantic_violation_rows
    ] + ["semantic_unclassified_substantive_claim"] * len(
        semantic_unclassified_rows
    )
    semantic_claim_audit_passed = not semantic_codes
    gate_human_review_required = bool(gate.get("human_review_required"))

    result = {
        "section_id": section_id,
        "section_title": section_title,
        "content_chars": len(content),
        "substantive_sentence_count": len(substantive),
        "gate": {
            "delivery_mode": gate.get("delivery_mode"),
            "allowed_claim_types": sorted(allowed),
            "forbidden_claim_types": sorted(forbidden),
            "required_qualifiers": qualifiers,
            "human_review_required": gate_human_review_required,
            "reason_codes": list(gate.get("reason_codes") or []),
        },
        "baseline": {
            "claim_audit_passed": bool(audit.get("passed")),
            "generated_claim_count": len(audit.get("generated_claims") or []),
            "violation_count": len(baseline_violations),
            "violation_codes": _counter_dict(baseline_codes),
            "unclassified_substantive_claim_count": baseline_codes.count(
                "unclassified_substantive_claim"
            ),
            "deterministic_claim_type_counts": dict(
                sorted(deterministic_claim_type_counts.items())
            ),
        },
        "semantic_3b": {
            "claim_audit_passed": semantic_claim_audit_passed,
            "violation_count": len(semantic_codes),
            "violation_codes": _counter_dict(semantic_codes),
            "unclassified_substantive_claim_count": len(
                semantic_unclassified_rows
            ),
            "claim_type_counts": dict(sorted(semantic_claim_type_counts.items())),
            "gate_clear_without_upstream_human_review": (
                semantic_claim_audit_passed and not gate_human_review_required
            ),
        },
        "model_calls": calls,
        "semantic_violations": semantic_violation_rows,
        "semantic_unclassified": semantic_unclassified_rows,
        "changed_classification_count": len(changed_examples),
    }
    return result, changed_examples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-sections", type=int, default=None)
    parser.add_argument("--section-order", type=int, default=None)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    if args.max_sections is not None and args.max_sections <= 0:
        raise ValueError("--max-sections must be positive when provided")
    if args.section_order is not None and args.section_order <= 0:
        raise ValueError("--section-order must be positive when provided")

    checkpoint = _load_latest_checkpoint(args.checkpoint_db)
    channel_values = dict(checkpoint.get("channel_values") or {})
    payload = dict(channel_values.get("scheme_generation_payload") or {})
    sections = list(payload.get("sections") or [])
    if not sections:
        raise RuntimeError("checkpoint does not contain generated sections")
    if args.section_order is not None:
        sections = [
            section
            for section in sections
            if int(section.get("section_order") or 0) == args.section_order
        ]
        if not sections:
            raise RuntimeError(
                f"checkpoint does not contain section_order={args.section_order}"
            )
    if args.max_sections is not None:
        sections = sections[: args.max_sections]

    run_id = str(payload.get("run_id") or channel_values.get("run_id") or "unknown")
    task_id = str(payload.get("task_id") or channel_values.get("task_id") or "unknown")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings(reload=True)
    gateway = None
    section_results: list[dict[str, Any]] = []
    changed_examples: list[dict[str, Any]] = []
    try:
        gateway = ModelGatewayFactory().build(settings)
        for index, section in enumerate(sections, start=1):
            title = str(section.get("section_title") or "")
            print(
                f"[ClaimAudit3BAB] section={index}/{len(sections)} START title={title}",
                flush=True,
            )
            result, changed = _analyze_section(
                gateway=gateway,
                run_id=run_id,
                task_id=task_id,
                section=section,
                batch_size=args.batch_size,
            )
            section_results.append(result)
            changed_examples.extend(changed)
            (args.output_dir / "claim_audit_3b_ab_progress.json").write_text(
                json.dumps(
                    {
                        "updated_at": _now_iso(),
                        "completed_sections": len(section_results),
                        "sections": section_results,
                        "changed_classifications": changed_examples,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"[ClaimAudit3BAB] section={index}/{len(sections)} END "
                f"baseline_violations={result['baseline']['violation_count']} "
                f"semantic_violations={result['semantic_3b']['violation_count']} "
                f"baseline_unclassified={result['baseline']['unclassified_substantive_claim_count']} "
                f"semantic_unclassified={result['semantic_3b']['unclassified_substantive_claim_count']}",
                flush=True,
            )
    except Exception as exc:
        (args.output_dir / "claim_audit_3b_ab_error.json").write_text(
            json.dumps(
                {
                    "created_at": _now_iso(),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "completed_sections": len(section_results),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
    finally:
        if gateway is not None:
            try:
                gateway.close()
            except Exception as close_exc:
                (args.output_dir / "claim_audit_3b_ab_close_error.json").write_text(
                    json.dumps(
                        {
                            "created_at": _now_iso(),
                            "error_type": type(close_exc).__name__,
                            "message": str(close_exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    baseline_codes = Counter()
    semantic_codes = Counter()
    baseline_unclassified = 0
    semantic_unclassified = 0
    baseline_passed_sections = 0
    semantic_passed_sections = 0
    semantic_gate_clear_sections = 0
    gate_human_review_sections = 0
    model_calls: list[dict[str, Any]] = []
    for result in section_results:
        baseline_codes.update(result["baseline"]["violation_codes"])
        semantic_codes.update(result["semantic_3b"]["violation_codes"])
        baseline_unclassified += result["baseline"]["unclassified_substantive_claim_count"]
        semantic_unclassified += result["semantic_3b"]["unclassified_substantive_claim_count"]
        baseline_passed_sections += int(result["baseline"]["claim_audit_passed"])
        semantic_passed_sections += int(result["semantic_3b"]["claim_audit_passed"])
        semantic_gate_clear_sections += int(
            result["semantic_3b"]["gate_clear_without_upstream_human_review"]
        )
        gate_human_review_sections += int(result["gate"]["human_review_required"])
        model_calls.extend(result["model_calls"])

    report = {
        "schema_version": "claim_audit_3b_ab_v1",
        "created_at": _now_iso(),
        "source_run_id": run_id,
        "source_task_id": task_id,
        "checkpoint_db": str(args.checkpoint_db.resolve()),
        "repository": {
            "branch": _git(["git", "branch", "--show-current"]),
            "head": _git(["git", "rev-parse", "HEAD"]),
            "dirty_count": int(
                _git(["git", "status", "--porcelain"]).count("\n") + 1
            )
            if _git(["git", "status", "--porcelain"]).strip()
            else 0,
        },
        "experiment": {
            "writer_content_frozen": True,
            "writer_gate_binding_frozen": True,
            "retrieval_rerun": False,
            "generation_rerun": False,
            "semantic_classifier_role": ModelRole.SEMANTIC_GATE.value,
            "semantic_confidence_threshold": SEMANTIC_CONFIDENCE_THRESHOLD,
            "batch_size": args.batch_size,
            "final_policy_decision": "deterministic allow/forbid comparison",
        },
        "summary": {
            "section_count": len(section_results),
            "baseline_claim_audit_passed_sections": baseline_passed_sections,
            "semantic_3b_claim_audit_passed_sections": semantic_passed_sections,
            "semantic_3b_gate_clear_sections_excluding_upstream_human_review": semantic_gate_clear_sections,
            "upstream_gate_human_review_required_sections": gate_human_review_sections,
            "baseline_violation_count": sum(baseline_codes.values()),
            "semantic_3b_violation_count": sum(semantic_codes.values()),
            "baseline_violation_codes": dict(sorted(baseline_codes.items())),
            "semantic_3b_violation_codes": dict(sorted(semantic_codes.items())),
            "baseline_unclassified_substantive_claim_count": baseline_unclassified,
            "semantic_3b_unclassified_substantive_claim_count": semantic_unclassified,
            "unclassified_reduction": baseline_unclassified - semantic_unclassified,
            "model_call_count": len(model_calls),
            "model_names": sorted(
                {
                    str(item.get("model_name"))
                    for item in model_calls
                    if item.get("model_name")
                }
            ),
            "model_latency_ms": sum(
                int(item.get("latency_ms") or 0) for item in model_calls
            ),
        },
        "sections": section_results,
        "changed_classifications": changed_examples,
    }
    report_path = args.output_dir / "claim_audit_3b_ab_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[ClaimAudit3BAB] REPORT {report_path}", flush=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
