"""用于冒烟、恢复和门禁测试的确定性模型客户端。

它不模拟自然语言质量，而是按 ``call_purpose`` 与环境场景稳定地产生特定输出，专门
验证路由、补充检索、引用修复、Self-RAG 和文档硬门禁等控制路径。
"""

from __future__ import annotations

import json
import os
import re

from contracts.base_client import BaseLLMClient
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


class FakeLLMClient(BaseLLMClient):
    """根据测试场景生成可预测响应，并返回近似 Token 用量。"""
    model_name = "fake_llm"

    @staticmethod
    def _force_unbound_output(request: ModelRequestSchema, scenario: str) -> bool:
        """Return whether this call must omit all citation markers.

        ``force_corrective_retrieval`` keeps the first generation attempt
        ungrounded, including local citation repair/regeneration, then allows
        attempt 2 to succeed after corrective retrieval.

        ``force_business_gate_failure`` keeps the target section ungrounded for
        every attempt so the document hard gate fails deterministically.
        """

        target_section = str(
            os.getenv("FAKE_LLM_TARGET_SECTION", "安全设计")
        ).strip()
        section_title = str(request.extra.get("section_title") or "")
        if section_title != target_section:
            return False
        if scenario == "force_business_gate_failure":
            return True
        if scenario == "force_corrective_retrieval":
            return "_attempt_2" not in str(request.model_call_id)
        return False

    @staticmethod
    def _quote_from_prompt(prompt: str) -> str:
        """从 Prompt 中提取一条真实 ``quote_text``，让测试输出满足同一证据契约。"""
        match = re.search(r'"quote_text"\s*:\s*("(?:\\.|[^"\\])*")', prompt)
        if not match:
            return ""
        try:
            return str(json.loads(match.group(1))).strip()
        except Exception:
            return ""

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """按调用目的和场景选择确定性输出分支。

        路由和语义门禁返回固定 JSON；Self-RAG 场景可让首次检查要求补检索；引用相关
        场景可故意生成无标记章节或复用 Prompt 中真实证据。这样测试的是生产控制逻辑，
        而不是依赖随机模型行为碰巧触发某个分支。
        """
        purpose = request.extra.get("call_purpose")
        response_contract = request.extra.get("response_contract")
        scenario = str(os.getenv("FAKE_LLM_SCENARIO", "default")).strip().lower()
        force_unbound = self._force_unbound_output(request, scenario)

        if response_contract == "canonical_claim_extraction_v1":
            units = None
            for message in request.messages:
                text = message.get("content", "")
                position = text.find("{")
                if position < 0:
                    continue
                try:
                    candidate, _end = json.JSONDecoder().raw_decode(text[position:])
                except (ValueError, TypeError):
                    continue
                if isinstance(candidate, dict) and "coverage_units" in candidate:
                    units = candidate["coverage_units"]
                    break
            if units is None:
                raise ValueError("fake Claim extractor requires exact coverage units")
            # Generic smoke has no semantic oracle: unknown is not no-claim/PASS.
            content = json.dumps({"units": [
                {"unit_id": item["unit_id"], "accounting_state": "INDETERMINATE", "claims": []}
                for item in units
            ]}, ensure_ascii=False)
        elif response_contract == "canonical_claim_verifier_v2":
            # Default fake has no validity oracle: even SUPPORTED is not VERIFIED.
            support_relation = None
            for message in request.messages:
                try:
                    payload = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and "shared_support" in payload:
                    support_relation = payload["shared_support"]["relation"]
                    break
            if support_relation is None:
                raise ValueError("fake Verifier requires committed shared support input")
            content = json.dumps({
                "verdict": "CONFLICTED" if support_relation == "CONTRADICTED" else "UNSUPPORTED",
                "rationale": "Deterministic contract fixture; Claim validity is not independently proven.",
                "positive_basis_refs": [],
                "issues": [{"issue_code": "FAKE_VALIDITY_UNCONFIRMED", "severity": "MAJOR",
                            "message": "No semantic validity oracle supplied for this fake verification.", "source_refs": []}],
            })
        elif response_contract in {
            "canonical_claim_evidence_support_v1",
            "canonical_claim_evidence_support_v2",
        }:
            # Generic smoke is not a semantic oracle; never manufacture SUPPORTED
            # or an ALIGNED certainty relation.
            payload = {
                "relation": "INDETERMINATE",
                "supporting_refs": [],
                "contradicting_refs": [],
            }
            if response_contract == "canonical_claim_evidence_support_v2":
                payload.update(
                    certainty_relation="INDETERMINATE",
                    certainty_reason_codes=["FAKE_CERTAINTY_ORACLE_UNAVAILABLE"],
                )
            content = json.dumps(payload)
        elif response_contract == "citation_claim_association_v1":
            material = None
            for message in request.messages:
                try:
                    text = message.get("content", "")
                    candidate, _end = json.JSONDecoder().raw_decode(text[text.index("{"):])
                except (ValueError, TypeError):
                    continue
                if isinstance(candidate, dict) and "citation_association_input" in candidate:
                    material = candidate["citation_association_input"]
                    break
            if material is None:
                raise ValueError("fake association requires exact committed citation/Claim inputs")
            # No generic semantic oracle; scenario tests supply determinate associations.
            content = json.dumps({"associations": [{"occurrence_ref": ref,
                "state": "INDETERMINATE", "associated_claim_target_ids": []}
                for ref in material["eligible_occurrence_refs"]]})
        elif response_contract == "requirement_adherence_batch_v1":
            requirements = None
            unit_refs = []
            for message in request.messages:
                text = str(message.get("content", ""))
                if text.startswith("CONTROL_REF=requirement-batch://"):
                    try:
                        payload = json.loads(text.split("\n", 1)[1])
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and "requirements" in payload:
                        requirements = payload["requirements"]
                position = text.find("{")
                if position >= 0:
                    try:
                        payload, _end = json.JSONDecoder().raw_decode(text[position:])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(payload, dict):
                        artifact = payload.get("artifact_units") or (
                            payload.get("requirement_adherence_artifact") or {}
                        ).get("artifact_units") or []
                        if isinstance(artifact, list):
                            unit_refs = [str(item.get("unit_ref") or "") for item in artifact if isinstance(item, dict) and item.get("unit_ref")]
            if requirements is None:
                raise ValueError("fake Requirement Adherence requires exact canonical Requirement batch")
            # Generic smoke has no business-semantic oracle.  It must not turn
            # structurally valid input into a semantic PASS.
            content = json.dumps({"results": [
                {"requirement_id": item["requirement_id"], "observation": "INDETERMINATE",
                 "response_refs": sorted(set(unit_refs)), "reason_codes": ["FAKE_SEMANTIC_ORACLE_UNAVAILABLE"]}
                for item in requirements
            ]}, ensure_ascii=False)
        elif response_contract == "coverage_semantic_presence_batch_v1":
            requirements = None
            for message in request.messages:
                text = str(message.get("content", ""))
                if not text.startswith("CONTROL_REF=coverage-semantic://"):
                    continue
                try:
                    payload = json.loads(text.split("\n", 1)[1])
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and "semantic_requirements" in payload:
                    requirements = payload["semantic_requirements"]
                    break
            if requirements is None:
                raise ValueError("fake Coverage requires exact semantic Coverage targets")
            # Generic smoke has no semantic-presence oracle.  Never manufacture
            # PRESENT or MISSING from lexical coincidences.
            content = json.dumps({"results": [
                {"requirement_id": item["requirement_id"], "observation": "INDETERMINATE",
                 "matched_unit_refs": [], "reason_codes": ["FAKE_SEMANTIC_ORACLE_UNAVAILABLE"]}
                for item in requirements
            ]}, ensure_ascii=False)
        elif response_contract == "artifact_consistency_shared_relation_batch_v1":
            logical_works = None
            assertion_refs = []
            for message in request.messages:
                text = str(message.get("content", ""))
                if text.startswith("CONTROL_REF=consistency-shared-batch://"):
                    try:
                        payload = json.loads(text.split("\n", 1)[1])
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        logical_works = payload.get("logical_works")
                position = text.find("{")
                if position < 0:
                    continue
                try:
                    payload, _end = json.JSONDecoder().raw_decode(text[position:])
                except (ValueError, TypeError):
                    continue
                stack = [payload]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        rows = value.get("assertions")
                        if isinstance(rows, list):
                            assertion_refs.extend(
                                str(item.get("assertion_ref") or "")
                                for item in rows
                                if isinstance(item, dict)
                                and item.get("assertion_ref")
                            )
                        stack.extend(value.values())
                    elif isinstance(value, list):
                        stack.extend(value)
            if not isinstance(logical_works, list) or not logical_works:
                raise ValueError(
                    "fake shared Consistency requires exact logical work controls"
                )
            universe = tuple(sorted(set(assertion_refs)))
            work_results = []
            for work in logical_works:
                required = work.get("required_relation_member_sets") or []
                obligations = work.get("required_logical_obligation_refs") or []
                candidates = [tuple(sorted(set(pair))) for pair in required]
                synthesized = set()
                if obligations and not candidates and len(universe) >= 2:
                    candidate = universe[:2]
                    candidates = [candidate]
                    synthesized.add(candidate)
                rows = []
                for pair in candidates:
                    unresolved_materiality = pair in synthesized
                    rows.append(
                        {
                            "member_assertion_refs": list(pair),
                            "relation_materiality": (
                                "INDETERMINATE"
                                if unresolved_materiality
                                else "MATERIAL_RELATION"
                            ),
                            "relation": (
                                None if unresolved_materiality else "INDETERMINATE"
                            ),
                            "semantic_relation_description": (
                                "Generic fake cannot establish the semantic consistency relation."
                            ),
                            "role_bindings": [],
                            "scope_condition_bindings": [],
                            "reason_codes": ["FAKE_SEMANTIC_ORACLE_UNAVAILABLE"],
                            "ambiguity_codes": (
                                ["FAKE_MATERIALITY_UNRESOLVED"]
                                if unresolved_materiality
                                else []
                            ),
                            "formal_predicate": None,
                            "precision_tolerance": None,
                        }
                    )
                work_results.append(
                    {
                        "logical_work_ref": work["logical_work_ref"],
                        "discovery_complete": True,
                        "results": rows,
                    }
                )
            content = json.dumps(
                {"work_results": work_results}, ensure_ascii=False
            )
        elif response_contract == "artifact_consistency_relation_batch_v1":
            required_pairs = None
            logical_obligations = []
            assertion_refs = []
            for message in request.messages:
                text = str(message.get("content", ""))
                if text.startswith("CONTROL_REF=consistency-batch://"):
                    try:
                        payload = json.loads(text.split("\n", 1)[1])
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        required_pairs = payload.get("required_relation_member_sets")
                        logical_obligations = payload.get(
                            "required_logical_obligation_refs"
                        ) or []
                position = text.find("{")
                if position < 0:
                    continue
                try:
                    payload, _end = json.JSONDecoder().raw_decode(text[position:])
                except (ValueError, TypeError):
                    continue
                stack = [payload]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        rows = value.get("assertions")
                        if isinstance(rows, list):
                            assertion_refs.extend(
                                str(item.get("assertion_ref") or "")
                                for item in rows
                                if isinstance(item, dict)
                                and item.get("assertion_ref")
                            )
                        stack.extend(value.values())
                    elif isinstance(value, list):
                        stack.extend(value)
            if required_pairs is None:
                raise ValueError("fake Consistency requires exact required relation member sets")
            candidates = [tuple(sorted(set(pair))) for pair in required_pairs]
            # A global logical discovery obligation with a 2+ member universe
            # cannot be turned into a fake empty/no-material PASS. Keep one
            # bounded candidate explicitly unresolved when no force pair exists.
            if logical_obligations and not candidates:
                universe = tuple(sorted(set(assertion_refs)))
                if len(universe) >= 2:
                    candidates = [universe[:2]]
            content = json.dumps(
                {
                    "discovery_complete": True,
                    "results": [
                        {
                            "member_assertion_refs": list(pair),
                            "relation_materiality": (
                                "INDETERMINATE"
                                if logical_obligations and pair not in {
                                    tuple(sorted(set(item))) for item in required_pairs
                                }
                                else "MATERIAL_RELATION"
                            ),
                            "relation": (
                                None
                                if logical_obligations and pair not in {
                                    tuple(sorted(set(item))) for item in required_pairs
                                }
                                else "INDETERMINATE"
                            ),
                            "semantic_relation_description": "Generic fake cannot establish the semantic consistency relation.",
                            "role_bindings": [],
                            "scope_condition_bindings": [],
                            "reason_codes": ["FAKE_SEMANTIC_ORACLE_UNAVAILABLE"],
                            "ambiguity_codes": ["FAKE_GLOBAL_DISCOVERY_UNRESOLVED"] if logical_obligations else [],
                            "formal_predicate": None,
                            "precision_tolerance": None,
                        }
                        for pair in candidates
                    ],
                },
                ensure_ascii=False,
            )
        elif response_contract == "planner_fact_requirements_v1":
            # Contract-only smoke response. Scenario tests supply semantic choices
            # at this external-model boundary, never by pre-committing a Plan.
            task_contract = None
            for message in request.messages:
                try:
                    candidate = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(candidate, dict) and "section_contracts" in candidate:
                    task_contract = candidate
                    break
            if task_contract is None:
                raise ValueError("fake Planner requires the actual locked section contract")
            content = json.dumps({"sections": [
                {"section_id": section["section_id"], "fact_requirements": []}
                for section in task_contract["section_contracts"]
            ]}, ensure_ascii=False)
        elif response_contract == "criterion_support_evaluator_v1":
            evidence_id = ""
            try:
                contract_input = json.loads(request.prompt.rsplit("\n", 1)[-1])
                selected_evidence = contract_input.get("selected_evidence") or []
                if selected_evidence:
                    evidence_id = str(selected_evidence[0].get("evidence_id") or "").strip()
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence_id = ""
            content = json.dumps(
                {
                    "support_state": "supported" if evidence_id else "indeterminate",
                    "evidence_ids": [evidence_id] if evidence_id else [],
                    "conflicts": [],
                    "reason_codes": ["fake_llm_deterministic_contract"],
                },
                ensure_ascii=False,
            )
        elif purpose == "workflow_routing":
            content = '{"task_type":"scheme_generation","reason":"deterministic smoke-test route"}'
        elif purpose == "semantic_section_gate":
            content = '{"decision":"pass","summary":"deterministic semantic gate pass","issues":[]}'
        elif purpose == "agent_section_self_rag_check" and scenario == "self_rag_retrieve_once":
            if int(request.extra.get("generation_attempt") or 1) <= 1:
                content = json.dumps(
                    {
                        "is_supported": False,
                        "faithfulness_label": "medium",
                        "answer_relevance_label": "medium",
                        "need_rewrite": False,
                        "need_retrieve_more": True,
                        "unsupported_claims": [],
                        "problems": ["test evidence gap"],
                        "score": 0.4,
                    }
                )
            else:
                content = json.dumps(
                    {
                        "is_supported": True,
                        "faithfulness_label": "high",
                        "answer_relevance_label": "high",
                        "need_rewrite": False,
                        "need_retrieve_more": False,
                        "unsupported_claims": [],
                        "problems": [],
                        "score": 0.95,
                    }
                )
        else:
            section_title = request.extra.get("section_title") or "章节"
            document_title = request.extra.get("document_title") or "项目文档"
            citation_ids = request.extra.get("available_citation_ids") or []
            marker = "" if force_unbound else (f"[{citation_ids[0]}]" if citation_ids else "")

            if force_unbound:
                content = (
                    f"### {section_title}\n\n"
                    "本次受控测试故意不输出引用标记，用于验证补充检索或业务硬门禁。"
                    "该段内容保持完整且达到最小章节长度，但不会包含任何Citation标记。"
                    "系统应据此进入Corrective Retrieval或由Document Hard Gate拒绝交付。"
                )
            elif scenario in {"always_grounded", "force_corrective_retrieval"} and marker:
                quote = self._quote_from_prompt(request.prompt)
                grounded_text = "；".join([quote] * 4) if quote else (
                    "当前章节采用知识库证据作为确定性依据，并保持任务、章节、证据和输出之间的追溯关系。"
                    "所有确定性陈述都必须来自当前项目输入或检索证据，缺失信息应明确标记为待补充。"
                )
                content = f"### {section_title}\n\n{grounded_text}{marker}"
            elif purpose == "scheme_grounded_regeneration" and marker:
                # Extract one real quote from the prompt so the smoke client
                # exercises the same Claim-Evidence contract as production.
                quote = self._quote_from_prompt(request.prompt)
                grounded_text = "；".join([quote] * 4) if quote else (
                    "证据不足，需项目方确认。当前章节不得编造未被项目输入或知识库支持的确定性事实，"
                    "并应保留证据、引用和生成结果之间的追溯关系。"
                )
                content = f"### {section_title}\n\n{grounded_text}{marker}"
            elif purpose == "scheme_section_continuation":
                content = (
                    f"续写部分进一步明确{section_title}的实施边界、输入依赖、责任分工和验收要求。"
                    f"所有确定性描述均应能够回溯到当前项目输入或知识库证据{marker}。"
                )
            else:
                content = (
                    f"本章节围绕《{document_title}》的{section_title}展开。系统应以调用方提供的"
                    "项目输入为事实边界，将知识检索结果作为补充依据，并保留任务、章节、证据和"
                    f"生成结果之间的追溯关系{marker}。对于输入中未明确的信息，应标记为待补充或"
                    "需项目方确认，不得自行推导投资金额、客户现状或其他未经确认的业务结论。"
                )

        prompt_tokens = max(1, len(request.prompt) // 4)
        completion_tokens = max(1, len(content) // 4)
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=self.model_name,
            success=True,
            content=content,
            raw_output={
                "is_smoke_test": True,
                "caller_agent": request.caller_agent,
                "call_purpose": purpose,
                "scenario": scenario,
                "forced_unbound": force_unbound,
            },
            created_at=request.created_at,
            token_usage=TokenUsageSchema(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            finish_reason="stop",
            metadata={"client": "FakeLLMClient", "scenario": scenario},
        )
