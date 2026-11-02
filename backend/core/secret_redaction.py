"""在运行时错误和诊断文本进入持久化前清除常见凭据。

该边界只处理文本，不尝试扫描任意对象图。调用方应显式传入已知密钥值，本模块再
补充识别 Authorization 头和常见 token 外形。脱敏是纵深防御，不能替代日志字段
白名单和上游避免记录敏感请求。
"""

from __future__ import annotations

import re
from collections.abc import Iterable


# 匹配完整 Authorization 键值，包括可选 Bearer 前缀；替换时连头名称一起删除，
# 防止持久化诊断中保留可复用或可拼接的部分认证信息。
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?[^\s,;\"']+"
)

# 匹配不嵌在字母数字中的常见 sk-/api-/key-/token- 形式长凭据。
# 该规则是补充启发式，不保证识别所有供应商格式。
_COMMON_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|api|key|token)-[A-Za-z0-9_.-]{8,}"
)


def redact_sensitive_text(
    value: object,
    *,
    secrets: Iterable[str] = (),
) -> str:
    """从诊断值中移除已知密钥和常见凭据形态。

    Args:
        value: 将被转换为字符串并写入日志或错误对象的诊断值。
        secrets: 调用上下文明确知道的真实密钥集合。

    Returns:
        已脱敏的文本；所有命中的具体密钥替换为 ``<redacted>``。
    """

    # 第一步：统一把空值和其他对象转换为可处理文本。
    text = str(value or "")

    # 第二步：去重并按长度降序替换已知密钥，避免短密钥先替换破坏长密钥匹配。
    known_secrets = sorted(
        {str(item) for item in secrets if str(item)},
        key=len,
        reverse=True,
    )
    for secret in known_secrets:
        text = text.replace(secret, "<redacted>")

    # 第三步：删除完整 Authorization 头，不在持久化诊断中保留头名或凭据片段。
    text = _AUTHORIZATION_PATTERN.sub("<redacted-authorization>", text)

    # 第四步：清除未显式传入但符合常见 token 外形的残余凭据。
    return _COMMON_TOKEN_PATTERN.sub("<redacted>", text)
