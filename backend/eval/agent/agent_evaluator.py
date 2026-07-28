"""对简单工具调用型 Agent 执行离线逐样本评测。

本模块适用于“输入查询 → Agent 选择工具 → 工具返回结果 → Agent 给出最终回答”
这一类轻量链路。它只负责执行样本并生成结构化记录，不负责持久化报告，也不对
复杂工作流中的中间状态、引用绑定或硬门禁进行判断。
"""

from typing import Any, Dict, List

from eval.agent.metrics import compute_metrics, keyword_hit


class AgentEvaluator:
    """执行工具选择、工具执行和回答命中率评测。

    Attributes:
        agent: 被测 Agent。对象必须提供 ``run(query)`` 方法，返回值至少包含
            ``tool_call``、``tool_result`` 和 ``final_answer`` 属性。

    每个样本会同时检查四个维度：工具选择是否正确、工具是否成功执行、最终回答
    是否覆盖预期关键词，以及三个条件是否共同满足端到端成功。
    """

    def __init__(self, agent: Any) -> None:
        """保存被测 Agent 实例。

        Args:
            agent: 提供同步 ``run`` 接口的被测对象。构造阶段不触发任何评测调用。
        """

        self.agent = agent

    def evaluate(self, eval_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        """按输入顺序执行全部样本并汇总总体指标。

        Args:
            eval_cases: 样本列表。每个样本必须提供 ``query`` 和
                ``expected_tool``，可选提供 ``expected_keywords``。

        Returns:
            包含 ``records`` 和 ``metrics`` 的字典。``records`` 保留逐样本细节，
            ``metrics`` 是基于全部记录计算的聚合比例。
        """

        # 第一步：保持样本原始顺序执行，便于失败记录与输入数据逐一对应。
        records: List[Dict[str, Any]] = []
        for case in eval_cases:
            records.append(self.evaluate_one(case))

        # 第二步：基于逐样本布尔结果计算宏观比例，不在执行循环中累计隐藏状态。
        metrics = compute_metrics(records)

        # 第三步：同时返回明细和汇总，使调用方可进一步诊断失败样本。
        return {
            "metrics": metrics,
            "records": records,
        }

    def evaluate_one(self, case: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个样本并生成可审计评测记录。

        Args:
            case: 包含查询、期望工具和可选关键词的样本字典。

        Returns:
            包含期望值、实际值、各项布尔判定和原始工具协议快照的记录。

        Raises:
            KeyError: 样本缺少 ``query`` 或 ``expected_tool`` 时抛出。该异常表示
                评测数据本身不符合契约，不应被误记为 Agent 失败。
        """

        # 第一步：读取不可缺失的黄金标签，并为可选关键词提供空列表默认值。
        query = case["query"]
        expected_tool = case["expected_tool"]
        expected_keywords = case.get("expected_keywords", [])

        # 第二步：以真实查询调用被测 Agent；这里不捕获异常，让基础设施故障显式暴露。
        result = self.agent.run(query)

        # 第三步：从结构化工具调用中读取实际工具。没有工具调用视为选择失败。
        actual_tool = result.tool_call.tool_name if result.tool_call else None
        tool_correct = actual_tool == expected_tool

        # 第四步：只有存在工具结果且其 success 为真，才判定工具执行成功。
        tool_success = (
            result.tool_result.success
            if result.tool_result is not None
            else False
        )

        # 第五步：检查最终回答是否覆盖样本要求的全部关键词。
        answer_hit = keyword_hit(
            text=result.final_answer,
            keywords=expected_keywords,
        )

        # 第六步：端到端成功采用严格 conjunction，任一环节失败都会否决结果。
        end_to_end_success = tool_correct and tool_success and answer_hit

        # 第七步：序列化工具协议对象，保留后续错误分析所需的输入输出细节。
        return {
            "query": query,
            "expected_tool": expected_tool,
            "actual_tool": actual_tool,
            "tool_correct": tool_correct,
            "tool_success": tool_success,
            "expected_keywords": expected_keywords,
            "answer_hit": answer_hit,
            "end_to_end_success": end_to_end_success,
            "final_answer": result.final_answer,
            "tool_call": result.tool_call.model_dump() if result.tool_call else None,
            "tool_result": result.tool_result.model_dump() if result.tool_result else None,
        }
