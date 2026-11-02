"""EXPERIMENT ONLY — DO NOT MERGE INTO PRODUCTION ROUTING OR PROMPTS.

Round 2 isolates one variable: the semantic-claim prompt wording for
uncertain/deferred/negated current-project statements.

Production code is not patched.  The same 30 fixed cases and the same local
Qwen2.5-3B model from round 1 are reused.  Only additional classifier prompt
rules are appended in-memory for this experiment.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bootstrap.model_factory import ModelGatewayFactory, build_default_model_routing
from core.config import get_settings
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from apps.enterprise_document.services.scheme_writer.semantic_claim_classifier import (
    _build_prompt as production_build_prompt,
    _extract_json_object,
)
from run_claim_classifier_ab_round1 import CASES, CURRENT_PROJECT_TYPES

EXPERIMENT_ID = "claim_classifier_round2_deferred_prompt"
PROMPT_VARIANT = "deferred_negated_modality_v1"
OUTPUT = Path(r"E:\rag-agent-runtime\claim_classifier_ab_round1\qwen3b_round2_prompt.json")

# This is the ONLY experimental variable.  It is intentionally not added to
# semantic_claim_classifier.py until the model-capability question is resolved.
ROUND2_RULES = """
\n补充分类规则（实验版，仅用于区分“当前项目已确定事实”和“尚未形成事实”）：
9. 句子出现“待补充、待确认、需项目方确认、尚未提供、当前未提供、尚未确定、后续测算、进一步测算和确认、最终以……为准”等表达，且句子没有同时给出已确定的具体值/品牌/型号/配置/金额/工期时，表示该当前项尚未形成确定事实。不得仅因为句子讨论数量、配置、成本或工期主题就标为 current_project_quantity/current_project_configuration/current_project_cost/current_project_schedule；应根据其实际方法/说明语义优先归入 general_method 或其他真正适用的非 current-project 类型。
10. “本方案不指定具体产品/品牌/型号”“不确定具体配置”等否定承诺表达，不属于 current_project_configuration，除非同一句还独立断言了另一项已确定配置。
11. “需结合……后确定”“需根据……进一步测算”“具体数值待补充”表达的是决策尚未完成，不等于已经断言当前项目的数量、配置、成本或工期。
12. 反过来，只要句子已经给出具体数值、品牌、型号、确定配置或明确项目承诺，即使含“预计/计划”等措辞，仍应按 current_project_* 分类。例如“预计日均用户达到10万”仍是 current_project_quantity，“本项目选用华为X型号”仍是 current_project_configuration。
""".strip()


def build_round2_prompt(*, section_title: str, sentences: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt, prompt = production_build_prompt(
        section_title=section_title,
        sentences=sentences,
    )
    marker = "8. 输出每条输入一次，id 必须原样返回。"
    if marker in prompt:
        prompt = prompt.replace(marker, ROUND2_RULES + "\n" + marker)
    else:
        prompt = prompt + "\n\n" + ROUND2_RULES
    return system_prompt, prompt


def resolve_local_3b_model_name(settings: Any) -> str:
    routing = build_default_model_routing(settings)
    profiles = {profile.profile_id: profile for profile in routing.profiles}
    return profiles["local_3b"].model_name


def classify_group(
    gateway: Any,
    model_name: str,
    section_title: str,
    rows: list[dict[str, Any]],
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sentence_rows = [{"id": row["id"], "text": row["text"]} for row in rows]
    system_prompt, prompt = build_round2_prompt(
        section_title=section_title,
        sentences=sentence_rows,
    )
    boundary = ModelCallBoundary(
        model_gateway=gateway,
        model_role=ModelRole.SEMANTIC_GATE,
        model_name=model_name,
        runtime_context={
            "task_id": EXPERIMENT_ID,
            "run_id": run_id,
            "section_id": section_title,
            "section_title": section_title,
            "caller_agent": "ClaimClassifierRound2Experiment",
        },
        default_purpose="claim_semantic_audit",
        call_suffix="claim_classifier_round2",
    )
    response = boundary.generate_response(
        prompt,
        system_prompt=system_prompt,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        do_sample=False,
        max_new_tokens=max(192, min(640, len(rows) * 72)),
        model_call_id=f"model_call_{run_id}_{abs(hash(section_title)) % 100000}",
        model_extra={
            "experiment": EXPERIMENT_ID,
            "prompt_variant": PROMPT_VARIANT,
            "do_not_merge": True,
        },
    )
    if not response.success:
        raise RuntimeError(f"model call failed for {section_title}: {response.error_message}")
    payload = _extract_json_object(response.content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"invalid classifier output for {section_title}: items missing")

    parsed: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        raw_types = item.get("claim_types")
        if not isinstance(raw_types, list):
            raw_types = []
        parsed[item_id] = {
            "claim_types": [str(value) for value in raw_types],
            "confidence": float(item.get("confidence") or 0.0),
        }
    return parsed, {
        "section_title": section_title,
        "model_name": response.model_name,
        "finish_reason": response.finish_reason,
        "latency_ms": response.latency_ms,
        "token_usage": response.token_usage.model_dump(mode="json"),
    }


def score(predictions: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    tp = fp = tn = fn = 0
    groups: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "correct": 0, "predicted_current": 0}
    )
    for case in CASES:
        pred = predictions.get(case["id"], {"claim_types": [], "confidence": 0.0})
        predicted_types = [str(value) for value in pred.get("claim_types", [])]
        predicted_current = bool(CURRENT_PROJECT_TYPES & set(predicted_types))
        expected = bool(case["expect_current_project"])
        correct = predicted_current == expected
        if expected and predicted_current:
            tp += 1
        elif expected and not predicted_current:
            fn += 1
        elif not expected and predicted_current:
            fp += 1
        else:
            tn += 1
        bucket = groups[case["group"]]
        bucket["total"] += 1
        bucket["correct"] += int(correct)
        bucket["predicted_current"] += int(predicted_current)
        rows.append(
            {
                **case,
                "predicted_claim_types": predicted_types,
                "confidence": pred.get("confidence", 0.0),
                "predicted_current_project": predicted_current,
                "binary_correct": correct,
            }
        )
    non_current = tn + fp
    current = tp + fn
    return {
        "summary": {
            "case_count": len(CASES),
            "current_positive_count": current,
            "non_current_count": non_current,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "accuracy": round((tp + tn) / len(CASES), 4),
            "current_project_precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
            "current_project_recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
            "non_current_false_positive_rate": round(fp / non_current, 4) if non_current else 0.0,
        },
        "groups": dict(groups),
        "rows": rows,
    }


def main() -> int:
    settings = get_settings(reload=True)
    if settings.default_model_name == "fake_llm":
        settings = replace(settings, default_model_name=settings.local_qwen_1_5b_model_name)
    model_name = resolve_local_3b_model_name(settings)
    run_id = f"{EXPERIMENT_ID}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    gateway = ModelGatewayFactory().build(settings)
    predictions: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    try:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in CASES:
            grouped[case["section_title"]].append(case)
        for section_title, rows in grouped.items():
            parsed, meta = classify_group(
                gateway,
                model_name,
                section_title,
                rows,
                run_id,
            )
            predictions.update(parsed)
            calls.append(meta)
    finally:
        gateway.close()

    scoring = score(predictions)
    result = {
        "experiment": EXPERIMENT_ID,
        "experimental_only": True,
        "do_not_merge_into_production": True,
        "production_code_modified": False,
        "profile": "local_3b",
        "requested_model_name": model_name,
        "round1_case_set_reused": True,
        "production_prompt_base_reused": True,
        "only_variable": "additional deferred/negated modality instructions",
        "prompt_variant": PROMPT_VARIANT,
        "prompt_addition": ROUND2_RULES,
        "temperature": 0.0,
        "do_sample": False,
        "scoring": scoring,
        "calls": calls,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "experiment": EXPERIMENT_ID,
                "experimental_only": True,
                "do_not_merge_into_production": True,
                "output": str(OUTPUT),
                **scoring["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
