"""计算简单工具调用型 Agent 的离线评测指标。"""

from typing import Any, Dict, List


def keyword_hit(text: str, keywords: List[str]) -> bool:
    """判断回答是否包含全部预期关键词。

    Args:
        text: Agent 最终回答文本。
        keywords: 样本要求出现的关键词集合。

    Returns:
        关键词为空时返回 ``True``；否则仅当每个关键词都以子串形式出现在
        ``text`` 中时返回 ``True``。

    当前实现刻意采用确定性的字面匹配，适合作为低成本回归门禁。它不代表语义
    正确性，也不能替代语义相似度、人工评审或 LLM-as-Judge。
    """

    # 没有关键词约束时，该维度不应让样本失败。
    if not keywords:
        return True

    # 使用 all 保持“必须全部命中”的严格样本契约。
    return all(keyword in text for keyword in keywords)


def compute_metrics(eval_records: List[Dict[str, Any]]) -> Dict[str, float | int]:
    """从逐样本记录计算工具和端到端成功比例。

    Args:
        eval_records: ``AgentEvaluator.evaluate_one`` 生成的记录列表。每条记录
            必须包含 ``tool_correct``、``tool_success``、``answer_hit`` 和
            ``end_to_end_success`` 四个布尔字段。

    Returns:
        样本总数以及四项宏观比例。空集合返回全零，避免除零并保持报告结构稳定。
    """

    # 第一步：使用真实记录数作为所有比例的统一分母。
    total = len(eval_records)

    # 第二步：空评测集返回结构完整的零值结果，而不是抛出除零异常。
    if total == 0:
        return {
            "total": 0,
            "tool_accuracy": 0.0,
            "tool_success_rate": 0.0,
            "answer_hit_rate": 0.0,
            "end_to_end_success_rate": 0.0,
        }

    # 第三步：分别统计各维度通过的样本数，避免将不同失败原因混为一类。
    tool_correct_count = sum(1 for record in eval_records if record["tool_correct"])
    tool_success_count = sum(1 for record in eval_records if record["tool_success"])
    answer_hit_count = sum(1 for record in eval_records if record["answer_hit"])
    end_to_end_success_count = sum(
        1 for record in eval_records if record["end_to_end_success"]
    )

    # 第四步：统一转换为 [0, 1] 比例，便于不同批次和样本规模之间比较。
    return {
        "total": total,
        "tool_accuracy": tool_correct_count / total,
        "tool_success_rate": tool_success_count / total,
        "answer_hit_rate": answer_hit_count / total,
        "end_to_end_success_rate": end_to_end_success_count / total,
    }
