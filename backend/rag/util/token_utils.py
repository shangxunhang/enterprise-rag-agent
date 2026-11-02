"""为 Chunker 提供 Hugging Face 优先、正则回退的 Token 计数。

可用本地 Tokenizer 时使用 ``encode(add_special_tokens=False)``，否则中文按单字、
英文/数字按连续词、标点按单字符计数。回退实现只保证稳定近似，不等于目标模型的
真实 Token 用量；模型调用阶段仍应记录实际 usage。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional


class TokenCounter:
    """封装可选 Hugging Face Tokenizer 和确定性正则回退。

    Attributes:
        tokenizer_name: 本地模型路径或 Hugging Face 模型标识。
        local_files_only: 是否禁止 ``from_pretrained`` 联网下载。
        tokenizer: 成功加载的 Tokenizer；失败或未配置时为 ``None``。
    """

    # 中文字符、英文/数字连续词和非空白标点分别视为一个回退 Token。
    _FALLBACK_PATTERN = re.compile(
        r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]",
        re.UNICODE,
    )

    def __init__(
        self,
        tokenizer_name: Optional[str] = None,
        local_files_only: bool = True,
    ) -> None:
        """保存配置并尽力加载 Tokenizer。

        Args:
            tokenizer_name: 可选本地路径或模型标识。
            local_files_only: 是否只允许读取本地缓存/文件。

        加载失败不会使构造失败，而是退化为正则计数；调用方可通过 ``backend`` 属性
        判断本次实际计数实现。
        """

        self.tokenizer_name = tokenizer_name
        self.local_files_only = local_files_only
        self.tokenizer = self._load_tokenizer(
            tokenizer_name,
            local_files_only,
        )

    @staticmethod
    def _load_tokenizer(
        tokenizer_name: Optional[str],
        local_files_only: bool,
    ):
        """尽力加载 Hugging Face AutoTokenizer。

        Returns:
            Tokenizer 实例；未配置名称、依赖缺失、路径不存在或加载异常时返回
            ``None``。

        本方法刻意吞掉加载异常以保证离线 Chunker 仍可运行，但生产索引 Manifest
        应记录 ``backend``，避免不同环境混用两种计数口径。
        """

        # 第一步：没有模型名称时直接选择回退实现。
        if not tokenizer_name:
            return None

        # 第二步：Transformers 是可选依赖，导入失败不阻断项目。
        try:
            from transformers import AutoTokenizer
        except Exception:
            return None

        try:
            # 第三步：看起来像本地绝对路径但不存在时直接回退，避免被解释为 repo_id。
            name_text = str(tokenizer_name)
            maybe_path = Path(name_text)
            if (
                (":" in name_text or name_text.startswith("/"))
                and not maybe_path.exists()
            ):
                return None

            # 第四步：按配置加载 Tokenizer；trust_remote_code 保留历史模型兼容行为。
            return AutoTokenizer.from_pretrained(
                tokenizer_name,
                local_files_only=local_files_only,
                trust_remote_code=True,
            )
        except Exception:
            return None

    def tokenize(self, text: str) -> List:
        """把文本转换为 Token ID 或正则 Token 列表。

        Args:
            text: 待切分文本。

        Returns:
            Hugging Face 后端返回整数 ID 列表；回退后端返回字符串 Token 列表。
            空文本返回空列表。
        """

        if not text:
            return []

        # 优先使用真实 Tokenizer，并排除模型自动添加的 BOS/EOS 等特殊 Token。
        if self.tokenizer is not None:
            return self.tokenizer.encode(
                text,
                add_special_tokens=False,
            )

        # 依赖或模型不可用时使用稳定正则近似。
        return self._FALLBACK_PATTERN.findall(text)

    def count(self, text: str) -> int:
        """返回 ``tokenize`` 结果长度。"""

        return len(self.tokenize(text))

    @property
    def backend(self) -> str:
        """返回当前实际计数后端名称。"""

        if self.tokenizer is not None:
            return "huggingface"
        return "fallback_regex"


@lru_cache(maxsize=8)
def get_token_counter(
    tokenizer_name: Optional[str] = None,
    local_files_only: bool = True,
) -> TokenCounter:
    """按 Tokenizer 名称和离线标记缓存计数器实例。

    最多缓存八种配置，避免多个 Chunker 重复加载同一本地 Tokenizer。缓存键由函数
    参数决定，因此参数应使用稳定字符串而不是可变对象。
    """

    return TokenCounter(
        tokenizer_name=tokenizer_name,
        local_files_only=local_files_only,
    )


def get_default_token_counter() -> TokenCounter:
    """从旧版 RAGConfig 解析默认 Tokenizer 并返回缓存计数器。

    优先使用 ``CHUNK_TOKENIZER_MODEL_NAME``，其次 ``EMBEDDING_MODEL_NAME``；配置
    导入失败或两者都为空时使用正则回退。
    """

    tokenizer_name = None
    local_files_only = True

    # 第一步：延迟导入配置，避免工具模块与配置模块形成导入时循环。
    try:
        from rag.configs import RAGConfig

        tokenizer_name = getattr(
            RAGConfig,
            "CHUNK_TOKENIZER_MODEL_NAME",
            None,
        )
        if tokenizer_name is None:
            tokenizer_name = getattr(
                RAGConfig,
                "EMBEDDING_MODEL_NAME",
                None,
            )
        local_files_only = getattr(
            RAGConfig,
            "CHUNK_TOKENIZER_LOCAL_FILES_ONLY",
            True,
        )
    except Exception:
        tokenizer_name = None

    # 第二步：统一名称为字符串，并复用缓存实例。
    return get_token_counter(
        str(tokenizer_name) if tokenizer_name else None,
        local_files_only,
    )
