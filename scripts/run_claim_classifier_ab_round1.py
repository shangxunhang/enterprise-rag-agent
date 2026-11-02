from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bootstrap.model_factory import ModelGatewayFactory, build_default_model_routing
from core.config import get_settings
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from apps.enterprise_document.services.scheme_writer.semantic_claim_classifier import (
    _build_prompt,
    _extract_json_object,
)

CURRENT_PROJECT_TYPES = {
    "current_project_fact",
    "current_project_cost",
    "current_project_quantity",
    "current_project_schedule",
    "current_project_configuration",
}

# Round 1 deliberately tests one narrow question only:
# can the classifier distinguish an asserted current-project fact from
# deferred/negated/general wording under the production semantic-classifier prompt?
CASES: list[dict[str, Any]] = [
    # Real DeepSeek-writer sentences from gov_cloud_deepseek_writer_ab_20260810_095217.
    # Gold: these do NOT assert a settled current-project fact/config/quantity/cost/schedule.
    {"id": "n01", "group": "deferred", "section_title": "建设目标", "expect_current_project": False, "text": "服务可用性、数据可靠性等量化指标需项目方确认。"},
    {"id": "n02", "group": "deferred", "section_title": "总体架构", "expect_current_project": False, "text": "各层级的资源规模、设备配置及具体组件选型，需依据本项目需求分析结果进一步测算和确认。"},
    {"id": "n03", "group": "deferred", "section_title": "计算资源建设方案", "expect_current_project": False, "text": "计算资源的具体规模需结合本项目实际业务系统数量、用户规模及性能要求进行测算，当前项目输入中未提供上述基础数据，具体资源数量待补充、需项目方确认。"},
    {"id": "n04", "group": "deferred", "section_title": "计算资源建设方案", "expect_current_project": False, "text": "当前项目输入中关于业务系统数量、用户规模、性能指标等关键数据尚未提供，相关资源规模、设备选型和投资估算均需在后续需求调研阶段补充确认。"},
    {"id": "n05", "group": "deferred", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "本项目具体分区数量及边界需根据业务系统部署层级和用户分布情况，结合项目方确认后确定。"},
    {"id": "n06", "group": "negated", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "具体设备品牌、型号及数量需项目方根据采购规范和预算情况确认，本方案不指定具体产品。"},
    {"id": "n07", "group": "deferred", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "本项目网络资源建设的具体规模、设备数量及带宽数值需结合项目现状和业务需求进一步确认，以下内容以建设原则和测算方法为主。"},
    {"id": "n08", "group": "deferred", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "上述维度的具体数值需项目方提供业务现状和预期规模后确认，当前标注为待补充。"},
    {"id": "n09", "group": "deferred", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "本项目网络拓扑的最终形态需结合部署方式确定，当前待补充。"},
    {"id": "n10", "group": "deferred", "section_title": "计算资源建设方案", "expect_current_project": False, "text": "对于高性能计算需求，可考虑配置GPU加速节点，具体配置需项目方确认。"},
    # General/reference/proposal controls: also not settled current-project assertions.
    {"id": "n11", "group": "general", "section_title": "计算资源建设方案", "expect_current_project": False, "text": "计算资源池建设建议遵循集约高效、弹性扩展、安全可靠和统一管理的原则。"},
    {"id": "n12", "group": "general", "section_title": "计算资源建设方案", "expect_current_project": False, "text": "计算资源池建议采用x86架构服务器集群，通过虚拟化软件实现计算资源的池化。"},
    {"id": "n13", "group": "general", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "网络链路设计应综合考虑业务并发量、数据备份流量、管理维护流量及安全审计流量等因素。"},
    {"id": "n14", "group": "general", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "设备选型建议从设备转发性能、端口密度、高可用能力和兼容性等维度综合评估。"},
    {"id": "n15", "group": "reference", "section_title": "网络资源建设方案", "expect_current_project": False, "text": "参照政务云典型网络拓扑，网络区域一般包括互联网接入区、业务生产区、数据存储区和安全运维管理区等。"},
    {"id": "n16", "group": "general", "section_title": "存储资源建设方案", "expect_current_project": False, "text": "存储资源池应支持横向扩展，通过增加存储节点提升容量和性能，满足政务业务系统多样化存储需求。"},
    {"id": "n17", "group": "general", "section_title": "安全设计", "expect_current_project": False, "text": "安全设计建议遵循最小权限、分区分域和纵深防御原则。"},
    {"id": "n18", "group": "reference", "section_title": "总体架构", "expect_current_project": False, "text": "参考同类政务云项目，典型架构通常采用基础设施层、平台层和应用支撑层的分层设计。"},
    {"id": "n19", "group": "general", "section_title": "实施与验收建议", "expect_current_project": False, "text": "验收工作可从功能、性能、安全和文档资料四个方面组织开展。"},
    {"id": "n20", "group": "general", "section_title": "存储资源建设方案", "expect_current_project": False, "text": "存储架构可根据数据类型、访问频率和可靠性要求选择集中式或分布式方案。"},
    # Real baseline overclaims from gov_cloud_timeout_1800_e2e_20260810_0050.
    # Gold: these DO assert concrete current-project quantities/configurations.
    {"id": "p01", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "根据前期调研和已有系统的运行状况，预计政务云平台的日均在线用户将达到10万，峰值流量可达每秒1000次请求。"},
    {"id": "p02", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "总内存需求为2TB，以应对大规模数据处理和复杂算法运算。"},
    {"id": "p03", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "网络带宽提供不低于1Gbps的网络连接，确保跨地域访问的稳定性和快速响应。"},
    {"id": "p04", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "品牌与型号选择国内外知名厂商的产品，如华为、戴尔、联想。"},
    {"id": "p05", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "CPU选用Intel Xeon系列，内存容量达到2TB，硬盘采用NVMe SSD，网络接口采用千兆以太网。"},
    {"id": "p06", "group": "asserted", "section_title": "计算资源建设方案", "expect_current_project": True, "text": "部署方式采用虚拟化技术，如KVM和Xen，实现资源的灵活调度和隔离。"},
    {"id": "p07", "group": "asserted", "section_title": "网络资源建设方案", "expect_current_project": True, "text": "核心交换机选用华为S5700-E-SI，支持10GE电口和25GE光口。"},
    {"id": "p08", "group": "asserted", "section_title": "网络资源建设方案", "expect_current_project": True, "text": "路由器选用Cisco Catalyst 9500 Series，支持IPv4/IPv6双栈。"},
    {"id": "p09", "group": "asserted", "section_title": "网络资源建设方案", "expect_current_project": True, "text": "防火墙选用HPE Synergy 5000，支持IPS/IDS功能。"},
    {"id": "p10", "group": "asserted", "section_title": "网络资源建设方案", "expect_current_project": True, "text": "入侵检测系统选用F5 BIG-IP A-Series，最大转发速率可达100Gbps。"},
]


