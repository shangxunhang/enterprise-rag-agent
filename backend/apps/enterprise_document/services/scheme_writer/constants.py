# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Policies shared by SchemeWriter services.

These values are moved unchanged from the former God Agent.  They remain
implementation policy, not domain schemas.
"""


import re

CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_.:\-]+)\]")
MIN_CITATION_SUPPORT_SCORE = 0.25
MIN_CITATION_TOKEN_OVERLAP = 5
MIN_CITATION_LONG_TOKEN_OVERLAP = 1
GENERIC_CITATION_TOKENS = {
    "企业", "系统", "方案", "建设", "项目", "内容", "设计", "相关", "通过",
    "进行", "实现", "提供", "支持", "能力", "业务", "工作", "要求", "技术", "数据",
}

QUALIFIED_FACT_TERMS = (
    "待补充", "需项目方确认", "需要项目方确认", "尚未提供", "当前未提供", "暂无法确认",
    "以项目方最终确认为准", "建议", "可采用", "可选用", "可配置", "可部署", "可考虑",
    "宜采用", "可根据", "原则上",
)
HARDWARE_RESOURCE_PATTERN = re.compile(
    r"(?:GPU|CPU|NPU|服务器|显卡|存储设备|网络设备|数据库节点|计算节点|"
    r"机柜|带宽|内存|硬盘|磁盘)",
    re.IGNORECASE,
)
STAFF_RESOURCE_PATTERN = re.compile(
    r"(?:项目经理|工程师|技术团队|实施团队|运维团队|项目小组|高级技术人员|"
    r"开发人员|测试人员|运维人员)",
    re.IGNORECASE,
)
RESOURCE_COMMITMENT_VERB_PATTERN = re.compile(r"(?:采购|配置|配备|部署|选用|使用|设置|新增|扩容|建设)")
STAFF_COMMITMENT_VERB_PATTERN = re.compile(r"(?:组建|招聘|配备|安排|配置|新增|投入|成立)")
HIGH_RISK_QUANTIFIED_PATTERN = re.compile(
    r"(?:GPU|CPU|NPU|服务器|显卡|内存|硬盘|磁盘|带宽|节点|机柜|"
    r"员工|用户|人员|工程师|项目经理|团队|部门|并发|QPS|准确率|召回率|"
    r"响应时间|预算|金额|成本|工期|周期)",
    re.IGNORECASE,
)
NUMERIC_OR_MODEL_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?%?|[一二三四五六七八九十百千万两]+(?:名|人|台|套|卡|个|节点)|"
    r"[A-Za-z][A-Za-z0-9_.+\-/]{1,})"
)
