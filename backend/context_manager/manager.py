# =============================================================================
# 中文阅读说明：上下文管理模块，用于组织证据、历史状态和 Token 预算。
# 主要定义：ContextBudgetExceededError、LLMContextManager。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Deterministic bounded context manager."""

from __future__ import annotations

import hashlib
from typing import Iterable, List

from schemas.context import (
    ContextBudgetSchema,
    ContextBuildRequestSchema,
    ContextDecisionSchema,
    ContextItemSchema,
    LLMContextPackageSchema,
)

from .token_estimator import DeterministicTokenEstimator


# 阅读注释（类）：封装 上下文 预算 exceeded 错误，集中封装相关状态、依赖和行为。
class ContextBudgetExceededError(RuntimeError):
    """Raised when required blocks alone cannot fit the configured budget."""


# 阅读注释（类）：封装 llmcontext 管理器，集中封装相关状态、依赖和行为。
class LLMContextManager:
    """Select, truncate and render one model-call context package.

    The manager is deliberately pure: it does not retrieve data, call an LLM,
    or mutate workflow state. The same request produces the same package.
    """

    # 阅读注释（函数）：初始化 LLMContextManager，保存运行所需的依赖、配置或状态。
    def __init__(self, estimator: DeterministicTokenEstimator | None = None) -> None:
        """初始化 LLMContextManager，保存运行所需的依赖、配置或状态。

        参数:
            estimator: estimator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：DeterministicTokenEstimator。
        """
        self.estimator = estimator or DeterministicTokenEstimator()

    # 阅读注释（函数）：构建 passthrough。
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
        """Wrap an existing auxiliary prompt without changing its behavior.

        Step 14 v1 fully decomposes normal section generation. Existing repair
        and recovery prompts are represented by this compatibility policy so
        every model call is traceable through the same package contract while
        their proven behavior remains unchanged.
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
            warnings.append("compatibility_passthrough_exceeds_estimated_token_budget")
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
                "budget_enforced": False,
            },
        )

    # 阅读注释（函数）：构建 LLMContextManager。
    def build(self, request: ContextBuildRequestSchema) -> LLMContextPackageSchema:
        """构建 LLMContextManager。

        参数:
            request: 当前请求对象。

        返回:
            LLMContextPackageSchema

        阅读提示:
            主要直接调用：max, int, sorted, strip, str, len, self.estimator.estimate, decisions.append。
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
            key=lambda item: (
                0 if item.required else 1,
                -int(item.priority),
                item.source_type,
                item.item_id,
            ),
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

    # 阅读注释（函数）：处理 fit rendered projection 相关逻辑。
    def _fit_rendered_projection(
        self,
        selected: List[ContextItemSchema],
        decisions: List[ContextDecisionSchema],
        *,
        max_chars: int,
        max_tokens: int,
    ) -> tuple[List[ContextItemSchema], List[ContextDecisionSchema], str, int]:
        """处理 fit rendered projection 相关逻辑。

        参数:
            selected: selected，具体约束请结合类型标注和调用方确认。
            decisions: decisions，具体约束请结合类型标注和调用方确认。
            max_chars: max chars，具体约束请结合类型标注和调用方确认。
            max_tokens: max tokens，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[List[ContextItemSchema], List[ContextDecisionSchema], str, int]

        阅读提示:
            主要直接调用：list, max, len, range, self._render, self.estimator.estimate, decision_by_id.values, next。
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

    # 阅读注释（函数）：处理 fit 数据项 content 相关逻辑。
    def _fit_item_content(
        self,
        item: ContextItemSchema,
        *,
        max_chars: int,
        max_tokens: int,
    ) -> tuple[str, dict, str]:
        """Fit one item while preserving the semantics of structured blocks.

        Ordinary prose uses deterministic sentence-boundary prefix fitting. Items
        such as ``citation_catalog`` may opt into ``line_blocks`` compaction so a
        required catalog remains syntactically usable instead of being cut in the
        middle of one citation entry.
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

    # 阅读注释（函数）：处理 fit line blocks 相关逻辑。
    def _fit_line_blocks(
        self,
        text: str,
        *,
        max_chars: int,
        max_tokens: int,
        min_blocks: int = 0,
    ) -> tuple[str, int, int]:
        """处理 fit line blocks 相关逻辑。

        参数:
            text: 待处理文本。
            max_chars: max chars，具体约束请结合类型标注和调用方确认。
            max_tokens: max tokens，具体约束请结合类型标注和调用方确认。
            min_blocks: min blocks，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[str, int, int]

        阅读提示:
            主要直接调用：len, text.splitlines, line.strip, splitlines, str, join, self.estimator.estimate, selected.append。
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

    # 阅读注释（函数）：处理 fit prefix 相关逻辑。
    def _fit_prefix(self, text: str, *, max_chars: int, max_tokens: int) -> str:
        """处理 fit prefix 相关逻辑。

        参数:
            text: 待处理文本。
            max_chars: max chars，具体约束请结合类型标注和调用方确认。
            max_tokens: max tokens，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：min, len, rstrip, self.estimator.estimate, best.rfind, max。
        """
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

    # 阅读注释（函数）：处理 heading 相关逻辑。
    @staticmethod
    def _heading(item: ContextItemSchema) -> str:
        """处理 heading 相关逻辑。

        参数:
            item: 数据项，具体约束请结合类型标注和调用方确认。

        返回:
            str
        """
        return f"## {item.title} [{item.source_type}:{item.item_id}]"

    # 阅读注释（函数）：渲染 LLMContextManager。
    @classmethod
    def _render(cls, items: Iterable[ContextItemSchema]) -> str:
        """渲染 LLMContextManager。

        参数:
            items: 待处理的数据项集合。

        返回:
            str

        阅读提示:
            主要直接调用：cls._heading, item.content.strip, join。
        """
        blocks = [f"{cls._heading(item)}\n{item.content.strip()}" for item in items]
        return "\n\n".join(blocks)

    # 阅读注释（函数）：处理 decision 相关逻辑。
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
        """处理 decision 相关逻辑。

        参数:
            item: 数据项，具体约束请结合类型标注和调用方确认。
            action: action，具体约束请结合类型标注和调用方确认。
            reason: reason，具体约束请结合类型标注和调用方确认。
            chars_before: chars before，具体约束请结合类型标注和调用方确认。
            chars_after: chars after，具体约束请结合类型标注和调用方确认。
            tokens_before: tokens before，具体约束请结合类型标注和调用方确认。
            tokens_after: tokens after，具体约束请结合类型标注和调用方确认。

        返回:
            ContextDecisionSchema

        阅读提示:
            主要直接调用：ContextDecisionSchema。
        """
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