def _resolve_model_name(profile_id: str, settings: Any) -> str:
    routing = build_default_model_routing(settings)
    profiles = {profile.profile_id: profile for profile in routing.profiles}
    if profile_id not in profiles:
        raise SystemExit(f"unknown profile_id: {profile_id}")
    return profiles[profile_id].model_name


def _classify_group(gateway: Any, model_name: str, section_title: str, rows: list[dict[str, Any]], run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    sentence_rows = [{"id": row["id"], "text": row["text"]} for row in rows]
    system_prompt, prompt = _build_prompt(section_title=section_title, sentences=sentence_rows)
    boundary = ModelCallBoundary(
        model_gateway=gateway,
        model_role=ModelRole.SEMANTIC_GATE,
        model_name=model_name,
        runtime_context={
            "task_id": "claim_classifier_ab_round1",
            "run_id": run_id,
            "section_id": section_title,
            "section_title": section_title,
            "caller_agent": "ClaimClassifierABRound1",
        },
        default_purpose="claim_semantic_audit",
        call_suffix="claim_classifier_ab_round1",
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
        model_extra={"experiment": "claim_classifier_ab_round1"},
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
    call_meta = {
        "section_title": section_title,
        "model_name": response.model_name,
        "provider": response.metadata.get("provider"),
        "finish_reason": response.finish_reason,
        "latency_ms": response.latency_ms,
        "token_usage": response.token_usage.model_dump(mode="json"),
    }
    return parsed, call_meta


def _score(predictions: dict[str, Any]) -> dict[str, Any]:
    rows = []
    tp = fp = tn = fn = 0
    group_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0, "predicted_current": 0})
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
        bucket = group_counts[case["group"]]
        bucket["total"] += 1
        bucket["correct"] += int(correct)
        bucket["predicted_current"] += int(predicted_current)
        rows.append({
            **case,
            "predicted_claim_types": predicted_types,
            "confidence": pred.get("confidence", 0.0),
            "predicted_current_project": predicted_current,
            "binary_correct": correct,
        })
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / len(CASES)
    non_current_total = tn + fp
    current_total = tp + fn
    return {
        "summary": {
            "case_count": len(CASES),
            "current_positive_count": current_total,
            "non_current_count": non_current_total,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "accuracy": round(accuracy, 4),
            "current_project_precision": round(precision, 4),
            "current_project_recall": round(recall, 4),
            "non_current_false_positive_rate": round(fp / non_current_total, 4) if non_current_total else 0.0,
        },
        "groups": dict(group_counts),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["local_3b", "deepseek"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    settings = get_settings(reload=True)
    if settings.default_model_name == "fake_llm":
        settings = replace(settings, default_model_name=settings.local_qwen_1_5b_model_name)
    if args.profile == "deepseek" and not str(settings.deepseek_api_key or "").strip():
        raise SystemExit("DEEPSEEK_API_KEY is not configured in this process")

    model_name = _resolve_model_name(args.profile, settings)
    run_id = f"claim_classifier_ab_round1_{args.profile}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    gateway = ModelGatewayFactory().build(settings)
    predictions: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    try:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in CASES:
            grouped[case["section_title"]].append(case)
        for section_title, rows in grouped.items():
            parsed, meta = _classify_group(gateway, model_name, section_title, rows, run_id)
            predictions.update(parsed)
            calls.append(meta)
    finally:
        gateway.close()

    result = {
        "experiment": "claim_classifier_ab_round1",
        "profile": args.profile,
        "requested_model_name": model_name,
        "production_prompt_reused": True,
        "temperature": 0.0,
        "do_sample": False,
        "primary_question": "Does the production semantic classifier distinguish settled current-project assertions from deferred/negated/general wording?",
        "scoring": _score(predictions),
        "calls": calls,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "profile": args.profile,
        "output": str(output),
        **result["scoring"]["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
