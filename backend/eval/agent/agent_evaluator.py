# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：AgentEvaluator。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from typing import Dict, List

from eval.agent.metrics import compute_metrics, keyword_hit


# 阅读注释（类）：封装 Agent evaluator，集中封装相关状态、依赖和行为。
class AgentEvaluator:
    """
    Agent 评估器。

    当前评估：
    1. 工具选择是否正确
    2. 工具是否执行成功
    3. 最终回答是否命中关键词
    4. 端到端是否成功
    """

    # 阅读注释（函数）：初始化 AgentEvaluator，保存运行所需的依赖、配置或状态。
    def __init__(self, agent) -> None:
        """初始化 AgentEvaluator，保存运行所需的依赖、配置或状态。

        参数:
            agent: Agent，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.agent = agent

    # 阅读注释（函数）：评估 AgentEvaluator。
    def evaluate(self, eval_cases: List[Dict]) -> Dict:
        """评估 AgentEvaluator。

        参数:
            eval_cases: 评测 cases，具体约束请结合类型标注和调用方确认。

        返回:
            Dict

        阅读提示:
            主要直接调用：self.evaluate_one, records.append, compute_metrics。
        """
        records = []

        for case in eval_cases:
            record = self.evaluate_one(case)
            records.append(record)

        metrics = compute_metrics(records)

        return {
            "metrics": metrics,
            "records": records,
        }

    # 阅读注释（函数）：评估 one。
    def evaluate_one(self, case: Dict) -> Dict:
        """评估 one。

        参数:
            case: case，具体约束请结合类型标注和调用方确认。

        返回:
            Dict

        阅读提示:
            主要直接调用：case.get, self.agent.run, keyword_hit, result.tool_call.model_dump, result.tool_result.model_dump。
        """
        query = case["query"]
        expected_tool = case["expected_tool"]
        expected_keywords = case.get("expected_keywords", [])

        result = self.agent.run(query)

        actual_tool = result.tool_call.tool_name if result.tool_call else None
        tool_correct = actual_tool == expected_tool

        tool_success = (
            result.tool_result.success
            if result.tool_result is not None
            else False
        )

        answer_hit = keyword_hit(
            text=result.final_answer,
            keywords=expected_keywords,
        )

        end_to_end_success = (
            tool_correct
            and tool_success
            and answer_hit
        )

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