from typing import Dict, List

from eval.agent.metrics import compute_metrics, keyword_hit


class AgentEvaluator:
    """
    Agent 评估器。

    当前评估：
    1. 工具选择是否正确
    2. 工具是否执行成功
    3. 最终回答是否命中关键词
    4. 端到端是否成功
    """

    def __init__(self, agent) -> None:
        self.agent = agent

    def evaluate(self, eval_cases: List[Dict]) -> Dict:
        records = []

        for case in eval_cases:
            record = self.evaluate_one(case)
            records.append(record)

        metrics = compute_metrics(records)

        return {
            "metrics": metrics,
            "records": records,
        }

    def evaluate_one(self, case: Dict) -> Dict:
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