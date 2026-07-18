from typing import Dict, List


def keyword_hit(text: str, keywords: List[str]) -> bool:
    """
    判断文本是否命中所有关键词。

    当前先用最简单的 contains。
    后续可以升级成：
    - 语义相似度
    - LLM-as-Judge
    - 正则匹配
    """
    if not keywords:
        return True

    return all(keyword in text for keyword in keywords)


def compute_metrics(eval_records: List[Dict]) -> Dict[str, float]:
    """
    计算 Agent Eval 指标。
    """
    total = len(eval_records)

    if total == 0:
        return {
            "total": 0,
            "tool_accuracy": 0.0,
            "tool_success_rate": 0.0,
            "answer_hit_rate": 0.0,
            "end_to_end_success_rate": 0.0,
        }

    tool_correct_count = sum(1 for r in eval_records if r["tool_correct"])
    tool_success_count = sum(1 for r in eval_records if r["tool_success"])
    answer_hit_count = sum(1 for r in eval_records if r["answer_hit"])
    end_to_end_success_count = sum(1 for r in eval_records if r["end_to_end_success"])

    return {
        "total": total,
        "tool_accuracy": tool_correct_count / total,
        "tool_success_rate": tool_success_count / total,
        "answer_hit_rate": answer_hit_count / total,
        "end_to_end_success_rate": end_to_end_success_count / total,
    }