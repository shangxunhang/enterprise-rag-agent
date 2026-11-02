"""为单次模型调用构造确定、可审计且不超预算的上下文包。

阅读主线：
1. ``build`` 按必需性和优先级选择候选上下文；
2. ``_fit_item_content`` 决定普通文本或结构化行块如何压缩；
3. ``_fit_rendered_projection`` 把标题等渲染开销重新计入预算；
4. 最终输出带哈希、预算快照和每个候选块的取舍记录。

本模块只做纯计算：不检索、不调用模型，也不修改工作流状态。同一请求应产生
同一上下文包，便于复现模型输入和定位预算问题。
"""

from __future__ import annotations

import hashlib
from typing import Callable, Iterable, List

from schemas.context import (
    ContextBudgetSchema,
    ContextBuildRequestSchema,
    ContextDecisionSchema,
    ContextItemSchema,
    LLMContextPackageSchema,
)

from core.token_estimator import DeterministicTokenEstimator


class ContextBudgetExceededError(RuntimeError):
    """必需上下文无法在字符或估算 Token 预算内完整表达时抛出。"""


class LLMContextManager:
    """选择、压缩并渲染一次模型调用所需的上下文。

    ``ContextItemSchema`` 是候选事实块，``ContextDecisionSchema`` 记录每个块被保留、
    截断或丢弃的原因；``LLMContextPackageSchema`` 是最终交给提示词层的确定性投影。
    """

    def __init__(self, estimator: DeterministicTokenEstimator | None = None) -> None:
        """注入稳定的 Token 规划估算器；未提供时使用项目默认实现。"""
        self.estimator = estimator or DeterministicTokenEstimator()

    def build_passthrough(
        self,
        *,
        task_id: str,
        run_id: str,
        call_purpose: str,
        content: str,
        section_id: str | None = None,
        section_title: str | None = None,
        max_context_chars: int = 6000,
        max_input_tokens: int = 8192,
        reserved_output_tokens: int = 1024,
        lineage: dict | None = None,
    ) -> LLMContextPackageSchema:
        """把既有辅助提示词包装成统一上下文包，但不改变其文本。

        修复、恢复等旧调用尚未拆成多个候选块，因此采用兼容直通策略。字符超限只记录
        警告，估算 Token 超过模型可用容量则立即失败，避免提交必然无法执行的请求。
        """

        normalized = str(content or "")
        estimated = self.estimator.estimate(normalized)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        configured_chars = max(256, int(max_context_chars))
        usable_tokens = max(
            1, int(max_input_tokens) - int(reserved_output_tokens)
        )
        warnings: list[str] = []
        if len(normalized) > configured_chars:
            warnings.append("compatibility_passthrough_exceeds_char_budget")
        if estimated > usable_tokens:
            raise ContextBudgetExceededError(
                "auxiliary passthrough exceeds selected-model token capacity: "
                f"estimated_input_tokens={estimated} usable_tokens={usable_tokens}"
            )
        item = ContextItemSchema(
            item_id="operation_prompt",
            source_type="operation",
            title="辅助操作上下文",
            content=normalized,
            priority=100,
            required=True,
            truncate_allowed=False,
            metadata={"compatibility_passthrough": True},
        )
        decision = self._decision(
            item,
            action="selected",
            reason="compatibility_passthrough_preserve_behavior",
            chars_before=len(normalized),
            chars_after=len(normalized),
            tokens_before=estimated,
            tokens_after=estimated,
        )
        return LLMContextPackageSchema(
            package_id=f"ctx_{run_id}_{digest[:16]}",
            task_id=task_id,
            run_id=run_id,
            call_purpose=call_purpose,
            section_id=section_id,
            section_title=section_title,
            selected_items=[item],
            decisions=[decision],
            rendered_context=normalized,
            context_sha256=digest,
            budget=ContextBudgetSchema(
                max_context_chars=configured_chars,
                max_input_tokens=int(max_input_tokens),
                reserved_output_tokens=int(reserved_output_tokens),
                safety_margin_tokens=0,
                used_context_chars=len(normalized),
                estimated_input_tokens=estimated,
                remaining_context_chars=max(0, configured_chars - len(normalized)),
                remaining_input_tokens=max(0, usable_tokens - estimated),
            ),
            lineage=dict(lineage or {}),
            warnings=warnings,
            metadata={
                "policy_id": "compatibility_passthrough_context_policy_v1",
                "candidate_item_count": 1,
                "selected_item_count": 1,
                "decision_count": 1,
                "token_estimator": "deterministic_mixed_text_v1",
                "budget_enforced": True,
            },
        )

    def build(
        self, request: ContextBuildRequestSchema, *,
        ordering_key: Callable[[ContextItemSchema], tuple] | None = None,
    ) -> LLMContextPackageSchema:
        """按声明预算构造一次可复现的模型上下文包。

        处理顺序：
        1. 计算扣除输出预留量和安全余量后的可用输入 Token；
        2. 必需块优先，再按优先级、来源类型和 ``item_id`` 稳定排序；
        3. 能完整放入则保留，不能放入则按块策略截断或丢弃；
        4. 渲染标题后再次校验真实投影，必要时从末尾低优先级块继续收缩；
        5. 返回内容哈希、预算使用量以及完整取舍记录。

        必需且不可截断的内容无法容纳时抛出 ``ContextBudgetExceededError``，而不是静默
        删除关键约束。
        """
        max_chars = max(256, int(request.max_context_chars))
        usable_tokens = max(
            1,
            int(request.max_input_tokens)
            - int(request.reserved_output_tokens)
            - int(request.safety_margin_tokens),
        )

        ordered = sorted(
            request.items,
            key=ordering_key or (lambda item: (
                0 if item.required else 1,
                -int(item.priority),
                item.source_type,
                item.item_id,
            )),
        )

        selected: List[ContextItemSchema] = []
        decisions: List[ContextDecisionSchema] = []
        used_chars = 0
        used_tokens = 0

        for item in ordered:
            content = str(item.content or "").strip()
            before_chars = len(content)
            before_tokens = self.estimator.estimate(content)
            if not content:
                decisions.append(
                    self._decision(
                        item,
                        action="dropped",
                        reason="empty_content",
                        chars_before=0,
                        chars_after=0,
                        tokens_before=0,
                        tokens_after=0,
                    )
                )
                continue

            remaining_chars = max_chars - used_chars
            remaining_tokens = usable_tokens - used_tokens
            if remaining_chars <= 0 or remaining_tokens <= 0:
                if item.required:
                    raise ContextBudgetExceededError(
                        f"required context item cannot fit: {item.item_id}"
                    )
                decisions.append(
                    self._decision(
                        item,
                        action="dropped",
                        reason="budget_exhausted",
                        chars_before=before_chars,
                        chars_after=0,
                        tokens_before=before_tokens,
                        tokens_after=0,
                    )
                )
                continue

            if before_chars <= remaining_chars and before_tokens <= remaining_tokens:
                selected_item = item.model_copy(update={"content": content})
                selected.append(selected_item)
                used_chars += before_chars
                used_tokens += before_tokens
                decisions.append(
                    self._decision(
                        item,
                        action="selected",
                        reason="within_budget",
                        chars_before=before_chars,
                        chars_after=before_chars,
                        tokens_before=before_tokens,
                        tokens_after=before_tokens,
                    )
                )
                continue

            if not item.truncate_allowed:
                if item.required:
                    raise ContextBudgetExceededError(
                        f"required context item cannot be truncated: {item.item_id}"
                    )
                decisions.append(
                    self._decision(
                        item,
                        action="dropped",
                        reason="item_not_truncatable",
                        chars_before=before_chars,
                        chars_after=0,
                        tokens_before=before_tokens,
                        tokens_after=0,
                    )
                )
                continue

            truncated, compaction_metadata, compaction_reason = self._fit_item_content(
                item,
                max_chars=remaining_chars,
                max_tokens=remaining_tokens,
            )
            after_chars = len(truncated)
            after_tokens = self.estimator.estimate(truncated)
            if truncated:
                selected_item = item.model_copy(
                    update={
                        "content": truncated,
                        "metadata": {
                            **dict(item.metadata or {}),
                            "context_truncated": True,
                            **compaction_metadata,
                        },
                    }
                )
                selected.append(selected_item)
                used_chars += after_chars
                used_tokens += after_tokens
                decisions.append(
                    self._decision(
                        item,
                        action="truncated",
                        reason=compaction_reason,
                        chars_before=before_chars,
                        chars_after=after_chars,
                        tokens_before=before_tokens,
                        tokens_after=after_tokens,
                    )
                )
                continue

            if item.required:
                raise ContextBudgetExceededError(
                    f"required context item cannot fit: {item.item_id}"
                )
            decisions.append(
                self._decision(
                    item,
                    action="dropped",
                    reason="insufficient_remaining_budget",
                    chars_before=before_chars,
                    chars_after=0,
                    tokens_before=before_tokens,
                    tokens_after=0,
                )
            )

        rendered = self._render(selected)
        rendered_tokens = self.estimator.estimate(rendered)
        # Section headings added by rendering count against the real package.
        # If they push the projection over budget, deterministically reduce the
        # final optional item first, then the final item if all are required.
        selected, decisions, rendered, rendered_tokens = self._fit_rendered_projection(
            selected,
            decisions,
            max_chars=max_chars,
            max_tokens=usable_tokens,
        )
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        budget = ContextBudgetSchema(
            max_context_chars=max_chars,
            max_input_tokens=int(request.max_input_tokens),
            reserved_output_tokens=int(request.reserved_output_tokens),
            safety_margin_tokens=int(request.safety_margin_tokens),
            used_context_chars=len(rendered),
            estimated_input_tokens=rendered_tokens,
            remaining_context_chars=max(0, max_chars - len(rendered)),
            remaining_input_tokens=max(0, usable_tokens - rendered_tokens),
        )
        package_id = f"ctx_{request.run_id}_{digest[:16]}"
        warnings = [
            f"{decision.item_id}:{decision.action}:{decision.reason}"
            for decision in decisions
            if decision.action != "selected"
        ]
        return LLMContextPackageSchema(
            package_id=package_id,
            task_id=request.task_id,
            run_id=request.run_id,
            call_purpose=request.call_purpose,
            section_id=request.section_id,
            section_title=request.section_title,
            selected_items=selected,
            decisions=decisions,
            rendered_context=rendered,
            context_sha256=digest,
            budget=budget,
            lineage=dict(request.lineage or {}),
            warnings=warnings,
            metadata={
                **dict(request.metadata or {}),
                "candidate_item_count": len(request.items),
                "selected_item_count": len(selected),
                "decision_count": len(decisions),
                "token_estimator": "deterministic_mixed_text_v1",
                "budget_enforced": True,
            },
        )

    def _fit_rendered_projection(
        self,
        selected: List[ContextItemSchema],
        decisions: List[ContextDecisionSchema],
        *,
        max_chars: int,
        max_tokens: int,
    ) -> tuple[List[ContextItemSchema], List[ContextDecisionSchema], str, int]:
        """把标题和块间空行的渲染开销纳入预算，并迭代收缩最终投影。

        初次选择阶段只累计块正文；真正渲染时还会增加 Markdown 标题和分隔符。若因此
        超限，本方法优先处理列表尾部的非必需块：能截断则缩短，不能截断则丢弃。
        只有全部候选均为必需块时才处理最后一个必需块，仍无法容纳便明确失败。
        """
        current = list(selected)
        decision_by_id = {item.item_id: item for item in decisions}
        max_iterations = max(16, len(current) * 6)

        for _ in range(max_iterations):
            rendered = self._render(current)
            rendered_tokens = self.estimator.estimate(rendered)
            if len(rendered) <= max_chars and rendered_tokens <= max_tokens:
                return current, list(decision_by_id.values()), rendered, rendered_tokens
            if not current:
                break

            index = next(
                (
                    idx
                    for idx in range(len(current) - 1, -1, -1)
                    if not current[idx].required
                ),
                len(current) - 1,
            )
            item = current[index]
            old_decision = decision_by_id[item.item_id]
            base_items = current[:index] + current[index + 1 :]
            base_rendered = self._render(base_items)
            prefix = ("\n\n" if base_rendered else "") + self._heading(item) + "\n"
            allowed_chars = max(0, max_chars - len(base_rendered) - len(prefix))
            allowed_tokens = max(
                0,
                max_tokens - self.estimator.estimate(base_rendered + prefix),
            )

            if item.truncate_allowed:
                shortened, compaction_metadata, compaction_reason = self._fit_item_content(
                    item,
                    max_chars=min(allowed_chars, max(0, len(item.content) - 1)),
                    max_tokens=allowed_tokens,
                )
            else:
                shortened = ""
                compaction_metadata = {}
                compaction_reason = "render_overhead_budget"

            if shortened and len(shortened) < len(item.content):
                current[index] = item.model_copy(
                    update={
                        "content": shortened,
                        "metadata": {
                            **dict(item.metadata or {}),
                            "context_truncated": True,
                            **compaction_metadata,
                        },
                    }
                )
                decision_by_id[item.item_id] = old_decision.model_copy(
                    update={
                        "action": "truncated",
                        "reason": (
                            "render_overhead_" + compaction_reason
                            if not compaction_reason.startswith("render_overhead_")
                            else compaction_reason
                        ),
                        "chars_after": len(shortened),
                        "estimated_tokens_after": self.estimator.estimate(shortened),
                    }
                )
                continue

            if item.required:
                reason = (
                    "required rendered context cannot be truncated"
                    if not item.truncate_allowed
                    else "required rendered context cannot fit"
                )
                raise ContextBudgetExceededError(f"{reason}: {item.item_id}")

            current.pop(index)
            decision_by_id[item.item_id] = old_decision.model_copy(
                update={
                    "action": "dropped",
                    "reason": (
                        "render_overhead_item_not_truncatable"
                        if not item.truncate_allowed
                        else "render_overhead_budget"
                    ),
                    "chars_after": 0,
                    "estimated_tokens_after": 0,
                }
            )

        rendered = self._render(current)
        rendered_tokens = self.estimator.estimate(rendered)
        if len(rendered) > max_chars or rendered_tokens > max_tokens:
            raise ContextBudgetExceededError("rendered context cannot fit configured budget")
        return current, list(decision_by_id.values()), rendered, rendered_tokens

    def _fit_item_content(
        self,
        item: ContextItemSchema,
        *,
        max_chars: int,
        max_tokens: int,
    ) -> tuple[str, dict, str]:
        """在剩余预算内压缩单个上下文块，同时保留其结构语义。

        普通段落使用稳定的句界前缀截断；引用目录等结构化内容可声明 ``line_blocks``，
        以完整行作为最小保留单位，避免把一条 citation 记录从中间切断。
        """

        strategy = str((item.metadata or {}).get("compaction_strategy") or "").strip()
        if strategy == "line_blocks":
            compacted, retained, original = self._fit_line_blocks(
                item.content,
                max_chars=max_chars,
                max_tokens=max_tokens,
                min_blocks=int((item.metadata or {}).get("min_blocks") or 0),
            )
            return (
                compacted,
                {
                    "context_compaction_strategy": "line_blocks",
                    "context_original_blocks": original,
                    "context_retained_blocks": retained,
                },
                "structured_line_compaction",
            )
        return (
            self._fit_prefix(
                item.content,
                max_chars=max_chars,
                max_tokens=max_tokens,
            ),
            {"context_compaction_strategy": "sentence_prefix"},
            "fit_remaining_budget",
        )

    def _fit_line_blocks(
        self,
        text: str,
        *,
        max_chars: int,
        max_tokens: int,
        min_blocks: int = 0,
    ) -> tuple[str, int, int]:
        """按完整非空行从前向后装入预算，返回文本、保留行数和原始行数。

        ``min_blocks`` 用于必需目录：若完整首行仍放不下，则对首行执行句界前缀拟合，
        尽量留下至少一条可识别记录，而不是返回半条随机文本。
        """
        if max_chars <= 0 or max_tokens <= 0:
            return "", 0, len([line for line in text.splitlines() if line.strip()])

        blocks = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        if not blocks:
            return "", 0, 0

        selected: list[str] = []
        for block in blocks:
            candidate = "\n".join(selected + [block])
            if len(candidate) <= max_chars and self.estimator.estimate(candidate) <= max_tokens:
                selected.append(block)
                continue
            break

        required_blocks = max(0, int(min_blocks))
        if len(selected) < required_blocks:
            # Preserve at least one meaningful catalog row when possible. Each row
            # is already compact, but a very small residual budget may still need
            # deterministic sentence-boundary fitting.
            first = self._fit_prefix(
                blocks[0],
                max_chars=max_chars,
                max_tokens=max_tokens,
            )
            if first:
                selected = [first]

        return "\n".join(selected), len(selected), len(blocks)

    def _fit_prefix(self, text: str, *, max_chars: int, max_tokens: int) -> str:
        """二分查找同时满足字符和 Token 预算的最长前缀，再回退到最近句界。"""
        if max_chars <= 0 or max_tokens <= 0:
            return ""
        upper = min(len(text), max_chars)
        low = 0
        best = ""
        while low <= upper:
            mid = (low + upper) // 2
            candidate = text[:mid].rstrip()
            if self.estimator.estimate(candidate) <= max_tokens:
                best = candidate
                low = mid + 1
            else:
                upper = mid - 1
        if not best:
            return ""
        cut_points = [
            best.rfind(marker)
            for marker in ("。", "；", "！", "？", "\n", ".", ";")
        ]
        cut = max(cut_points)
        if cut >= max(24, len(best) // 2):
            best = best[: cut + 1].rstrip()
        return best

    @staticmethod
    def _heading(item: ContextItemSchema) -> str:
        """生成带来源类型和稳定标识的 Markdown 块标题。"""
        return f"## {item.title} [{item.source_type}:{item.item_id}]"

    @classmethod
    def _render(cls, items: Iterable[ContextItemSchema]) -> str:
        """按当前稳定顺序把上下文块渲染为最终模型输入文本。"""
        blocks = [f"{cls._heading(item)}\n{item.content.strip()}" for item in items]
        return "\n\n".join(blocks)

    @staticmethod
    def _decision(
        item: ContextItemSchema,
        *,
        action: str,
        reason: str,
        chars_before: int,
        chars_after: int,
        tokens_before: int,
        tokens_after: int,
    ) -> ContextDecisionSchema:
        """把一次选取决策标准化为可审计记录，不在此处修改候选块。"""
        return ContextDecisionSchema(
            item_id=item.item_id,
            source_type=item.source_type,
            action=action,
            reason=reason,
            priority=item.priority,
            required=item.required,
            chars_before=chars_before,
            chars_after=chars_after,
            estimated_tokens_before=tokens_before,
            estimated_tokens_after=tokens_after,
        )
