# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：resolve_path、require_path。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Path resolution for local RAG resources."""

from __future__ import annotations

from pathlib import Path


# 阅读注释（函数）：解析并确定 路径。
def resolve_path(path: str | Path, project_root: str | Path | None = None) -> str:
    """解析并确定 路径。

    参数:
        path: 目标文件或目录路径。
        project_root: 项目 root，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：Path, candidate.is_absolute, str, Path.cwd, resolve。
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    root = Path(project_root) if project_root else Path.cwd()
    return str((root / candidate).resolve())


# 阅读注释（函数）：处理 require 路径 相关逻辑。
def require_path(path: str | Path, name: str) -> None:
    """处理 require 路径 相关逻辑。

    参数:
        path: 目标文件或目录路径。
        name: 名称，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：exists, Path, FileNotFoundError。
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"{name} not found: {path}")
