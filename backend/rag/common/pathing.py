"""解析和校验本地 RAG 资源路径。"""

from __future__ import annotations

from pathlib import Path


def resolve_path(
    path: str | Path,
    project_root: str | Path | None = None,
) -> str:
    """把绝对或项目相对路径转换为绝对字符串路径。

    Args:
        path: 待解析路径。
        project_root: 相对路径的可选基准目录；省略时使用当前工作目录。

    Returns:
        绝对路径的字符串表示。

    绝对路径保持原样，不额外调用 ``resolve``；相对路径会与基准目录拼接并解析
    ``..``、``.`` 等路径段。
    """

    # 第一步：统一转换为 Path，保留调用方传入的绝对路径语义。
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)

    # 第二步：选择显式项目根目录或当前工作目录作为相对路径基准。
    root = Path(project_root) if project_root else Path.cwd()

    # 第三步：拼接并解析为规范化绝对路径。
    return str((root / candidate).resolve())


def require_path(path: str | Path, name: str) -> None:
    """要求路径在文件系统中存在。

    Args:
        path: 待检查的文件或目录路径。
        name: 错误消息中使用的资源名称。

    Raises:
        FileNotFoundError: 路径不存在时抛出。

    本函数只检查存在性，不限制资源必须是文件还是目录；具体类型应由调用方按业务
    契约继续验证。
    """

    # 统一通过 Path.exists 检查，并在异常中同时保留逻辑名称和实际路径。
    if not Path(path).exists():
        raise FileNotFoundError(f"{name} not found: {path}")
