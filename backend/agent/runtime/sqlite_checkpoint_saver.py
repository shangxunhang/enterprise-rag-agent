"""为已退役的自定义 SQLite 检查点导入路径提供兼容适配。

新生产代码应通过 ``official_checkpoint_saver`` 显式创建并持有检查点资源。本模块
只保留旧的 ``SQLiteCheckpointSaver`` 函数名，帮助迁移期间的外部调用方继续运行，
同时发出弃用警告；它不再实现任何检查点表结构或序列化协议。
"""

from __future__ import annotations

from pathlib import Path
import warnings

from langgraph.checkpoint.sqlite import SqliteSaver

from agent.runtime.official_checkpoint_saver import (
    create_sqlite_checkpoint_saver,
)


def SQLiteCheckpointSaver(path: str | Path) -> SqliteSaver:
    """通过旧入口返回官方 LangGraph SQLite saver。

    Args:
        path: SQLite 检查点数据库路径。

    Returns:
        由官方 ``langgraph-checkpoint-sqlite`` 实现的同步 saver。

    调用时会发出 ``DeprecationWarning``。兼容工厂只返回 saver，无法让装配层显式
    管理连接关闭，因此生产代码必须迁移到 ``create_sqlite_checkpoint_resource``。
    """

    # 第一步：把弃用警告定位到实际调用者，便于从日志或测试中找到旧导入点。
    warnings.warn(
        "agent.runtime.sqlite_checkpoint_saver.SQLiteCheckpointSaver is "
        "deprecated; production code must own "
        "create_sqlite_checkpoint_resource()",
        DeprecationWarning,
        stacklevel=2,
    )

    # 第二步：委托官方 saver 工厂；本兼容层不接触数据库协议和序列化细节。
    return create_sqlite_checkpoint_saver(path)


# 限制星号导入只暴露旧兼容入口，避免把内部依赖误当成本模块公共 API。
__all__ = ["SQLiteCheckpointSaver"]
