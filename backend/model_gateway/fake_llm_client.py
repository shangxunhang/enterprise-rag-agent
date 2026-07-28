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
        scenario = str(os.getenv("FAKE_LLM_SCENARIO", "default")).strip().lower()
        force_unbound = self._force_unbound_output(request, scenario)

        if purpose == "workflow_routing":
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
